"""
Enrichment Repository

Context7 best practice: единый репозиторий для всех видов обогащений
с идемпотентностью через ON CONFLICT(post_id, kind) и структурированным логированием.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional
import structlog
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

enrichment_upsert_total = Counter(
    'post_enrichment_upsert_total',
    'Total enrichment upsert operations',
    ['kind', 'provider', 'status']
)

enrichment_upsert_duration_seconds = Histogram(
    'post_enrichment_upsert_duration_seconds',
    'Enrichment upsert operation duration',
    ['kind', 'provider']
)

enrichment_upsert_errors_total = Counter(
    'post_enrichment_upsert_errors_total',
    'Enrichment upsert errors',
    ['kind', 'provider', 'error_type']
)


class EnrichmentRepository:
    """
    Единый репозиторий для всех видов обогащений.
    
    Context7 best practices:
    - Единая точка записи для всех видов обогащений
    - Идемпотентность через ON CONFLICT(post_id, kind)
    - Структурированное логирование с trace_id
    - Валидация данных перед сохранением
    """
    
    def __init__(self, db_session):
        """
        Инициализация репозитория.
        
        Args:
            db_session: SQLAlchemy AsyncSession или asyncpg.Pool
        """
        self.db_session = db_session
        
        # Context7: Определение типа подключения для правильного использования
        # asyncpg.Pool имеет метод acquire() и не имеет commit()
        # SQLAlchemy AsyncSession имеет метод commit()
        import asyncpg
        from sqlalchemy.ext.asyncio import AsyncSession
        
        self._is_asyncpg = isinstance(db_session, asyncpg.Pool)
        self._is_sqlalchemy = isinstance(db_session, AsyncSession) or (hasattr(db_session, 'commit') and not hasattr(db_session, 'acquire'))
        
        logger.debug(
            "EnrichmentRepository initialized",
            is_asyncpg=self._is_asyncpg,
            is_sqlalchemy=self._is_sqlalchemy,
            type_name=type(db_session).__name__
        )
    
    def compute_params_hash(
        self,
        model: Optional[str] = None,
        version: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Вычисление params_hash для идемпотентности.
        
        Context7: Используется для определения, нужно ли обновлять обогащение
        при изменении параметров модели/версии.
        
        Args:
            model: Название модели (например, 'gigachat-vision')
            version: Версия модели (например, '2025-10')
            inputs: Входные параметры (threshold, lang, etc)
            
        Returns:
            SHA256 hash строки параметров
        """
        params_str = '|'.join([
            str(model or ''),
            str(version or ''),
            json.dumps(inputs or {}, sort_keys=True)
        ])
        return hashlib.sha256(params_str.encode('utf-8')).hexdigest()
    
    async def upsert_enrichment(
        self,
        post_id: str,
        kind: str,
        provider: str,
        data: Dict[str, Any],
        params_hash: Optional[str] = None,
        status: str = 'ok',
        error: Optional[str] = None,
        trace_id: Optional[str] = None
    ) -> None:
        """
        Единый метод для upsert всех видов обогащений.
        
        Context7: Использует ON CONFLICT(post_id, kind) для идемпотентности.
        Модульное сохранение: каждый вид обогащения хранится отдельной записью.
        
        Args:
            post_id: UUID поста
            kind: Тип обогащения ('vision', 'vision_ocr', 'crawl', 'tags', 'classify')
            provider: Провайдер обогащения ('gigachat-vision', 'tesseract', 'crawl4ai')
            data: JSONB данные обогащения
            params_hash: Опциональный hash параметров для версионирования
            status: Статус ('ok', 'partial', 'error')
            error: Текст ошибки (если status='error')
            trace_id: Trace ID для корреляции
        """
        import time
        start_time = time.time()
        
        # Валидация
        valid_kinds = {'vision', 'vision_ocr', 'crawl', 'tags', 'classify', 'general'}
        if kind not in valid_kinds:
            logger.warning(
                "Invalid enrichment kind",
                kind=kind,
                valid_kinds=valid_kinds,
                trace_id=trace_id
            )
            enrichment_upsert_errors_total.labels(
                kind=kind,
                provider=provider,
                error_type='invalid_kind'
            ).inc()
            raise ValueError(f"Invalid kind: {kind}. Must be one of {valid_kinds}")
        
        valid_statuses = {'ok', 'partial', 'error'}
        if status not in valid_statuses:
            status = 'ok'  # Fallback
            logger.warning(
                "Invalid status, using 'ok'",
                status=status,
                trace_id=trace_id
            )
        
        try:
            updated_at = datetime.now(timezone.utc)
            # Context7: Логируем данные перед сериализацией для диагностики OCR и params_hash
            has_ocr = bool(data.get("ocr") and (isinstance(data.get("ocr"), dict) and data["ocr"].get("text")))
            ocr_text_length = len(data.get("ocr", {}).get("text", "")) if isinstance(data.get("ocr"), dict) else 0
            logger.debug(
                "Upserting enrichment to DB",
                post_id=post_id,
                kind=kind,
                provider=provider,
                params_hash=params_hash,
                has_ocr=has_ocr,
                ocr_text_length=ocr_text_length,
                data_keys=list(data.keys()) if isinstance(data, dict) else [],
                trace_id=trace_id
            )
            
            # Context7: Сериализация JSON с сохранением None значений
            # ВАЖНО: json.dumps сохраняет None как null в JSON, что корректно для PostgreSQL JSONB
            data_jsonb = json.dumps(data, ensure_ascii=False, default=str)
            
            # Context7: Проверяем, что OCR сохранился после сериализации
            data_parsed = json.loads(data_jsonb)
            has_ocr_after = bool(data_parsed.get("ocr") and (isinstance(data_parsed.get("ocr"), dict) and data_parsed["ocr"].get("text")))
            if has_ocr != has_ocr_after:
                logger.warning(
                    "OCR data lost during JSON serialization",
                    post_id=post_id,
                    kind=kind,
                    has_ocr_before=has_ocr,
                    has_ocr_after=has_ocr_after,
                    trace_id=trace_id
                )
            
            if self._is_asyncpg:
                # asyncpg.Pool
                async with self.db_session.acquire() as conn:
                    # Context7: Убрана ссылка на несуществующую колонку enriched_at
                    await conn.execute("""
                        INSERT INTO post_enrichment (
                            post_id, kind, provider, params_hash, data, status, error,
                            updated_at
                        ) VALUES (
                            $1, $2, $3, $4, $5::jsonb, $6, $7, $8
                        )
                        ON CONFLICT (post_id, kind) DO UPDATE SET
                            provider = EXCLUDED.provider,
                            params_hash = COALESCE(EXCLUDED.params_hash, post_enrichment.params_hash),
                            data = EXCLUDED.data,
                            status = EXCLUDED.status,
                            error = EXCLUDED.error,
                            updated_at = EXCLUDED.updated_at
                            -- Context7: Legacy поля обновляются только для vision через отдельный UPDATE после INSERT
                            -- Это избегает проблем с ambiguous column references в ON CONFLICT DO UPDATE SET
                            -- Context7: params_hash использует COALESCE чтобы не перезаписывать существующий hash на NULL
                    """,
                        post_id,
                        kind,
                        provider,
                        params_hash,
                        data_jsonb,
                        status,
                        error,
                        updated_at
                    )
                    
                    # Context7: Legacy поля удалены из БД миграцией 20251117_remove_legacy
                    # Все данные хранятся только в data JSONB
                    # Проверяем, что данные сохранились правильно после upsert
                    check_row = await conn.fetchrow("""
                        SELECT data, params_hash 
                        FROM post_enrichment 
                        WHERE post_id = $1 AND kind = $2
                    """, post_id, kind)
                    
                    if check_row:
                        saved_data = check_row['data']
                        saved_params_hash = check_row['params_hash']
                        logger.debug(
                            "Enrichment saved to DB - verification",
                            post_id=post_id,
                            kind=kind,
                            params_hash_saved=bool(saved_params_hash),
                            params_hash_value=saved_params_hash[:16] + "..." if saved_params_hash else None,
                            trace_id=trace_id
                        )
            
            elif self._is_sqlalchemy:
                # SQLAlchemy AsyncSession
                from sqlalchemy import text
                
                # Context7: Используем CAST вместо :: для совместимости с SQLAlchemy
                await self.db_session.execute(
                    # Context7: Убрана ссылка на несуществующую колонку enriched_at
                    text("""
                        INSERT INTO post_enrichment (
                            post_id, kind, provider, params_hash, data, status, error,
                            updated_at
                        ) VALUES (
                            :post_id, :kind, :provider, :params_hash, CAST(:data AS jsonb), :status, :error,
                            :updated_at
                        )
                        ON CONFLICT (post_id, kind) DO UPDATE SET
                            provider = EXCLUDED.provider,
                            params_hash = COALESCE(EXCLUDED.params_hash, post_enrichment.params_hash),
                            data = EXCLUDED.data,
                            status = EXCLUDED.status,
                            error = EXCLUDED.error,
                            updated_at = EXCLUDED.updated_at
                            -- Context7: Legacy поля удалены из БД миграцией 20251117_remove_legacy
                            -- Все данные хранятся только в data JSONB
                    """),
                    {
                        "post_id": post_id,
                        "kind": kind,
                        "provider": provider,
                        "params_hash": params_hash,
                        "data": data_jsonb,
                        "status": status,
                        "error": error,
                        "updated_at": updated_at,
                        "post_id_sub": post_id,  # Отдельные параметры для подзапроса
                        "kind_sub": kind,
                        "updated_at_sub": updated_at
                    }
                )
                await self.db_session.commit()
            else:
                raise ValueError("Unsupported db_session type. Use asyncpg.Pool or SQLAlchemy AsyncSession")
            
            duration = time.time() - start_time
            enrichment_upsert_duration_seconds.labels(
                kind=kind,
                provider=provider
            ).observe(duration)
            
            enrichment_upsert_total.labels(
                kind=kind,
                provider=provider,
                status=status
            ).inc()
            
            logger.debug(
                "Enrichment upserted",
                post_id=post_id,
                kind=kind,
                provider=provider,
                status=status,
                duration_ms=int(duration * 1000),
                trace_id=trace_id
            )
            
        except Exception as e:
            enrichment_upsert_errors_total.labels(
                kind=kind,
                provider=provider,
                error_type=type(e).__name__
            ).inc()
            
            logger.error(
                "Failed to upsert enrichment",
                post_id=post_id,
                kind=kind,
                provider=provider,
                error=str(e),
                trace_id=trace_id
            )
            raise
    
    async def get_enrichment(
        self,
        post_id: str,
        kind: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Получение обогащения по post_id и опционально kind.
        
        Args:
            post_id: UUID поста
            kind: Опциональный тип обогащения (если None - вернёт все)
            
        Returns:
            Dict с данными обогащения или список dict если kind=None
        """
        if self._is_asyncpg:
            async with self.db_session.acquire() as conn:
                if kind:
                    row = await conn.fetchrow("""
                        SELECT post_id, kind, provider, params_hash, data, status, error,
                               created_at, updated_at
                        FROM post_enrichment
                        WHERE post_id = $1 AND kind = $2
                    """, post_id, kind)
                    return dict(row) if row else None
                else:
                    rows = await conn.fetch("""
                        SELECT post_id, kind, provider, params_hash, data, status, error,
                               created_at, updated_at
                        FROM post_enrichment
                        WHERE post_id = $1
                        ORDER BY kind
                    """, post_id)
                    return [dict(row) for row in rows] if rows else None
        
        elif self._is_sqlalchemy:
            from sqlalchemy import text, select
            from sqlalchemy.engine import Result
            
            if kind:
                result: Result = await self.db_session.execute(
                    text("""
                        SELECT post_id, kind, provider, params_hash, data, status, error,
                               created_at, updated_at
                        FROM post_enrichment
                        WHERE post_id = :post_id AND kind = :kind
                    """),
                    {"post_id": post_id, "kind": kind}
                )
                row = result.fetchone()
                return dict(row._mapping) if row else None
            else:
                result: Result = await self.db_session.execute(
                    text("""
                        SELECT post_id, kind, provider, params_hash, data, status, error,
                               created_at, updated_at
                        FROM post_enrichment
                        WHERE post_id = :post_id
                        ORDER BY kind
                    """),
                    {"post_id": post_id}
                )
                rows = result.fetchall()
                return [dict(row._mapping) for row in rows] if rows else None
        
        else:
            raise ValueError("Unsupported db_session type")

