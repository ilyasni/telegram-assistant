"""
State-store для мультиагентного пайплайна групповых дайджестов.

Context7 best practices:
- Redis используется как оперативное хранилище стадий (idempotency, fast lookup).
- Postgres (JSONB) хранит персистентные артефакты и метаданные.
- Ключи: digest:{tenant}:{group}:{window}:{stage}, отдельный lock-ключ.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Protocol

import redis
from redis.exceptions import RedisError
import structlog
from sqlalchemy.orm import Session

from worker.shared.database import GroupDigestStageArtifact, SessionLocal

logger = structlog.get_logger(__name__)


DEFAULT_SCHEMA_VERSION = os.getenv("DIGEST_SCHEMA_VERSION", "v1")
DEFAULT_TTL_SECONDS = int(os.getenv("DIGEST_STATE_TTL_SECONDS", str(24 * 3600)))
DEFAULT_LOCK_TTL_SECONDS = int(os.getenv("DIGEST_LOCK_TTL_SECONDS", str(15 * 60)))
METADATA_STAGE = "__meta__"


def _parse_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if value in (None, "", "None"):
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        logger.warning("digest_state.invalid_uuid", value=value)
        return None


@dataclass(frozen=True)
class DigestLock:
    """Информация о захваченной блокировке окна."""

    key: str
    token: str
    ttl_seconds: int


class DigestStateStore:
    """
    Основное хранилище состоятний стадий дайджеста.

    Redis отвечает за быструю идемпотентность, Postgres — за персистентность.
    В случае недоступности Redis применяется in-memory fallback.
    """

    def __init__(self, redis_url: Optional[str] = None, schema_version: str = DEFAULT_SCHEMA_VERSION):
        redis_url = redis_url or os.getenv("REDIS_URL", "redis://redis:6379/0")
        self._schema_version = schema_version
        self._redis_enabled = True
        self._memory_store: Dict[str, Dict[str, Any]] = {}

        try:
            self._redis = redis.Redis.from_url(redis_url, decode_responses=True, socket_timeout=2.0)
            self._redis.ping()
            logger.info("digest_state.redis_ready", redis_url=redis_url)
        except (RedisError, OSError) as exc:  # pragma: no cover - редкий случай
            logger.warning("digest_state.redis_unavailable", error=str(exc), redis_url=redis_url)
            self._redis_enabled = False
            self._redis = None

    @property
    def schema_version(self) -> str:
        return self._schema_version

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _stage_key(tenant_id: str, group_id: str, window_id: str, stage: str) -> str:
        return f"digest:{tenant_id}:{group_id}:{window_id}:{stage}"

    @staticmethod
    def _lock_key(tenant_id: str, group_id: str, window_id: str) -> str:
        return f"digest:lock:{tenant_id}:{group_id}:{window_id}"

    # -------------------------------------------------------------------- redis
    def _redis_set(self, key: str, value: Dict[str, Any], ttl: int = DEFAULT_TTL_SECONDS) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        if not self._redis_enabled:
            self._memory_store[key] = value
            return
        try:
            self._redis.set(key, payload, ex=ttl)
        except RedisError as exc:  # pragma: no cover
            logger.warning("digest_state.redis_set_failed", key=key, error=str(exc))
            self._memory_store[key] = value

    def _redis_get(self, key: str) -> Optional[Dict[str, Any]]:
        if self._redis_enabled:
            try:
                data = self._redis.get(key)
                if data:
                    return json.loads(data)
            except (RedisError, json.JSONDecodeError) as exc:  # pragma: no cover
                logger.warning("digest_state.redis_get_failed", key=key, error=str(exc))
        return self._memory_store.get(key)

    def _redis_exists(self, key: str) -> bool:
        if self._redis_enabled:
            try:
                return bool(self._redis.exists(key))
            except RedisError as exc:  # pragma: no cover
                logger.warning("digest_state.redis_exists_failed", key=key, error=str(exc))
        return key in self._memory_store

    # ---------------------------------------------------------------- persistence
    def _persist_artifact(
        self,
        tenant_id: str,
        group_id: str,
        window_id: str,
        stage: str,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> None:
        session: Session = SessionLocal()
        try:
            artifact = (
                session.query(GroupDigestStageArtifact)
                .filter(
                    GroupDigestStageArtifact.tenant_id == _parse_uuid(tenant_id),
                    GroupDigestStageArtifact.group_id == _parse_uuid(group_id),
                    GroupDigestStageArtifact.window_id == _parse_uuid(window_id),
                    GroupDigestStageArtifact.stage == stage,
                )
                .one_or_none()
            )
            now = datetime.utcnow()
            if artifact:
                artifact.schema_version = self._schema_version
                artifact.prompt_id = metadata.get("prompt_id")
                artifact.prompt_version = metadata.get("prompt_version")
                artifact.model_id = metadata.get("model_id")
                artifact.payload = payload
                artifact.updated_at = now
            else:
                artifact = GroupDigestStageArtifact(
                    tenant_id=_parse_uuid(tenant_id),
                    group_id=_parse_uuid(group_id),
                    window_id=_parse_uuid(window_id),
                    stage=stage,
                    schema_version=self._schema_version,
                    prompt_id=metadata.get("prompt_id"),
                    prompt_version=metadata.get("prompt_version"),
                    model_id=metadata.get("model_id"),
                    payload=payload,
                    created_at=now,
                    updated_at=now,
                )
                session.add(artifact)
            session.commit()
        except Exception as exc:  # pragma: no cover - персистентность не критична для пайплайна
            session.rollback()
            logger.error(
                "digest_state.persist_failed",
                tenant_id=tenant_id,
                group_id=group_id,
                window_id=window_id,
                stage=stage,
                error=str(exc),
            )
        finally:
            session.close()

    def _load_artifact_from_db(
        self,
        tenant_id: str,
        group_id: str,
        window_id: str,
        stage: str,
    ) -> Optional[Dict[str, Any]]:
        session: Session = SessionLocal()
        try:
            artifact = (
                session.query(GroupDigestStageArtifact)
                .filter(
                    GroupDigestStageArtifact.tenant_id == _parse_uuid(tenant_id),
                    GroupDigestStageArtifact.group_id == _parse_uuid(group_id),
                    GroupDigestStageArtifact.window_id == _parse_uuid(window_id),
                    GroupDigestStageArtifact.stage == stage,
                )
                .one_or_none()
            )
            if not artifact:
                return None
            return {
                "schema_version": artifact.schema_version,
                "metadata": {
                    "prompt_id": artifact.prompt_id,
                    "prompt_version": artifact.prompt_version,
                    "model_id": artifact.model_id,
                    "stored_at": artifact.updated_at.isoformat() if artifact.updated_at else None,
                },
                "payload": artifact.payload,
            }
        except Exception as exc:  # pragma: no cover
            logger.error(
                "digest_state.load_db_failed",
                tenant_id=tenant_id,
                group_id=group_id,
                window_id=window_id,
                stage=stage,
                error=str(exc),
            )
            return None
        finally:
            session.close()

    # -------------------------------------------------------------------- public
    def set_artifact(
        self,
        tenant_id: str,
        group_id: str,
        window_id: str,
        stage: str,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        ttl: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        metadata = metadata or {}
        record = {
            "schema_version": self._schema_version,
            "metadata": metadata,
            "payload": payload,
            "stored_at": datetime.utcnow().isoformat(),
        }
        key = self._stage_key(tenant_id, group_id, window_id, stage)
        self._redis_set(key, record, ttl=ttl)
        self._persist_artifact(tenant_id, group_id, window_id, stage, payload, metadata)

    def get_artifact(self, tenant_id: str, group_id: str, window_id: str, stage: str) -> Optional[Dict[str, Any]]:
        key = self._stage_key(tenant_id, group_id, window_id, stage)
        data = self._redis_get(key)
        if data:
            return data
        return self._load_artifact_from_db(tenant_id, group_id, window_id, stage)

    def get_metadata(self, tenant_id: str, group_id: str, window_id: str) -> Dict[str, Any]:
        record = self.get_artifact(tenant_id, group_id, window_id, METADATA_STAGE)
        if record and isinstance(record.get("payload"), dict):
            return record["payload"]
        return {}

    def set_metadata(
        self,
        tenant_id: str,
        group_id: str,
        window_id: str,
        metadata: Dict[str, Any],
        ttl: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        safe_meta = dict(metadata or {})
        safe_meta.setdefault("updated_at", datetime.utcnow().isoformat())
        meta_record = {
            "schema_version": self._schema_version,
            "metadata": {
                "prompt_id": "STATE_METADATA",
                "prompt_version": "v1",
                "model_id": "system",
            },
            "payload": safe_meta,
            "stored_at": datetime.utcnow().isoformat(),
        }
        key = self._stage_key(tenant_id, group_id, window_id, METADATA_STAGE)
        self._redis_set(key, meta_record, ttl=ttl)
        self._persist_artifact(
            tenant_id,
            group_id,
            window_id,
            METADATA_STAGE,
            safe_meta,
            meta_record["metadata"],
        )

    def has_stage(self, tenant_id: str, group_id: str, window_id: str, stage: str) -> bool:
        key = self._stage_key(tenant_id, group_id, window_id, stage)
        if self._redis_exists(key):
            return True
        session: Session = SessionLocal()
        try:
            exists = (
                session.query(GroupDigestStageArtifact.stage)
                .filter(
                    GroupDigestStageArtifact.tenant_id == _parse_uuid(tenant_id),
                    GroupDigestStageArtifact.group_id == _parse_uuid(group_id),
                    GroupDigestStageArtifact.window_id == _parse_uuid(window_id),
                    GroupDigestStageArtifact.stage == stage,
                )
                .limit(1)
                .scalar()
            )
            return exists is not None
        except Exception as exc:  # pragma: no cover
            logger.error("digest_state.has_stage_failed", stage=stage, error=str(exc))
            return False
        finally:
            session.close()

    def load_state(self, tenant_id: str, group_id: str, window_id: str) -> Dict[str, Dict[str, Any]]:
        """Возвращает словарь {stage -> payload} из Redis/in-memory."""
        pattern = self._stage_key(tenant_id, group_id, window_id, "*")
        records: Dict[str, Dict[str, Any]] = {}
        if self._redis_enabled:
            try:
                cursor = 0
                while True:
                    cursor, keys = self._redis.scan(cursor=cursor, match=pattern, count=50)
                    for key in keys:
                        data = self._redis_get(key)
                        if not data:
                            continue
                        stage = key.split(":")[-1]
                        records[stage] = data
                    if cursor == 0:
                        break
            except RedisError as exc:  # pragma: no cover
                logger.warning("digest_state.scan_failed", pattern=pattern, error=str(exc))
        for key, value in self._memory_store.items():
            if key.startswith(pattern[:-1]):  # pattern без *
                stage = key.split(":")[-1]
                records.setdefault(stage, value)
        return records

    # --------------------------------------------------------------------- locks
    def acquire_lock(
        self,
        tenant_id: str,
        group_id: str,
        window_id: str,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> Optional[DigestLock]:
        token = uuid.uuid4().hex
        key = self._lock_key(tenant_id, group_id, window_id)
        if not self._redis_enabled:
            existing = self._memory_store.get(key)
            if existing and existing.get("expires_at", 0) > time.time():
                return None
            self._memory_store[key] = {"token": token, "expires_at": time.time() + ttl_seconds}
            return DigestLock(key=key, token=token, ttl_seconds=ttl_seconds)
        try:
            if self._redis.set(key, token, nx=True, ex=ttl_seconds):
                return DigestLock(key=key, token=token, ttl_seconds=ttl_seconds)
            return None
        except RedisError as exc:  # pragma: no cover
            logger.error("digest_state.lock_failed", key=key, error=str(exc))
            return None

    def release_lock(self, lock: DigestLock) -> None:
        if not lock:
            return
        if self._redis_enabled:
            try:
                script = """
                if redis.call('get', KEYS[1]) == ARGV[1] then
                    return redis.call('del', KEYS[1])
                else
                    return 0
                end
                """
                self._redis.eval(script, 1, lock.key, lock.token)
            except RedisError as exc:  # pragma: no cover
                logger.warning("digest_state.unlock_failed", key=lock.key, error=str(exc))
        else:
            stored = self._memory_store.get(lock.key)
            if stored and stored.get("token") == lock.token:
                self._memory_store.pop(lock.key, None)

    def renew_lock(self, lock: DigestLock, ttl_seconds: Optional[int] = None) -> bool:
        ttl = ttl_seconds or lock.ttl_seconds
        if self._redis_enabled:
            try:
                return bool(self._redis.expire(lock.key, ttl))
            except RedisError as exc:  # pragma: no cover
                logger.warning("digest_state.lock_renew_failed", key=lock.key, error=str(exc))
                return False
        stored = self._memory_store.get(lock.key)
        if not stored or stored.get("token") != lock.token:
            return False
        stored["expires_at"] = time.time() + ttl
        return True


class SupportsDigestState(Protocol):
    """Интерфейс для handle, используемого в оркестраторе."""

    def acquire_lock(self) -> bool: ...

    def release_lock(self) -> None: ...

    def renew_lock(self) -> bool: ...

    def get_stage(self, stage: str) -> Optional[Dict[str, Any]]: ...

    def set_stage(self, stage: str, payload: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None: ...

    def load_metadata(self) -> Dict[str, Any]: ...

    def update_metadata(self, **kwargs: Any) -> None: ...


class DigestStateHandle(SupportsDigestState):
    """Обёртка вокруг `DigestStateStore` для конкретного окна дайджеста."""

    def __init__(
        self,
        store: DigestStateStore,
        tenant_id: str,
        group_id: str,
        window_id: str,
        digest_version: str,
    ):
        self._store = store
        self._tenant_id = tenant_id or ""
        self._group_id = group_id or ""
        self._window_id = window_id
        self._digest_version = digest_version
        self._lock: Optional[DigestLock] = None
        self._stage_cache: Dict[str, Dict[str, Any]] = {}
        self._metadata_cache: Optional[Dict[str, Any]] = None

    def acquire_lock(self) -> bool:
        if self._lock:
            return True
        lock = self._store.acquire_lock(self._tenant_id, self._group_id, self._window_id)
        if lock:
            self._lock = lock
            return True
        return False

    def release_lock(self) -> None:
        if not self._lock:
            return
        self._store.release_lock(self._lock)
        self._lock = None

    def renew_lock(self) -> bool:
        if not self._lock:
            return False
        return self._store.renew_lock(self._lock)

    def get_stage(self, stage: str) -> Optional[Dict[str, Any]]:
        if stage in self._stage_cache:
            return self._stage_cache[stage]
        record = self._store.get_artifact(self._tenant_id, self._group_id, self._window_id, stage)
        if record:
            self._stage_cache[stage] = record
        return record

    def set_stage(self, stage: str, payload: Dict[str, Any], metadata: Optional[Dict[str, Any]] = None) -> None:
        metadata = dict(metadata or {})
        metadata.setdefault("schema_version", self._store.schema_version)
        metadata.setdefault("digest_version", self._digest_version)
        metadata.setdefault("stage", stage)
        metadata.setdefault("stored_at", datetime.utcnow().isoformat())
        self._store.set_artifact(
            self._tenant_id,
            self._group_id,
            self._window_id,
            stage,
            payload,
            metadata=metadata,
        )
        if self._lock:
            self._store.renew_lock(self._lock)
        self._stage_cache[stage] = {
            "schema_version": self._store.schema_version,
            "metadata": metadata,
            "payload": payload,
            "stored_at": metadata["stored_at"],
        }
        meta = self.load_metadata()
        meta.setdefault("stages", {})[stage] = metadata
        meta.setdefault("digest_version", self._digest_version)
        meta["schema_version"] = self._store.schema_version
        meta["updated_at"] = datetime.utcnow().isoformat()
        self._store.set_metadata(self._tenant_id, self._group_id, self._window_id, meta)
        self._metadata_cache = meta

    def load_metadata(self) -> Dict[str, Any]:
        if self._metadata_cache is None:
            raw_meta = self._store.get_metadata(self._tenant_id, self._group_id, self._window_id) or {}
            self._metadata_cache = dict(raw_meta)
        self._metadata_cache.setdefault("stages", {})
        return self._metadata_cache

    def update_metadata(self, **kwargs: Any) -> None:
        meta = self.load_metadata()
        meta.setdefault("stages", {})
        changed = False
        for key, value in kwargs.items():
            if value is None:
                continue
            meta[key] = value
            changed = True
        if not changed:
            return
        meta.setdefault("digest_version", self._digest_version)
        meta["schema_version"] = self._store.schema_version
        meta["updated_at"] = datetime.utcnow().isoformat()
        self._store.set_metadata(self._tenant_id, self._group_id, self._window_id, meta)
        self._metadata_cache = meta
        if self._lock:
            self._store.renew_lock(self._lock)


class DigestStateStoreFactory:
    """Фабрика, выдающая handle для окна дайджеста."""

    def __init__(self, store: Optional[DigestStateStore] = None):
        self._store = store or DigestStateStore()

    @property
    def store(self) -> DigestStateStore:
        return self._store

    def create(
        self,
        tenant_id: str,
        group_id: str,
        window_id: str,
        digest_version: str,
    ) -> DigestStateHandle:
        return DigestStateHandle(self._store, tenant_id, group_id, window_id, digest_version)


_STATE_STORE: Optional[DigestStateStore] = None


def get_digest_state_store() -> DigestStateStore:
    global _STATE_STORE
    if _STATE_STORE is None:
        _STATE_STORE = DigestStateStore()
    return _STATE_STORE


__all__ = [
    "DigestStateStore",
    "DigestStateStoreFactory",
    "DigestStateHandle",
    "SupportsDigestState",
    "DigestLock",
    "get_digest_state_store",
    "DEFAULT_SCHEMA_VERSION",
]

