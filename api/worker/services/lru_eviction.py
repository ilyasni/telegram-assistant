"""
LRU Eviction Service для автоматической очистки неиспользуемого контента.
[C7-ID: STORAGE-LRU-001]

Стратегия:
- Приоритет: refs_count=0 (неиспользуемые медиа)
- По last_seen_at (старые файлы)
- По content_type (crawl > vision > media)
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Any, Optional, Tuple
from dataclasses import dataclass

import structlog
from prometheus_client import Counter, Histogram, Gauge

# Для проверки доступности S3 service
try:
    from api.services.s3_storage import S3StorageService
except ImportError:
    S3StorageService = None

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

lru_eviction_operations_total = Counter(
    'lru_eviction_operations_total',
    'LRU eviction operations',
    ['content_type', 'reason']
)

lru_eviction_freed_bytes = Histogram(
    'lru_eviction_freed_bytes',
    'Bytes freed by LRU eviction',
    ['content_type']
)

lru_candidates_scanned = Gauge(
    'lru_candidates_scanned',
    'Number of LRU candidates scanned',
    ['content_type']
)

# ============================================================================
# LRU EVICTION SERVICE
# ============================================================================

@dataclass
class EvictionCandidate:
    """Кандидат на удаление."""
    s3_key: str
    content_type: str  # media | vision | crawl
    size_bytes: int
    last_seen_at: Optional[datetime]
    refs_count: int = 0
    tenant_id: Optional[str] = None
    file_sha256: Optional[str] = None  # Context7: SHA256 для синхронизации с БД


class LRUEvictionService:
    """
    LRU Eviction Service для автоматической очистки неиспользуемого контента.
    
    Strategy:
    1. Приоритет на media с refs_count=0 (неиспользуемые)
    2. По last_seen_at (старые файлы)
    3. По content_type (crawl > vision > media)
    """
    
    def __init__(
        self,
        s3_service,
        db_pool=None,  # asyncpg.Pool для получения refs_count
        target_free_gb: float = 2.0
    ):
        self.s3_service = s3_service
        self.db_pool = db_pool
        self.target_free_gb = target_free_gb
        
        logger.info("LRUEvictionService initialized", target_free_gb=target_free_gb)
    
    async def find_eviction_candidates(
        self,
        target_free_gb: float,
        content_type: Optional[str] = None
    ) -> List[EvictionCandidate]:
        """
        Поиск кандидатов на удаление для освобождения target_free_gb.
        
        Priority:
        1. Media с refs_count=0 (неиспользуемые)
        2. Старые файлы по last_seen_at
        3. Crawl > Vision > Media (по приоритету очистки)
        """
        target_bytes = int(target_free_gb * (1024 ** 3))
        candidates = []
        
        try:
            # Если есть доступ к БД, получаем refs_count из media_objects
            if self.db_pool and content_type in (None, 'media'):
                db_candidates = await self._find_unused_media_from_db()
                candidates.extend(db_candidates)
            
                # Сортируем по приоритету: refs_count=0, затем по last_seen_at
                candidates.sort(
                    key=lambda x: (
                        x.refs_count != 0,  # Сначала refs_count=0
                        x.last_seen_at or datetime.min.replace(tzinfo=timezone.utc)
                    )
                )
            
            # Если недостаточно кандидатов из БД, сканируем S3
            current_freed = sum(c.size_bytes for c in candidates)
            
            if current_freed < target_bytes:
                # Дополняем кандидатами из S3
                s3_candidates = await self._find_old_objects_from_s3(
                    target_bytes - current_freed,
                    content_type
                )
                
                # Добавляем только те, которых нет в candidates
                existing_keys = {c.s3_key for c in candidates}
                candidates.extend(
                    c for c in s3_candidates
                    if c.s3_key not in existing_keys
                )
                
                # Сортируем снова
                candidates.sort(
                    key=lambda x: (
                        self._get_content_type_priority(x.content_type),
                        x.last_seen_at or datetime.min.replace(tzinfo=timezone.utc)
                    )
                )
            
            # Ограничиваем список до target_free_gb
            selected = []
            total_size = 0
            
            for candidate in candidates:
                if total_size >= target_bytes:
                    break
                selected.append(candidate)
                total_size += candidate.size_bytes
            
            lru_candidates_scanned.labels(content_type=content_type or 'all').set(len(candidates))
            
            logger.info(
                "LRU eviction candidates found",
                candidates_count=len(selected),
                target_free_gb=target_free_gb,
                estimated_free_gb=total_size / (1024 ** 3)
            )
            
            return selected
            
        except Exception as e:
            logger.error("Failed to find eviction candidates", error=str(e))
            return []
    
    async def _find_unused_media_from_db(self) -> List[EvictionCandidate]:
        """Поиск неиспользуемых медиа из БД (refs_count=0)."""
        if not self.db_pool:
            return []
        
        try:
            async with self.db_pool.acquire() as conn:
                # Получаем медиа с refs_count=0, отсортированные по last_seen_at
                rows = await conn.fetch("""
                    SELECT 
                        file_sha256,
                        s3_key,
                        size_bytes,
                        last_seen_at,
                        refs_count,
                        mime
                    FROM media_objects
                    WHERE refs_count = 0
                    ORDER BY last_seen_at ASC NULLS FIRST
                    LIMIT 1000
                """)
                
                candidates = []
                for row in rows:
                    # Определяем content_type по mime или s3_key
                    content_type = self._guess_content_type(row.get('mime', ''), row.get('s3_key', ''))
                    
                    candidates.append(EvictionCandidate(
                        s3_key=row['s3_key'],
                        content_type=content_type,
                        size_bytes=row['size_bytes'],
                        last_seen_at=row['last_seen_at'],
                        refs_count=row['refs_count'],
                        file_sha256=row['file_sha256']  # Context7: Сохраняем для синхронизации с БД
                    ))
                
                return candidates
                
        except Exception as e:
            logger.error("Failed to get unused media from DB", error=str(e))
            return []
    
    async def _find_old_objects_from_s3(
        self,
        target_bytes: int,
        content_type: Optional[str] = None
    ) -> List[EvictionCandidate]:
        """Поиск старых объектов в S3 по префиксам."""
        candidates = []
        
        # Приоритет префиксов для очистки
        prefixes = []
        if content_type:
            prefixes.append((content_type, f"{content_type}/"))
        else:
            # По приоритету: crawl > vision > media
            prefixes = [
                ("crawl", "crawl/"),
                ("vision", "vision/"),
                ("media", "media/")
            ]
        
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)  # Старше 30 дней
        
        for type_name, prefix in prefixes:
            try:
                # List objects
                objects = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda p=prefix: list(self.s3_service.s3_client.list_objects_v2(
                        Bucket=self.s3_service.bucket_name,
                        Prefix=p
                    ).get('Contents', []))
                )
                
                for obj in objects:
                    last_modified = obj.get('LastModified')
                    if last_modified and last_modified.replace(tzinfo=timezone.utc) < cutoff_date:
                        candidates.append(EvictionCandidate(
                            s3_key=obj['Key'],
                            content_type=type_name,
                            size_bytes=obj['Size'],
                            last_seen_at=last_modified.replace(tzinfo=timezone.utc)
                        ))
                
            except Exception as e:
                logger.warning(f"Failed to scan prefix {prefix}", error=str(e))
                continue
        
        return candidates
    
    def _guess_content_type(self, mime: str, s3_key: str) -> str:
        """Определение content_type по mime или s3_key."""
        if s3_key.startswith("media/"):
            return "media"
        elif s3_key.startswith("vision/"):
            return "vision"
        elif s3_key.startswith("crawl/"):
            return "crawl"
        
        # По MIME типу
        if mime.startswith("image/") or mime.startswith("video/"):
            return "media"
        elif mime == "application/json":
            if "vision" in s3_key:
                return "vision"
            return "crawl"
        
        return "media"  # default
    
    def _get_content_type_priority(self, content_type: str) -> int:
        """Приоритет типа контента для очистки (меньше = выше приоритет)."""
        priorities = {
            "crawl": 0,
            "vision": 1,
            "media": 2
        }
        return priorities.get(content_type, 99)
    
    async def evict_candidates(
        self,
        candidates: List[EvictionCandidate],
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Удаление кандидатов из S3 с синхронизацией БД.
        
        Context7 best practice:
        - Проверяет refs_count перед удалением (защита от удаления используемых медиа)
        - Удаляет/обновляет записи в media_objects после успешного удаления из S3
        - Синхронизирует состояние между S3 и БД
        
        Returns:
            {
                "deleted_count": int,
                "freed_bytes": int,
                "freed_gb": float,
                "by_content_type": {...},
                "db_updated": int
            }
        """
        if not candidates:
            return {
                "deleted_count": 0,
                "freed_bytes": 0,
                "freed_gb": 0.0,
                "by_content_type": {},
                "db_updated": 0
            }
        
        deleted_count = 0
        freed_bytes = 0
        db_updated = 0
        by_content_type = {}
        
        if dry_run:
            logger.info("DRY RUN: Would evict candidates", count=len(candidates))
            for candidate in candidates:
                by_content_type[candidate.content_type] = by_content_type.get(candidate.content_type, 0) + candidate.size_bytes
                freed_bytes += candidate.size_bytes
                deleted_count += 1
        else:
            # Context7: Проверяем refs_count в БД перед удалением (защита)
            validated_candidates = []
            if self.db_pool:
                validated_candidates = await self._validate_candidates_with_db(candidates)
            else:
                # Без БД используем все кандидаты (рискованно, но лучше чем ничего)
                validated_candidates = candidates
                logger.warning(
                    "DB pool not available, skipping refs_count validation",
                    candidates_count=len(candidates)
                )
            
            if not validated_candidates:
                logger.warning("No validated candidates after DB check", original_count=len(candidates))
                return {
                    "deleted_count": 0,
                    "freed_bytes": 0,
                    "freed_gb": 0.0,
                    "by_content_type": {},
                    "db_updated": 0,
                    "status": "no_validated_candidates"
                }
            
            # Удаляем батчами по 1000 (boto3 лимит)
            for i in range(0, len(validated_candidates), 1000):
                batch = validated_candidates[i:i+1000]
                delete_keys = [{'Key': c.s3_key} for c in batch]
                
                try:
                    result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda keys=delete_keys: self.s3_service.s3_client.delete_objects(
                            Bucket=self.s3_service.bucket_name,
                            Delete={'Objects': keys}
                        )
                    )
                    
                    # Подсчитываем успешно удалённые
                    deleted = result.get('Deleted', [])
                    deleted_keys = {obj['Key'] for obj in deleted}
                    deleted_count += len(deleted)
                    
                    # Context7: Синхронизируем БД после успешного удаления из S3
                    if self.db_pool:
                        batch_db_updated = await self._sync_db_after_deletion(
                            [c for c in batch if c.s3_key in deleted_keys]
                        )
                        db_updated += batch_db_updated
                    
                    for candidate in batch:
                        if candidate.s3_key in deleted_keys:
                            freed_bytes += candidate.size_bytes
                            by_content_type[candidate.content_type] = by_content_type.get(candidate.content_type, 0) + candidate.size_bytes
                            
                            # Обновляем метрики
                            lru_eviction_operations_total.labels(
                                content_type=candidate.content_type,
                                reason='lru'
                            ).inc()
                    
                    # Логируем ошибки удаления (если есть)
                    errors = result.get('Errors', [])
                    if errors:
                        logger.warning(
                            "Some objects failed to delete",
                            errors_count=len(errors),
                            batch_size=len(batch),
                            errors_sample=[e.get('Key') for e in errors[:5]]
                        )
                    
                except Exception as e:
                    logger.error(
                        "Failed to delete batch",
                        batch_size=len(batch),
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True
                    )
        
        freed_gb = freed_bytes / (1024 ** 3)
        
        # Обновляем метрики
        lru_eviction_freed_bytes.labels(content_type='all').observe(freed_bytes)
        
        for content_type, bytes_freed in by_content_type.items():
            lru_eviction_freed_bytes.labels(content_type=content_type).observe(bytes_freed)
        
        result = {
            "deleted_count": deleted_count,
            "freed_bytes": freed_bytes,
            "freed_gb": freed_gb,
            "by_content_type": {
                k: v / (1024 ** 3) for k, v in by_content_type.items()
            },
            "db_updated": db_updated
        }
        
        logger.info(
            "LRU eviction completed",
            deleted_count=deleted_count,
            freed_gb=freed_gb,
            db_updated=db_updated,
            dry_run=dry_run
        )
        
        return result
    
    async def _validate_candidates_with_db(
        self,
        candidates: List[EvictionCandidate]
    ) -> List[EvictionCandidate]:
        """
        Валидация кандидатов на удаление через БД.
        
        Context7: Проверяет refs_count перед удалением для защиты от удаления используемых медиа.
        
        Returns:
            Список валидированных кандидатов (только с refs_count=0)
        """
        if not self.db_pool or not candidates:
            return candidates
        
        try:
            # Получаем s3_key для кандидатов (нужно для поиска по БД)
            # Для медиа из БД уже есть s3_key, для медиа из S3 может не быть
            validated = []
            
            async with self.db_pool.acquire() as conn:
                # Группируем кандидатов по типу для оптимизации запросов
                media_candidates = [
                    c for c in candidates 
                    if c.content_type == 'media' and hasattr(c, 's3_key') and c.s3_key
                ]
                
                if media_candidates:
                    # Context7: Проверяем refs_count для медиа из БД
                    # Получаем file_sha256 из s3_key (нужно для поиска в БД)
                    s3_keys = [c.s3_key for c in media_candidates]
                    
                    # Получаем refs_count для каждого s3_key
                    rows = await conn.fetch("""
                        SELECT s3_key, refs_count, file_sha256
                        FROM media_objects
                        WHERE s3_key = ANY($1::text[])
                    """, s3_keys)
                    
                    # Создаём mapping s3_key -> refs_count
                    refs_map = {row['s3_key']: row['refs_count'] for row in rows}
                    sha256_map = {row['s3_key']: row['file_sha256'] for row in rows}
                    
                    # Фильтруем только кандидатов с refs_count=0
                    for candidate in media_candidates:
                        refs_count = refs_map.get(candidate.s3_key, -1)
                        
                        if refs_count == 0:
                            # Обновляем refs_count в candidate
                            candidate.refs_count = 0
                            # Сохраняем file_sha256 для последующего удаления из БД
                            if candidate.s3_key in sha256_map:
                                candidate.file_sha256 = sha256_map[candidate.s3_key]
                            validated.append(candidate)
                        elif refs_count > 0:
                            logger.debug(
                                "Skipping candidate with refs_count > 0",
                                s3_key=candidate.s3_key,
                                refs_count=refs_count
                            )
                        else:
                            # Медиа нет в БД - считаем безопасным для удаления
                            validated.append(candidate)
                
                # Для vision и crawl добавляем все (не имеют refs_count в media_objects)
                other_candidates = [
                    c for c in candidates 
                    if c.content_type in ('vision', 'crawl')
                ]
                validated.extend(other_candidates)
                
                logger.info(
                    "Validated LRU eviction candidates",
                    original_count=len(candidates),
                    validated_count=len(validated),
                    skipped_count=len(candidates) - len(validated)
                )
                
                return validated
                
        except Exception as e:
            logger.error(
                "Failed to validate candidates with DB",
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            # При ошибке валидации возвращаем пустой список (безопаснее)
            return []
    
    async def _sync_db_after_deletion(
        self,
        deleted_candidates: List[EvictionCandidate]
    ) -> int:
        """
        Синхронизация БД после удаления медиа из S3.
        
        Context7: Удаляет записи из media_objects после успешного удаления из S3.
        
        Returns:
            Количество обновлённых записей в БД
        """
        if not self.db_pool or not deleted_candidates:
            return 0
        
        try:
            async with self.db_pool.acquire() as conn:
                updated_count = 0
                
                # Группируем по типу контента
                media_candidates = [
                    c for c in deleted_candidates 
                    if c.content_type == 'media' and hasattr(c, 'file_sha256') and c.file_sha256
                ]
                
                if media_candidates:
                    # Context7: Удаляем записи из media_objects для удалённых медиа
                    # Используем file_sha256 для точного поиска
                    file_sha256_list = [c.file_sha256 for c in media_candidates if hasattr(c, 'file_sha256')]
                    
                    if file_sha256_list:
                        # Удаляем записи из media_objects (CASCADE удалит связанные записи в post_media_map)
                        result = await conn.execute("""
                            DELETE FROM media_objects
                            WHERE file_sha256 = ANY($1::text[])
                        """, file_sha256_list)
                        
                        updated_count = int(result.split()[-1])  # "DELETE N" -> N
                        
                        logger.info(
                            "Synced DB after media deletion",
                            deleted_media_count=len(file_sha256_list),
                            db_records_updated=updated_count
                        )
                
                # Для vision и crawl медиа нет в media_objects, пропускаем
                
                return updated_count
                
        except Exception as e:
            logger.error(
                "Failed to sync DB after deletion",
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            # Не критично - продолжаем без обновления БД
            return 0
    
    async def evict_to_target(
        self,
        target_free_gb: float,
        content_type: Optional[str] = None,
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Полный цикл LRU eviction до достижения target_free_gb.
        
        Workflow:
        1. Найти кандидатов
        2. Удалить их
        3. Вернуть статистику
        """
        candidates = await self.find_eviction_candidates(target_free_gb, content_type)
        
        if not candidates:
            return {
                "status": "no_candidates",
                "deleted_count": 0,
                "freed_gb": 0.0
            }
        
        result = await self.evict_candidates(candidates, dry_run=dry_run)
        
        return result

