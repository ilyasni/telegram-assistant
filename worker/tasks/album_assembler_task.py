"""
Album Assembler Task
Context7 best practice: отслеживание vision.completed для сборки альбомов

Задача:
- Слушает albums.parsed и posts.vision.analyzed события
- Отслеживает прогресс vision анализа для всех элементов альбома
- Собирает vision summary на уровне альбома
- Эмитирует album.assembled когда все элементы обработаны
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List, Set
from collections import defaultdict

import redis.asyncio as redis
import structlog
from prometheus_client import Counter, Histogram, Gauge
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from event_bus import EventConsumer, EventPublisher, ConsumerConfig, STREAMS
from events.schemas import AlbumParsedEventV1, AlbumAssembledEventV1, VisionAnalyzedEventV1
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from api.services.s3_storage import S3StorageService

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

albums_parsed_total = Counter(
    'albums_parsed_total',
    'Total albums parsed events received',
    ['status']
)

album_assembly_lag_seconds = Histogram(
    'album_assembly_lag_seconds',
    'Time from first to last vision analysis for album',
    buckets=[1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 600.0]
)

album_items_count_gauge = Gauge(
    'album_items_count_gauge',
    'Number of items in albums being assembled',
    ['album_id', 'status']  # status: total, analyzed, pending
)

albums_assembled_total = Counter(
    'albums_assembled_total',
    'Total albums assembled',
    ['status']
)

album_vision_summary_size_bytes = Histogram(
    'album_vision_summary_size_bytes',
    'Size of album vision summary in bytes',
    buckets=[100, 500, 1000, 5000, 10000, 50000, 100000]  # 100B to 100KB
)

album_aggregation_duration_ms = Histogram(
    'album_aggregation_duration_ms',
    'Time to aggregate album vision summary in milliseconds',
    buckets=[10, 50, 100, 500, 1000, 5000]  # 10ms to 5s
)


class AlbumAssemblerTask:
    """
    Album Assembler Task для отслеживания сборки альбомов.
    
    Отслеживает прогресс vision анализа всех элементов альбома и эмитирует
    album.assembled когда все элементы обработаны.
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        db_session: AsyncSession,
        event_publisher: EventPublisher,
        s3_service: Optional['S3StorageService'] = None,
        consumer_group: str = "album_assemblers",
        consumer_name: str = None
    ):
        self.redis = redis_client
        self.db = db_session
        self.event_publisher = event_publisher
        self.s3_service = s3_service
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name or f"album_assembler_{int(time.time())}"
        
        # Хранилище состояния альбомов в Redis
        # Формат ключа: album:state:{album_id}
        # Значение: JSON с полями:
        #   - album_id, grouped_id, channel_id, tenant_id
        #   - items_count, items_analyzed (Set[post_id])
        #   - first_analyzed_at, last_analyzed_at
        #   - vision_summaries: List[Dict] (результаты vision по элементам)
        self.state_prefix = "album:state:"
        self.state_ttl = 86400  # 24 часа
        
        self.running = False
        
        logger.info(
            "AlbumAssemblerTask initialized",
            consumer_group=consumer_group,
            consumer_name=self.consumer_name
        )
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Context7: Health check для album_assembler_task.
        
        Проверяет:
        - Подключение к Redis
        - Наличие активных состояний альбомов
        - Backlog в Redis Streams
        - Последние успешные сборки
        """
        try:
            health = {
                'status': 'healthy',
                'redis_connected': False,
                'running': self.running,
                'albums_in_progress': 0,
                'backlog_size': 0,
                'recent_assembly_rate': 0.0,
                'last_error': None
            }
            
            # Проверка Redis
            try:
                await self.redis.ping()
                health['redis_connected'] = True
            except Exception as e:
                health['status'] = 'unhealthy'
                health['last_error'] = f"Redis ping failed: {str(e)}"
                return health
            
            # Проверка активных состояний альбомов
            try:
                pattern = f"{self.state_prefix}*"
                keys = []
                async for key in self.redis.scan_iter(match=pattern):
                    keys.append(key)
                health['albums_in_progress'] = len(keys)
            except Exception as e:
                logger.debug("Failed to count album states", error=str(e))
            
            # Проверка backlog в Redis Streams
            try:
                from event_bus import STREAMS
                albums_parsed_stream = STREAMS.get('albums.parsed', 'stream:albums:parsed')
                backlog = await self.redis.xpending_range(
                    albums_parsed_stream,
                    self.consumer_group,
                    min='-',
                    max='+',
                    count=100
                )
                health['backlog_size'] = len(backlog) if backlog else 0
            except Exception as e:
                logger.debug("Failed to check backlog", error=str(e))
            
            # Проверка скорости сборки (если есть метрики)
            try:
                # Импортируем метрику для проверки (если доступна)
                import prometheus_client
                registry = prometheus_client.REGISTRY
                # Проверяем наличие метрики albums_assembled_total
                for collector in list(registry._collector_to_names.keys()):
                    if hasattr(collector, '_name') and collector._name == 'albums_assembled_total':
                        # Метрика существует, считаем rate (упрощённо)
                        health['recent_assembly_rate'] = 'available'  # Может быть вычислен из Prometheus
                        break
            except Exception:
                pass
            
            # Определяем общий статус
            if not health['redis_connected'] or not health['running']:
                health['status'] = 'unhealthy'
            elif health['backlog_size'] > 100 or health['albums_in_progress'] > 200:
                health['status'] = 'degraded'
            
            return health
            
        except Exception as e:
            logger.error(
                "Error in album_assembler health check",
                error=str(e)
            )
            return {
                'status': 'unhealthy',
                'error': str(e),
                'running': self.running
            }
    
    async def start(self):
        """Запуск Album Assembler Task."""
        self.running = True
        
        # Context7: Создание consumer groups для обоих стримов
        albums_parsed_stream = STREAMS['albums.parsed']
        vision_analyzed_stream = STREAMS['posts.vision.analyzed']
        
        # Создаём consumer groups (идемпотентно)
        for stream_name, stream_key in [
            ('albums.parsed', albums_parsed_stream),
            ('posts.vision.analyzed', vision_analyzed_stream)
        ]:
            try:
                await self.redis.xgroup_create(
                    stream_key,
                    self.consumer_group,
                    id='0',
                    mkstream=True
                )
                logger.info(f"Created consumer group for {stream_name}")
            except redis.ResponseError as e:
                if "BUSYGROUP" not in str(e):
                    logger.error(f"Failed to create consumer group for {stream_name}: {e}")
                    raise
                else:
                    logger.debug(f"Consumer group already exists for {stream_name}")
        
        logger.info("AlbumAssemblerTask started")
        
        # Запускаем обработку обоих стримов параллельно
        await asyncio.gather(
            self._consume_albums_parsed(),
            self._consume_vision_analyzed(),
            return_exceptions=True
        )
    
    async def _consume_albums_parsed(self):
        """Обработка событий albums.parsed."""
        stream_key = STREAMS['albums.parsed']
        backlog_processed = False
        
        while self.running:
            try:
                start_id = '0' if not backlog_processed else '>'
                
                messages = await self.redis.xreadgroup(
                    self.consumer_group,
                    self.consumer_name,
                    {stream_key: start_id},
                    count=10,
                    block=2000 if start_id == '>' else 100
                )
                
                if messages:
                    for stream_name, stream_messages in messages:
                        for message_id, fields in stream_messages:
                            try:
                                await self._process_album_parsed(message_id, fields)
                                await self.redis.xack(stream_key, self.consumer_group, message_id)
                                albums_parsed_total.labels(status='ok').inc()
                            except Exception as e:
                                logger.error(
                                    "Error processing album.parsed event",
                                    message_id=message_id,
                                    error=str(e),
                                    exc_info=True
                                )
                                albums_parsed_total.labels(status='error').inc()
                    
                    backlog_processed = True
                else:
                    if start_id == '0':
                        backlog_processed = True
                    await asyncio.sleep(0.1)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in _consume_albums_parsed", error=str(e), exc_info=True)
                await asyncio.sleep(5)
    
    async def _consume_vision_analyzed(self):
        """Обработка событий posts.vision.analyzed."""
        stream_key = STREAMS['posts.vision.analyzed']
        backlog_processed = False
        
        while self.running:
            try:
                start_id = '0' if not backlog_processed else '>'
                
                messages = await self.redis.xreadgroup(
                    self.consumer_group,
                    self.consumer_name,
                    {stream_key: start_id},
                    count=10,
                    block=2000 if start_id == '>' else 100
                )
                
                if messages:
                    for stream_name, stream_messages in messages:
                        for message_id, fields in stream_messages:
                            try:
                                await self._process_vision_analyzed(message_id, fields)
                                await self.redis.xack(stream_key, self.consumer_group, message_id)
                            except Exception as e:
                                logger.error(
                                    "Error processing vision.analyzed event",
                                    message_id=message_id,
                                    error=str(e),
                                    exc_info=True
                                )
                    
                    backlog_processed = True
                else:
                    if start_id == '0':
                        backlog_processed = True
                    await asyncio.sleep(0.1)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in _consume_vision_analyzed", error=str(e), exc_info=True)
                await asyncio.sleep(5)
    
    async def _process_album_parsed(self, message_id: str, fields: Dict[str, Any]):
        """Обработка события albums.parsed - инициализация состояния альбома."""
        # Парсим событие
        event_data = self._parse_event_data(fields)
        
        album_id = event_data.get('album_id')
        grouped_id = event_data.get('grouped_id')
        channel_id = event_data.get('channel_id')
        tenant_id = event_data.get('tenant_id')
        
        # Context7: Нормализация post_ids (может быть JSON строка или список)
        post_ids_raw = event_data.get('post_ids', [])
        if isinstance(post_ids_raw, str):
            try:
                post_ids = json.loads(post_ids_raw)
            except json.JSONDecodeError:
                logger.warning("Failed to parse post_ids as JSON", post_ids_raw=post_ids_raw[:100])
                post_ids = []
        elif isinstance(post_ids_raw, list):
            post_ids = post_ids_raw
        else:
            post_ids = []
        
        # Context7: items_count должен соответствовать реальному количеству постов
        # Используем количество post_ids как источник правды
        items_count = event_data.get('items_count', len(post_ids))
        # Исправляем, если items_count не соответствует реальному количеству постов
        if len(post_ids) > 0 and items_count != len(post_ids):
            logger.warning(
                "items_count mismatch, using actual post_ids count",
                album_id=album_id,
                reported_items_count=items_count,
                actual_post_ids_count=len(post_ids)
            )
            items_count = len(post_ids)
        
        if not album_id:
            logger.warning("Missing album_id in albums.parsed event", message_id=message_id)
            return
        
        # Инициализируем состояние альбома в Redis
        state_key = f"{self.state_prefix}{album_id}"
        state = {
            'album_id': album_id,
            'grouped_id': grouped_id,
            'channel_id': channel_id,
            'tenant_id': tenant_id,
            'items_count': items_count,
            'post_ids': post_ids,
            'items_analyzed': [],  # Set[post_id]
            'first_analyzed_at': None,
            'last_analyzed_at': None,
            'vision_summaries': [],  # List[Dict] с результатами vision
            'created_at': datetime.now(timezone.utc).isoformat()
        }
        
        await self.redis.setex(
            state_key,
            self.state_ttl,
            json.dumps(state)
        )
        
        # Обновляем метрики
        album_items_count_gauge.labels(album_id=str(album_id), status='total').set(items_count)
        album_items_count_gauge.labels(album_id=str(album_id), status='analyzed').set(0)
        album_items_count_gauge.labels(album_id=str(album_id), status='pending').set(items_count)
        
        logger.info(
            "Album state initialized",
            album_id=album_id,
            grouped_id=grouped_id,
            items_count=items_count
        )
    
    async def _process_vision_analyzed(self, message_id: str, fields: Dict[str, Any]):
        """Обработка события posts.vision.analyzed - обновление прогресса альбома."""
        # Парсим событие
        event_data = self._parse_event_data(fields)
        post_id = event_data.get('post_id') or event_data.get('payload', {}).get('post_id')
        
        if not post_id:
            logger.warning("Missing post_id in vision.analyzed event", message_id=message_id)
            return
        
        # Находим альбом, которому принадлежит этот пост
        album_id = await self._find_album_for_post(post_id)
        if not album_id:
            # Пост не принадлежит альбому, пропускаем
            return
        
        # Получаем состояние альбома
        state_key = f"{self.state_prefix}{album_id}"
        state_json = await self.redis.get(state_key)
        if not state_json:
            logger.warning(
                "Album state not found for vision.analyzed event",
                album_id=album_id,
                post_id=post_id
            )
            return
        
        state = json.loads(state_json)
        
        # Проверяем, не обработан ли уже этот пост
        if post_id in state.get('items_analyzed', []):
            logger.debug(
                "Post already analyzed for album",
                album_id=album_id,
                post_id=post_id
            )
            return
        
        # Получаем vision результаты из БД
        vision_data = await self._get_vision_results(post_id)
        if not vision_data:
            logger.warning(
                "Vision results not found in DB",
                album_id=album_id,
                post_id=post_id
            )
            return
        
        # Обновляем состояние
        state['items_analyzed'].append(post_id)
        
        analyzed_at = datetime.now(timezone.utc)
        if not state.get('first_analyzed_at'):
            state['first_analyzed_at'] = analyzed_at.isoformat()
        state['last_analyzed_at'] = analyzed_at.isoformat()
        
        # Добавляем vision summary
        vision_summary = {
            'post_id': post_id,
            'analyzed_at': analyzed_at.isoformat(),
            'description': vision_data.get('description'),
            'labels': vision_data.get('labels', []),
            'is_meme': vision_data.get('is_meme', False),
            'has_text': bool(vision_data.get('ocr_text'))
        }
        state['vision_summaries'].append(vision_summary)
        
        # Сохраняем обновлённое состояние
        await self.redis.setex(
            state_key,
            self.state_ttl,
            json.dumps(state)
        )
        
        # Обновляем метрики
        items_analyzed = len(state['items_analyzed'])
        items_total = state['items_count']
        album_items_count_gauge.labels(album_id=str(album_id), status='analyzed').set(items_analyzed)
        album_items_count_gauge.labels(album_id=str(album_id), status='pending').set(items_total - items_analyzed)
        
        # Проверяем, собрался ли альбом полностью
        if items_analyzed >= items_total:
            await self._assemble_album(album_id, state)
        
        logger.debug(
            "Album progress updated",
            album_id=album_id,
            post_id=post_id,
            items_analyzed=items_analyzed,
            items_total=items_total
        )
    
    async def _find_album_for_post(self, post_id: str) -> Optional[int]:
        """Находит album_id для поста через media_group_items."""
        try:
            result = await self.db.execute(
                text("""
                    SELECT group_id
                    FROM media_group_items
                    WHERE post_id = :post_id
                    LIMIT 1
                """),
                {"post_id": post_id}
            )
            row = result.fetchone()
            if row:
                # Получаем album_id из media_groups
                result2 = await self.db.execute(
                    text("""
                        SELECT id FROM media_groups WHERE id = :group_id LIMIT 1
                    """),
                    {"group_id": row[0]}
                )
                row2 = result2.fetchone()
                return row2[0] if row2 else None
        except Exception as e:
            logger.debug(
                "Error finding album for post",
                post_id=post_id,
                error=str(e)
            )
        return None
    
    async def _get_vision_results(self, post_id: str) -> Optional[Dict[str, Any]]:
        """
        Получает vision результаты для поста из БД.
        
        Context7 best practice: Используем новый формат (data JSONB) с fallback на legacy поля.
        """
        try:
            # Context7: Используем новый формат (data JSONB) с fallback на legacy поля
            result = await self.db.execute(
                text("""
                    SELECT 
                        pe.data,
                        pe.updated_at,
                        -- Legacy поля для обратной совместимости (fallback)
                        pe.vision_description,
                        pe.vision_classification,
                        pe.vision_is_meme,
                        pe.vision_ocr_text,
                        pe.vision_analyzed_at
                    FROM post_enrichment pe
                    WHERE pe.post_id = :post_id
                    AND pe.kind = 'vision'
                    ORDER BY pe.updated_at DESC
                    LIMIT 1
                """),
                {"post_id": post_id}
            )
            row = result.fetchone()
            if row:
                data_jsonb = row[0]  # data (JSONB)
                
                # Context7: Приоритет новому формату (data JSONB)
                if data_jsonb and isinstance(data_jsonb, dict):
                    # Извлекаем из нового формата
                    description = data_jsonb.get('description') or data_jsonb.get('caption')
                    labels = data_jsonb.get('labels', [])
                    is_meme = data_jsonb.get('is_meme', False)
                    ocr_data = data_jsonb.get('ocr')
                    ocr_text = ocr_data.get('text') if isinstance(ocr_data, dict) else (ocr_data if isinstance(ocr_data, str) else None)
                    analyzed_at_str = data_jsonb.get('analyzed_at')
                    
                    return {
                        'description': description,
                        'labels': labels if isinstance(labels, list) else [],
                        'is_meme': bool(is_meme),
                        'ocr_text': ocr_text,
                        'analyzed_at': analyzed_at_str
                    }
                
                # Fallback: Legacy поля (для обратной совместимости)
                description = row[2]  # vision_description
                classification = row[3]  # vision_classification
                is_meme = row[4] if row[4] is not None else False
                ocr_text = row[5]  # vision_ocr_text
                analyzed_at = row[6]  # vision_analyzed_at
                
                # Извлекаем labels из vision_classification (JSONB)
                labels = []
                if classification:
                    try:
                        if isinstance(classification, str):
                            classification = json.loads(classification)
                        if isinstance(classification, dict):
                            labels = classification.get('tags', []) or classification.get('labels', [])
                            if not isinstance(labels, list):
                                labels = []
                    except Exception:
                        pass
                
                if description or labels or ocr_text:
                    return {
                        'description': description,
                        'labels': labels,
                        'is_meme': is_meme,
                        'ocr_text': ocr_text,
                        'analyzed_at': analyzed_at.isoformat() if analyzed_at else None
                    }
        except Exception as e:
            logger.debug(
                "Error getting vision results",
                post_id=post_id,
                error=str(e)
            )
        return None
    
    def _aggregate_vision_summary(self, vision_summaries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Улучшенная агрегация vision summary на уровне альбома.
        
        Context7: Умное объединение описаний, дедупликация labels, приоритизация.
        """
        if not vision_summaries:
            return {
                'summary': None,
                'labels': [],
                'ocr_text': None,
                'has_meme': False,
                'has_text': False
            }
        
        # Объединяем описания: приоритет первым элементам (обложка обычно первая)
        descriptions = []
        for idx, s in enumerate(vision_summaries):
            desc = s.get('description')
            if desc and desc.strip():
                # Первые 3 элемента имеют больший вес
                descriptions.append({
                    'text': desc.strip(),
                    'weight': 3.0 - (idx * 0.5) if idx < 3 else 1.0
                })
        
        # Формируем сводное описание (уникальные + приоритетные)
        seen_descriptions = set()
        weighted_summary_parts = []
        for desc_item in sorted(descriptions, key=lambda x: x['weight'], reverse=True):
            text = desc_item['text']
            # Дедупликация похожих описаний (простейший вариант - точное совпадение)
            if text not in seen_descriptions:
                seen_descriptions.add(text)
                weighted_summary_parts.append(text)
                if len(weighted_summary_parts) >= 5:  # Максимум 5 уникальных описаний
                    break
        
        vision_summary = " | ".join(weighted_summary_parts) if weighted_summary_parts else None
        
        # Объединяем labels с дедупликацией и нормализацией
        all_labels = defaultdict(float)  # label -> count
        for s in vision_summaries:
            labels = s.get('labels', [])
            if isinstance(labels, list):
                for label in labels:
                    if label and isinstance(label, str):
                        # Нормализация: lowercase, strip
                        normalized = label.strip().lower()
                        if normalized:
                            all_labels[normalized] += 1.0
        
        # Сортируем по частоте и берём топ-20
        sorted_labels = sorted(all_labels.items(), key=lambda x: x[1], reverse=True)
        vision_labels = [label for label, count in sorted_labels[:20]]
        
        # Объединяем OCR текст
        ocr_texts = []
        for s in vision_summaries:
            ocr = s.get('ocr_text')
            if ocr and ocr.strip():
                ocr_texts.append(ocr.strip())
        ocr_text = " | ".join(ocr_texts[:3]) if ocr_texts else None  # Максимум 3 OCR текста
        
        # Проверяем мемы и текст
        has_meme = any(s.get('is_meme', False) for s in vision_summaries)
        has_text = bool(ocr_text) or any(s.get('has_text', False) for s in vision_summaries)
        
        return {
            'summary': vision_summary,
            'labels': vision_labels,
            'ocr_text': ocr_text,
            'has_meme': has_meme,
            'has_text': has_text
        }
    
    async def _assemble_album(self, album_id: int, state: Dict[str, Any]):
        """Собирает альбом: формирует vision summary и эмитирует album.assembled."""
        import time
        aggregation_start = time.time()
        
        try:
            # Собираем vision summary на уровне альбома
            vision_summaries = state.get('vision_summaries', [])
            if not vision_summaries:
                logger.warning("No vision summaries for album", album_id=album_id)
                return
            
            # Context7: Улучшенная агрегация vision summary
            aggregated = self._aggregate_vision_summary(vision_summaries)
            vision_summary = aggregated['summary']
            vision_labels = aggregated['labels']
            ocr_text = aggregated['ocr_text']
            has_meme = aggregated['has_meme']
            has_text = aggregated['has_text']
            
            # Вычисляем assembly lag
            first_analyzed = state.get('first_analyzed_at')
            last_analyzed = state.get('last_analyzed_at')
            assembly_lag_seconds = None
            if first_analyzed and last_analyzed:
                try:
                    first_dt = datetime.fromisoformat(first_analyzed.replace('Z', '+00:00'))
                    last_dt = datetime.fromisoformat(last_analyzed.replace('Z', '+00:00'))
                    assembly_lag_seconds = (last_dt - first_dt).total_seconds()
                    album_assembly_lag_seconds.observe(assembly_lag_seconds)
                except Exception as e:
                    logger.debug("Error calculating assembly lag", error=str(e))
            
            # Context7: Сохраняем vision summary в S3
            s3_key = None
            size_bytes = None
            if self.s3_service and vision_summary:
                try:
                    tenant_id = state.get('tenant_id', 'default')
                    s3_key = self.s3_service.build_album_key(
                        tenant_id=tenant_id,
                        album_id=album_id,
                        suffix="_vision_summary",
                        schema_version="v1"
                    )
                    
                    # Формируем структуру для сохранения
                    album_summary_data = {
                        'album_id': album_id,
                        'grouped_id': state.get('grouped_id'),
                        'tenant_id': tenant_id,
                        'channel_id': state.get('channel_id'),
                        'items_count': state.get('items_count'),
                        'items_analyzed': len(state.get('items_analyzed', [])),
                        'vision_summary': vision_summary,
                        'vision_labels': vision_labels,
                        'ocr_text': ocr_text,
                        'has_meme': has_meme,
                        'has_text': has_text,
                        'first_analyzed_at': first_analyzed,
                        'last_analyzed_at': last_analyzed,
                        'assembly_completed_at': datetime.now(timezone.utc).isoformat(),
                        'assembly_lag_seconds': assembly_lag_seconds,
                        'schema_version': '1.0'
                    }
                    
                    # Сохраняем в S3 (сжатие автоматически через put_json)
                    size_bytes = await self.s3_service.put_json(
                        s3_key=s3_key,
                        data=album_summary_data,
                        compress=True
                    )
                    
                    logger.debug(
                        "Album vision summary saved to S3",
                        album_id=album_id,
                        s3_key=s3_key,
                        size_bytes=size_bytes
                    )
                except Exception as e:
                    logger.warning(
                        "Failed to save album vision summary to S3",
                        album_id=album_id,
                        error=str(e)
                    )
            
            # Context7: Сохраняем enrichment в БД (media_groups.meta)
            try:
                enrichment_meta = {
                    'vision_summary': vision_summary,
                    'vision_labels': vision_labels,
                    'ocr_text': ocr_text,
                    'has_meme': has_meme,
                    'has_text': has_text,
                    'assembly_completed_at': datetime.now(timezone.utc).isoformat(),
                    's3_key': s3_key
                }
                
                # Context7: Используем правильный синтаксис для asyncpg (CAST вместо ::)
                # Context7: ensure_ascii=False для корректного сохранения кириллицы и специальных символов
                enrichment_json = json.dumps(enrichment_meta, ensure_ascii=False, default=str)
                await self.db.execute(
                    text("""
                        UPDATE media_groups
                        SET meta = jsonb_set(
                            COALESCE(meta, '{}'::jsonb),
                            '{enrichment}',
                            CAST(:enrichment AS jsonb)
                        )
                        WHERE id = :album_id
                    """),
                    {
                        "album_id": album_id,
                        "enrichment": enrichment_json
                    }
                )
                await self.db.commit()
                
                logger.debug(
                    "Album enrichment saved to DB",
                    album_id=album_id
                )
            except Exception as e:
                logger.warning(
                    "Failed to save album enrichment to DB",
                    album_id=album_id,
                    error=str(e)
                )
            
            # Формируем событие album.assembled
            event = AlbumAssembledEventV1(
                idempotency_key=f"{state['tenant_id']}:{state['channel_id']}:{state['grouped_id']}:assembled",
                user_id=state.get('user_id', ''),
                channel_id=state['channel_id'],
                album_id=album_id,
                grouped_id=state['grouped_id'],
                tenant_id=state['tenant_id'],
                album_kind=None,  # Можно получить из БД при необходимости
                items_count=state['items_count'],
                items_analyzed=len(state['items_analyzed']),
                vision_summary=vision_summary,
                vision_labels=vision_labels,
                vision_ocr_text=ocr_text,
                vision_tags=[],  # Можно заполнить из тегов постов
                has_meme=has_meme,
                has_text=has_text,
                s3_key=s3_key,  # S3 ключ для vision summary
                posted_at=None,  # Можно получить из БД
                first_analyzed_at=datetime.fromisoformat(first_analyzed.replace('Z', '+00:00')) if first_analyzed else None,
                last_analyzed_at=datetime.fromisoformat(last_analyzed.replace('Z', '+00:00')) if last_analyzed else None,
                assembly_completed_at=datetime.now(timezone.utc),
                assembly_lag_seconds=assembly_lag_seconds
            )
            
            # Эмитируем событие
            await self.event_publisher.publish_event('album.assembled', event)
            
            # Обновляем метрики
            albums_assembled_total.labels(status='ok').inc()
            
            aggregation_duration_ms = (time.time() - aggregation_start) * 1000
            album_aggregation_duration_ms.observe(aggregation_duration_ms)
            
            # Обновляем метрики размера S3 (если сохранение прошло успешно)
            if s3_key and size_bytes is not None:
                album_vision_summary_size_bytes.observe(size_bytes)
            
            # Удаляем состояние альбома (больше не нужно)
            state_key = f"{self.state_prefix}{album_id}"
            await self.redis.delete(state_key)
            
            logger.info(
                "Album assembled and event emitted",
                album_id=album_id,
                grouped_id=state['grouped_id'],
                items_analyzed=len(state['items_analyzed']),
                assembly_lag_seconds=assembly_lag_seconds
            )
            
        except Exception as e:
            logger.error(
                "Error assembling album",
                album_id=album_id,
                error=str(e),
                exc_info=True
            )
            albums_assembled_total.labels(status='error').inc()
    
    def _parse_event_data(self, fields: Dict[str, Any]) -> Dict[str, Any]:
        """
        Парсинг данных события из Redis Streams.
        
        Context7 best practice: Поддержка разных форматов событий:
        - data как JSON string (bytes или str)
        - прямые поля (legacy формат)
        - bytes ключи и значения от redis-py
        """
        # Context7: Декодирование bytes ключей и значений (redis-py может возвращать bytes)
        decoded_fields = {}
        for k, v in fields.items():
            key = k.decode('utf-8') if isinstance(k, bytes) else k
            if isinstance(v, bytes):
                try:
                    decoded_fields[key] = v.decode('utf-8')
                except UnicodeDecodeError:
                    decoded_fields[key] = v
            else:
                decoded_fields[key] = v
        
        # Context7: Поддержка формата с полем 'data' (JSON string)
        if "data" in decoded_fields:
            data_str = decoded_fields["data"]
            if isinstance(data_str, str):
                try:
                    parsed_data = json.loads(data_str)
                    # Context7: Если это dict, возвращаем его (новый формат)
                    if isinstance(parsed_data, dict):
                        return parsed_data
                except json.JSONDecodeError as e:
                    logger.error(
                        "Failed to parse JSON data field in albums.parsed event",
                        error=str(e),
                        data_preview=data_str[:200] if len(str(data_str)) > 200 else data_str
                    )
                    # Fallback: возвращаем исходные поля
            elif isinstance(data_str, dict):
                return data_str
        
        # Context7: Поддержка формата с полем 'payload'
        if "payload" in decoded_fields:
            payload = decoded_fields["payload"]
            if isinstance(payload, str):
                try:
                    return json.loads(payload)
                except json.JSONDecodeError:
                    pass
            elif isinstance(payload, dict):
                return payload
        
        # Context7: Legacy формат - прямые поля, нормализуем JSON строки
        event_data = {}
        for key, value in decoded_fields.items():
            # Пропускаем служебные поля
            if key in ['event', 'idempotency_key']:
                continue
                
            if key in ['post_ids', 'labels', 'tags']:
                # Context7: Нормализация полей, которые могут быть JSON строками
                if isinstance(value, str):
                    try:
                        event_data[key] = json.loads(value)
                    except json.JSONDecodeError:
                        event_data[key] = value
                else:
                    event_data[key] = value
            else:
                event_data[key] = value
        
        return event_data
    
    async def stop(self):
        """Остановка task."""
        self.running = False
        logger.info("AlbumAssemblerTask stopped")

