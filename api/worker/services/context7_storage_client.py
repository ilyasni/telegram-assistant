from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional, Set

import httpx
import structlog

logger = structlog.get_logger(__name__)


class Context7StorageClient:
    """
    [C7-ID: DIGEST-CONTEXT-STORAGE-001]
    Клиент Context7 Storage для сохранения окон групповых дайджестов.
    Использует REST API (`/namespaces/{namespace}` + `/documents`).
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: Optional[str] = None,
        namespace_prefix: str = "group-digest",
        timeout: float = 5.0,
    ) -> None:
        if not base_url:
            raise ValueError("Context7 Storage base_url must be provided")

        self._base_url = base_url.rstrip("/")
        self._namespace_prefix = namespace_prefix.rstrip(":")
        self._timeout = timeout
        self._headers = {
            "Content-Type": "application/json",
            "User-Agent": "telegram-assistant/worker",
        }
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"

        self._known_namespaces: Set[str] = set()

    def _namespace(self, tenant_id: str) -> str:
        tenant_norm = tenant_id.strip().lower()
        return f"{self._namespace_prefix}:{tenant_norm}"

    def ensure_namespace(self, tenant_id: str) -> None:
        namespace = self._namespace(tenant_id)
        if namespace in self._known_namespaces:
            return

        url = f"{self._base_url}/namespaces/{namespace}"
        try:
            response = httpx.put(url, headers=self._headers, timeout=self._timeout)
            response.raise_for_status()
            self._known_namespaces.add(namespace)
            logger.debug("context7_storage.namespace_ready", namespace=namespace, status=response.status_code)
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "context7_storage.namespace_failed",
                namespace=namespace,
                status=exc.response.status_code,
                body=exc.response.text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("context7_storage.namespace_error", namespace=namespace, error=str(exc))

    def upsert_window_context(
        self,
        *,
        tenant_id: str,
        group_id: str,
        window_id: str,
        trace_id: str,
        stats: Dict[str, Any],
        sanitized_messages: Iterable[Dict[str, Any]],
        ranking: Iterable[Dict[str, Any]],
        duplicates: Dict[str, Any],
        historical_links: Dict[str, Any],
    ) -> None:
        namespace = self._namespace(tenant_id)
        self.ensure_namespace(tenant_id)

        document_id = f"{group_id}:{window_id}"
        body = {
            "document_id": document_id,
            "metadata": {
                "group_id": group_id,
                "window_id": window_id,
                "tenant_id": tenant_id,
                "trace_id": trace_id,
                "stats": stats,
            },
            "payload": {
                "messages": list(sanitized_messages),
                "ranking": list(ranking),
                "duplicates": duplicates,
                "historical_links": historical_links,
            },
        }

        url = f"{self._base_url}/namespaces/{namespace}/documents"
        try:
            response = httpx.post(url, json=body, headers=self._headers, timeout=self._timeout)
            response.raise_for_status()
            logger.debug(
                "context7_storage.context_persisted",
                namespace=namespace,
                document_id=document_id,
                status=response.status_code,
                stored=len(body["payload"]["messages"]),
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "context7_storage.context_failed",
                namespace=namespace,
                document_id=document_id,
                status=exc.response.status_code,
                body=exc.response.text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "context7_storage.context_error",
                namespace=namespace,
                document_id=document_id,
                error=str(exc),
            )

    def healthcheck(self) -> bool:
        """Простая проверка доступности API."""
        url = f"{self._base_url}/health"
        try:
            response = httpx.get(url, headers=self._headers, timeout=self._timeout)
            response.raise_for_status()
            return True
        except Exception:  # noqa: BLE001
            return False

    def fetch_recent_context(
        self,
        *,
        tenant_id: str,
        group_id: str,
        limit_windows: int = 3,
        limit_messages: int = 150,
    ) -> Dict[str, Any]:
        """
        Получает последние окна контекста из Context7 Storage.

        Возвращает агрегированные сообщения и ранкинг, пригодные для повторного использования.
        В случае ошибок — пустой результат.
        """
        namespace = self._namespace(tenant_id)
        url = f"{self._base_url}/namespaces/{namespace}/documents/search"
        body = {
            "filter": {"group_id": group_id},
            "limit": max(1, limit_windows),
            "order": [{"field": "metadata.window_id", "direction": "desc"}],
        }

        try:
            response = httpx.post(url, json=body, headers=self._headers, timeout=self._timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "context7_storage.search_failed",
                namespace=namespace,
                group_id=group_id,
                error=str(exc),
            )
            return {"messages": [], "ranking": [], "duplicates": {}}

        documents = payload.get("documents") or []
        aggregated_messages: list[Dict[str, Any]] = []
        aggregated_ranking: list[Dict[str, Any]] = []
        aggregated_duplicates: Dict[str, Any] = {}

        for doc in documents:
            doc_payload = doc.get("payload") or {}
            messages = doc_payload.get("messages") or []
            ranking = doc_payload.get("ranking") or []
            duplicates = doc_payload.get("duplicates") or {}

            for message in messages:
                if len(aggregated_messages) >= limit_messages:
                    break
                aggregated_messages.append(message)
            if len(aggregated_messages) >= limit_messages:
                break

            aggregated_ranking.extend(ranking)
            aggregated_duplicates.update(duplicates)

        if aggregated_ranking:
            aggregated_ranking = aggregated_ranking[:limit_messages]

        logger.debug(
            "context7_storage.search_success",
            namespace=namespace,
            group_id=group_id,
            windows=len(documents),
            messages=len(aggregated_messages),
        )
        return {
            "messages": aggregated_messages,
            "ranking": aggregated_ranking,
            "duplicates": aggregated_duplicates,
        }


__all__ = ["Context7StorageClient"]

