"""
Context7 best practice: Атомарное сохранение в БД без FK ошибок.

Порядок операций:
1. UPSERT users (ON CONFLICT telegram_id)
2. UPSERT channels (ON CONFLICT telegram_id) 
3. INSERT posts (ON CONFLICT DO NOTHING)

HWM обновляется ТОЛЬКО после успешного commit.
"""

import time
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
import structlog
from sqlalchemy import text, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert
from prometheus_client import Counter, Histogram

logger = structlog.get_logger()

# Context7: Метрики БД без высокой кардинальности
db_posts_insert_success_total = Counter(
    'db_posts_insert_success_total',
    'Successful post inserts'
)

db_posts_insert_failures_total = Counter(
    'db_posts_insert_failures_total',
    'Failed inserts',
    ['reason']  # fk_violation, timeout, connection_error, unknown
)

db_batch_commit_latency_seconds = Histogram(
    'db_batch_commit_latency_seconds',
    'Batch commit latency',
    buckets=[0.1, 0.5, 1, 2, 5, 10]
)

db_transaction_rollbacks_total = Counter(
    'db_transaction_rollbacks_total',
    'Transaction rollbacks',
    ['reason']
)

db_users_upserted_total = Counter(
    'db_users_upserted_total',
    'Users upserted'
)

db_channels_upserted_total = Counter(
    'db_channels_upserted_total',
    'Channels upserted'
)

# Context7: Метрики для CAS операций (media_objects + post_media_map)
media_objects_upserted_total = Counter(
    'media_objects_upserted_total',
    'Total media_objects upserted',
    ['status']  # 'new', 'existing'
)

media_objects_refs_updated_total = Counter(
    'media_objects_refs_updated_total',
    'Total refs_count increments'
)

post_media_map_inserted_total = Counter(
    'post_media_map_inserted_total',
    'Total post_media_map inserts',
    ['status']  # 'new', 'duplicate'
)

cas_operations_latency_seconds = Histogram(
    'cas_operations_latency_seconds',
    'CAS operations latency',
    ['operation'],  # 'save_media_to_cas'
    buckets=[0.01, 0.05, 0.1, 0.5, 1, 2, 5]
)

cas_operations_errors_total = Counter(
    'cas_operations_errors_total',
    'CAS operations errors',
    ['operation', 'error_type']
)


class AtomicDBSaver:
    """
    Context7: Атомарное сохранение без FK ошибок.
    
    Гарантии:
    - UPSERT users/channels перед INSERT posts
    - ON CONFLICT DO NOTHING для идемпотентности
    - HWM обновляется только после commit
    - Полные метрики для мониторинга
    """
    
    def __init__(self):
        self.logger = logger
    
    async def save_batch_atomic(
        self,
        db_session: AsyncSession,
        user_data: Dict[str, Any],
        channel_data: Dict[str, Any],
        posts_data: List[Dict[str, Any]]
    ) -> Tuple[bool, Optional[str], int]:
        """
        Атомарная транзакция с метриками.
        
        Args:
            db_session: AsyncSession для транзакции
            user_data: Данные пользователя
            channel_data: Данные канала
            posts_data: Список постов для сохранения
            
        Returns:
            Tuple[success, error_message, inserted_count]
        """
        if not posts_data:
            return True, None, 0
            
        start_time = time.time()
        inserted_count = 0
        
        # [C7-ID: dev-mode-011] Context7 best practice: проверка состояния сессии перед транзакцией
        # Исправление ошибки "A transaction is already begun on this Session"
        try:
            # Если сессия уже в транзакции - делаем rollback для чистого состояния
            if db_session.in_transaction():
                await db_session.rollback()
                self.logger.debug("Rolled back existing transaction before starting new one")
        except Exception as e:
            self.logger.warning("Failed to check/rollback session state", error=str(e))
        
        try:
            async with db_session.begin():
                # 1. UPSERT user (ON CONFLICT telegram_id)
                await self._upsert_user(db_session, user_data)
                
                # 2. UPSERT channel (ON CONFLICT telegram_id)
                await self._upsert_channel(db_session, channel_data)
                
                # 3. BULK INSERT posts (ON CONFLICT DO NOTHING)
                inserted_count = await self._bulk_insert_posts(
                    db_session, 
                    posts_data
                )
                
            # Коммит успешен (context manager автоматически коммитит)
            duration = time.time() - start_time
            db_batch_commit_latency_seconds.observe(duration)
            db_posts_insert_success_total.inc(inserted_count)
            
            self.logger.info("Atomic batch save successful", 
                           user_id=user_data.get('telegram_id'),
                           channel_id=channel_data.get('telegram_id'),
                           posts_count=len(posts_data),
                           inserted_count=inserted_count,
                           duration=duration)
            
            return True, None, inserted_count
            
        except Exception as e:
            await db_session.rollback()
            
            reason = self._classify_error(e)
            db_posts_insert_failures_total.labels(reason=reason).inc()
            db_transaction_rollbacks_total.labels(reason=reason).inc()
            
            self.logger.error("Atomic batch save failed", 
                            user_id=user_data.get('telegram_id'),
                            channel_id=channel_data.get('telegram_id'),
                            posts_count=len(posts_data),
                            error=str(e), 
                            reason=reason)
            return False, str(e), 0
    
    async def save_forwards_reactions_replies(
        self,
        db_session: AsyncSession,
        post_id: str,
        forwards_data: List[Dict[str, Any]] = None,
        reactions_data: List[Dict[str, Any]] = None,
        replies_data: List[Dict[str, Any]] = None
    ) -> None:
        """
        Context7: Сохранение деталей forwards, reactions и replies в БД.
        Использует batch insert с идемпотентностью через ON CONFLICT.
        
        Args:
            db_session: SQLAlchemy AsyncSession
            post_id: UUID поста
            forwards_data: Список данных о forwards
            reactions_data: Список данных о reactions
            replies_data: Список данных о replies
        """
        from sqlalchemy import text
        
        try:
            # Сохранение forwards
            if forwards_data:
                forwards_sql = text("""
                    INSERT INTO post_forwards (
                        post_id, from_chat_id, from_message_id,
                        from_chat_title, from_chat_username, forwarded_at
                    ) VALUES (
                        :post_id, :from_chat_id, :from_message_id,
                        :from_chat_title, :from_chat_username, :forwarded_at
                    )
                    ON CONFLICT DO NOTHING
                """)
                
                forwards_params = [
                    {
                        'post_id': post_id,
                        'from_chat_id': fwd.get('from_chat_id'),
                        'from_message_id': fwd.get('from_message_id'),
                        'from_chat_title': fwd.get('from_chat_title'),
                        'from_chat_username': fwd.get('from_chat_username'),
                        'forwarded_at': fwd.get('forwarded_at')
                    }
                    for fwd in forwards_data
                ]
                
                if forwards_params:
                    await db_session.execute(forwards_sql, forwards_params)
                    logger.debug("Saved forwards", post_id=post_id, count=len(forwards_data))
            
            # Сохранение reactions
            if reactions_data:
                reactions_sql = text("""
                    INSERT INTO post_reactions (
                        post_id, reaction_type, reaction_value, user_tg_id, is_big
                    ) VALUES (
                        :post_id, :reaction_type, :reaction_value, :user_tg_id, :is_big
                    )
                    ON CONFLICT (post_id, reaction_type, reaction_value, user_tg_id) 
                    DO UPDATE SET updated_at = NOW()
                """)
                
                reactions_params = [
                    {
                        'post_id': post_id,
                        'reaction_type': reaction.get('reaction_type', 'emoji'),
                        'reaction_value': reaction.get('reaction_value'),
                        'user_tg_id': reaction.get('user_tg_id'),
                        'is_big': reaction.get('is_big', False)
                    }
                    for reaction in reactions_data
                ]
                
                if reactions_params:
                    await db_session.execute(reactions_sql, reactions_params)
                    logger.debug("Saved reactions", post_id=post_id, count=len(reactions_data))
            
            # Сохранение replies
            if replies_data:
                replies_sql = text("""
                    INSERT INTO post_replies (
                        post_id, reply_to_post_id, reply_message_id, reply_chat_id,
                        reply_author_tg_id, reply_author_username, reply_content, reply_posted_at
                    ) VALUES (
                        :post_id, :reply_to_post_id, :reply_message_id, :reply_chat_id,
                        :reply_author_tg_id, :reply_author_username, :reply_content, :reply_posted_at
                    )
                    ON CONFLICT DO NOTHING
                """)
                
                replies_params = [
                    {
                        'post_id': post_id,
                        'reply_to_post_id': reply.get('reply_to_post_id'),
                        'reply_message_id': reply.get('reply_message_id'),
                        'reply_chat_id': reply.get('reply_chat_id'),
                        'reply_author_tg_id': reply.get('reply_author_tg_id'),
                        'reply_author_username': reply.get('reply_author_username'),
                        'reply_content': reply.get('reply_content'),
                        'reply_posted_at': reply.get('reply_posted_at')
                    }
                    for reply in replies_data
                ]
                
                if replies_params:
                    await db_session.execute(replies_sql, replies_params)
                    logger.debug("Saved replies", post_id=post_id, count=len(replies_data))
                    
        except Exception as e:
            logger.error("Failed to save forwards/reactions/replies", 
                        post_id=post_id, error=str(e))
            # Не прерываем транзакцию - это дополнительная информация
    
    async def save_media_to_cas(
        self,
        db_session: AsyncSession,
        post_id: str,
        media_files: List[Any],  # List[MediaFile] - объекты с sha256, s3_key, mime_type, size_bytes
        s3_bucket: str,
        trace_id: Optional[str] = None
    ) -> None:
        """
        Context7: Сохранение медиа в CAS таблицы (media_objects и post_media_map).
        
        Использует:
        - UPSERT в media_objects с инкрементом refs_count
        - INSERT в post_media_map с ON CONFLICT DO NOTHING
        - Транзакционность через db_session
        - Метрики Prometheus для мониторинга
        
        Args:
            db_session: SQLAlchemy AsyncSession
            post_id: UUID поста
            media_files: Список MediaFile объектов (sha256, s3_key, mime_type, size_bytes)
            s3_bucket: Имя S3 bucket
            trace_id: Trace ID для логирования
        """
        if not media_files:
            return
        
        start_time = time.time()
        
        try:
            # Context7: Batch insert для media_objects (UPSERT с инкрементом refs_count)
            # Context7: asyncpg требует naive datetime для PostgreSQL TIMESTAMP
            # Используем datetime.utcnow() вместо datetime.now(timezone.utc)
            now_utc = datetime.utcnow()
            media_objects_params = []
            for media_file in media_files:
                media_objects_params.append({
                    'file_sha256': media_file.sha256,
                    'mime': media_file.mime_type,
                    'size_bytes': media_file.size_bytes,
                    's3_key': media_file.s3_key,
                    's3_bucket': s3_bucket,
                    'now': now_utc
                })
            
            if media_objects_params:
                # Context7: Проверяем, какие медиа уже существуют (для метрик)
                # Используем ANY для asyncpg - правильный синтаксис для массива
                existing_check_sql = text("""
                    SELECT file_sha256 FROM media_objects 
                    WHERE file_sha256 = ANY(:sha256_list)
                """)
                sha256_list = [mf.sha256 for mf in media_files]
                existing_result = await db_session.execute(
                    existing_check_sql,
                    {"sha256_list": sha256_list}
                )
                existing_sha256s = {row[0] for row in existing_result.fetchall()}
                
                media_objects_sql = text("""
                    INSERT INTO media_objects (
                        file_sha256, mime, size_bytes, s3_key, s3_bucket,
                        first_seen_at, last_seen_at, refs_count
                    ) VALUES (
                        :file_sha256, :mime, :size_bytes, :s3_key, :s3_bucket,
                        :now, :now, 1
                    )
                    ON CONFLICT (file_sha256) DO UPDATE SET
                        last_seen_at = :now,
                        refs_count = media_objects.refs_count + 1,
                        s3_key = EXCLUDED.s3_key,
                        s3_bucket = EXCLUDED.s3_bucket
                """)
                
                await db_session.execute(media_objects_sql, media_objects_params)
                
                # Context7: Обновляем метрики
                for mf in media_files:
                    if mf.sha256 in existing_sha256s:
                        media_objects_upserted_total.labels(status='existing').inc()
                        media_objects_refs_updated_total.inc()
                    else:
                        media_objects_upserted_total.labels(status='new').inc()
                
                logger.debug(
                    "Saved media_objects",
                    post_id=post_id,
                    count=len(media_files),
                    trace_id=trace_id
                )
            
            # Context7: Batch insert для post_media_map
            # Context7: asyncpg требует naive datetime для PostgreSQL TIMESTAMP
            # Используем datetime.utcnow() вместо datetime.now(timezone.utc)
            uploaded_at_utc = datetime.utcnow()
            post_media_map_params = []
            for idx, media_file in enumerate(media_files):
                post_media_map_params.append({
                    'post_id': post_id,
                    'file_sha256': media_file.sha256,
                    'position': idx,
                    'role': 'primary',
                    'uploaded_at': uploaded_at_utc
                })
            
            if post_media_map_params:
                # Context7: Проверяем существующие связи (для метрик)
                existing_map_check_sql = text("""
                    SELECT file_sha256 FROM post_media_map 
                    WHERE post_id = :post_id AND file_sha256 IN :sha256_list
                """)
                existing_map_result = await db_session.execute(
                    existing_map_check_sql,
                    {"post_id": post_id, "sha256_list": tuple(mf.sha256 for mf in media_files)}
                )
                existing_map_sha256s = {row[0] for row in existing_map_result.fetchall()}
                
                post_media_map_sql = text("""
                    INSERT INTO post_media_map (
                        post_id, file_sha256, position, role, uploaded_at
                    ) VALUES (
                        :post_id, :file_sha256, :position, :role, :uploaded_at
                    )
                    ON CONFLICT (post_id, file_sha256) DO NOTHING
                """)
                
                result = await db_session.execute(post_media_map_sql, post_media_map_params)
                
                # Context7: Обновляем метрики
                inserted_count = len(media_files) - len(existing_map_sha256s)
                duplicate_count = len(existing_map_sha256s)
                if inserted_count > 0:
                    post_media_map_inserted_total.labels(status='new').inc(inserted_count)
                if duplicate_count > 0:
                    post_media_map_inserted_total.labels(status='duplicate').inc(duplicate_count)
                
                logger.debug(
                    "Saved post_media_map",
                    post_id=post_id,
                    count=len(media_files),
                    inserted=inserted_count,
                    duplicates=duplicate_count,
                    trace_id=trace_id
                )
            
            duration = time.time() - start_time
            cas_operations_latency_seconds.labels(operation='save_media_to_cas').observe(duration)
            
            logger.info(
                "Media saved to CAS",
                post_id=post_id,
                media_count=len(media_files),
                duration_ms=int(duration * 1000),
                trace_id=trace_id
            )
                    
        except Exception as e:
            duration = time.time() - start_time
            cas_operations_latency_seconds.labels(operation='save_media_to_cas').observe(duration)
            cas_operations_errors_total.labels(
                operation='save_media_to_cas',
                error_type=type(e).__name__
            ).inc()
            
            logger.error(
                "Failed to save media to CAS",
                post_id=post_id,
                error=str(e),
                duration_ms=int(duration * 1000),
                trace_id=trace_id,
                exc_info=True
            )
            # Context7: Не прерываем транзакцию - медиа уже в S3, CAS можно восстановить позже
    
    async def _upsert_user(self, db_session: AsyncSession, user_data: Dict[str, Any]) -> None:
        """
        Context7: UPSERT identity + membership (users) через общую утилиту.
        Избегаем дублирования логики с api/routers/users.py.
        """
        try:
            # Подготовка данных пользователя
            # [C7-ID: dev-mode-012] Context7 best practice: соответствие реальной схеме БД
            telegram_id = user_data.get('telegram_id')
            if isinstance(telegram_id, str):
                telegram_id = int(telegram_id)
            elif telegram_id is None:
                raise ValueError("telegram_id is required")
            tenant_id = user_data.get('tenant_id')
            if not tenant_id:
                raise ValueError("tenant_id is required for membership upsert")
            
            # Context7: Используем общую утилиту для избежания дублирования логики
            # Импортируем напрямую из api модуля (монтируется в /opt/telegram-assistant/api)
            import sys
            import os
            import importlib.util
            
            # Context7: Используем importlib для прямого импорта файла, так как обычный импорт не работает
            api_path = '/opt/telegram-assistant/api'
            utils_path = os.path.join(api_path, 'utils', 'identity_membership.py')
            
            if os.path.exists(utils_path):
                spec = importlib.util.spec_from_file_location("identity_membership", utils_path)
                identity_membership_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(identity_membership_module)
                upsert_identity_and_membership_async = identity_membership_module.upsert_identity_and_membership_async
            else:
                # Fallback: попробуем обычный импорт
                if api_path not in sys.path:
                    sys.path.insert(0, api_path)
                from utils.identity_membership import upsert_identity_and_membership_async
            
            identity_id, user_id = await upsert_identity_and_membership_async(
                db_session=db_session,
                tenant_id=str(tenant_id),
                telegram_id=telegram_id,
                username=user_data.get('username'),
                first_name=user_data.get('first_name'),
                last_name=user_data.get('last_name'),
                tier=user_data.get('tier', 'free')
            )
            
            db_users_upserted_total.inc()
            
            self.logger.debug("Membership upserted",
                              tenant_id=str(tenant_id),
                              identity_id=str(identity_id),
                              user_id=str(user_id),
                              telegram_id=telegram_id)
            
        except Exception as e:
            self.logger.error("Failed to upsert user", 
                            user_data=user_data,
                            error=str(e))
            raise
    
    async def _upsert_channel(self, db_session: AsyncSession, channel_data: Dict[str, Any]) -> None:
        """
        Context7: UPSERT канала с ON CONFLICT.
        """
        try:
            # Подготовка данных канала
            # [C7-ID: dev-mode-012] Context7 best practice: соответствие реальной схеме БД
            # Таблица channels имеет: id, tg_channel_id, username, title, is_active, last_message_at, created_at, settings, last_parsed_at
            # НЕТ: updated_at, description, participants_count, is_broadcast, is_megagroup, telegram_id
            # [C7-ID: dev-mode-013] tg_channel_id должен быть int (bigint в БД), не str
            tg_channel_id = channel_data.get('telegram_id')
            if isinstance(tg_channel_id, str):
                tg_channel_id = int(tg_channel_id)
            elif tg_channel_id is None:
                raise ValueError("telegram_id (tg_channel_id) is required for channel")
            
            # Context7: [C7-ID: username-normalization-003] Нормализация username перед сохранением в БД
            # Убираем @ из начала username для единообразия
            username_raw = channel_data.get('username', '')
            username_normalized = username_raw.lstrip('@') if username_raw else ''
            
            channel_record = {
                'id': str(uuid.uuid4()),
                'tg_channel_id': tg_channel_id,  # int для bigint в БД
                'title': channel_data.get('title', ''),
                'username': username_normalized,  # Сохраняем нормализованный username (без @)
                'is_active': True,
                'created_at': datetime.now(timezone.utc)
            }
            
            # UPSERT через PostgreSQL ON CONFLICT (по tg_channel_id, без несуществующих полей)
            sql = """
            INSERT INTO channels (id, tg_channel_id, title, username, is_active, created_at)
            VALUES (:id, :tg_channel_id, :title, :username, :is_active, :created_at)
            ON CONFLICT (tg_channel_id)
            DO UPDATE SET
                title = EXCLUDED.title,
                username = EXCLUDED.username,
                is_active = EXCLUDED.is_active
            """
            
            await db_session.execute(text(sql), channel_record)
            db_channels_upserted_total.inc()
            
            self.logger.debug("Channel upserted", 
                            telegram_id=channel_data.get('telegram_id'))
            
        except Exception as e:
            self.logger.error("Failed to upsert channel", 
                            channel_data=channel_data,
                            error=str(e))
            raise
    
    async def _bulk_insert_posts(self, db_session: AsyncSession, posts_data: List[Dict[str, Any]]) -> int:
        """
        Context7: Эффективный bulk insert через SQLAlchemy Core.
        
        Args:
            db_session: AsyncSession
            posts_data: Список постов для вставки
            
        Returns:
            Количество вставленных строк
        """
        if not posts_data:
            return 0
            
        try:
            # Подготовка данных постов
            prepared_posts = []
            for post in posts_data:
                # [C7-ID: dev-mode-014] Context7 best practice: JSONB сериализация
                # media_urls должен быть JSON строкой или уже JSON строкой для jsonb типа в PostgreSQL
                media_urls = post.get('media_urls', [])
                if isinstance(media_urls, list):
                    # Конвертируем список в JSON строку для jsonb
                    media_urls = json.dumps(media_urls)
                elif media_urls is None:
                    media_urls = '[]'  # Пустой JSON массив по умолчанию
                # Если уже строка - оставляем как есть (может быть уже JSON строка)
                
                prepared_post = {
                    'id': post.get('id') or str(uuid.uuid4()),
                    'channel_id': post.get('channel_id'),
                    'telegram_message_id': post.get('telegram_message_id'),
                    'content': post.get('content', ''),
                    'media_urls': media_urls,
                    'posted_at': post.get('posted_at'),
                    'created_at': datetime.now(timezone.utc),
                    'is_processed': False,
                    'has_media': post.get('has_media', False),
                    'yyyymm': post.get('yyyymm', ''),
                    'views_count': post.get('views_count', 0),
                    'forwards_count': post.get('forwards_count', 0),
                    'reactions_count': post.get('reactions_count', 0),
                    'replies_count': post.get('replies_count', 0),
                    'is_pinned': post.get('is_pinned', False),
                    'is_edited': post.get('is_edited', False),
                    'edited_at': post.get('edited_at'),
                    'post_author': post.get('post_author', ''),
                    'reply_to_message_id': post.get('reply_to_message_id'),
                    'reply_to_chat_id': post.get('reply_to_chat_id'),
                    'via_bot_id': post.get('via_bot_id'),
                    'via_business_bot_id': post.get('via_business_bot_id'),
                    'is_silent': post.get('is_silent', False),
                    'is_legacy': post.get('is_legacy', False),
                    'noforwards': post.get('noforwards', False),
                    'invert_media': post.get('invert_media', False),
                    'telegram_post_url': post.get('telegram_post_url', ''),
                    'grouped_id': post.get('grouped_id')  # Context7: ID альбома для дедупликации
                }
                prepared_posts.append(prepared_post)
            
            # Context7: Bulk insert с ON CONFLICT DO NOTHING
            # Используем правильный подход: передаем список словарей в execute()
            # SQLAlchemy автоматически использует executemany для списка параметров
            # Для PostgreSQL ON CONFLICT используем text() с правильным синтаксисом
            
            sql = text("""
                INSERT INTO posts (
                    id, channel_id, telegram_message_id, content, media_urls,
                    posted_at, created_at, is_processed, has_media, yyyymm,
                    views_count, forwards_count, reactions_count, replies_count,
                    is_pinned, is_edited, edited_at, post_author,
                    reply_to_message_id, reply_to_chat_id, via_bot_id, via_business_bot_id,
                    is_silent, is_legacy, noforwards, invert_media, telegram_post_url, grouped_id
                )
                VALUES (
                    :id, :channel_id, :telegram_message_id, :content, :media_urls,
                    :posted_at, :created_at, :is_processed, :has_media, :yyyymm,
                    :views_count, :forwards_count, :reactions_count, :replies_count,
                    :is_pinned, :is_edited, :edited_at, :post_author,
                    :reply_to_message_id, :reply_to_chat_id, :via_bot_id, :via_business_bot_id,
                    :is_silent, :is_legacy, :noforwards, :invert_media, :telegram_post_url, :grouped_id
                )
                ON CONFLICT (channel_id, telegram_message_id)
                DO NOTHING
            """)
            
            # Context7 best practice: SQLAlchemy автоматически использует executemany
            # при передаче списка словарей во второй аргумент execute()
            # Это правильно обработает bulk insert и даст корректный rowcount
            result = await db_session.execute(sql, prepared_posts)
            
            # Context7: Правильный подсчет вставленных строк
            # result.rowcount возвращает количество действительно вставленных/обновленных строк
            # При ON CONFLICT DO NOTHING это количество новых строк (не конфликтных)
            # Следим: при executemany с PostgreSQL rowcount может быть -1 (не поддерживается)
            inserted_count = result.rowcount if result.rowcount is not None else 0
            
            # Context7: Проверяем на некорректные значения rowcount
            if inserted_count < 0:
                # PostgreSQL/asyncpg может вернуть -1 для bulk операций
                # В этом случае считаем, что все посты были вставлены (оптимистичный подход)
                # Лучше пересчитать через SELECT, но это дорого - используем логику идемпотентности
                self.logger.warning("Invalid rowcount from bulk insert, assuming all inserted",
                                  total_count=len(prepared_posts),
                                  rowcount=result.rowcount)
                inserted_count = len(prepared_posts)
            
            # Если rowcount недоступен (старые версии SQLAlchemy), используем приблизительную оценку
            # Но для bulk insert с executemany это может быть неправильно
            # Поэтому добавляем предупреждение если rowcount не совпадает с ожидаемым
            if inserted_count == 0 and len(prepared_posts) > 0:
                self.logger.warning("No posts inserted despite having data", 
                                  posts_count=len(prepared_posts),
                                  rowcount=result.rowcount)
            elif inserted_count < len(prepared_posts):
                self.logger.debug("Some posts were duplicates and not inserted",
                                total=len(prepared_posts),
                                inserted=inserted_count,
                                duplicates=len(prepared_posts) - inserted_count)
            
            self.logger.info("Posts bulk insert completed", 
                            total_count=len(prepared_posts),
                            inserted_count=inserted_count,
                            rowcount=result.rowcount)
            
            return inserted_count
            
        except Exception as e:
            self.logger.error("Failed to bulk insert posts", 
                            posts_count=len(posts_data),
                            error=str(e))
            raise
    
    def _classify_error(self, error: Exception) -> str:
        """
        Классификация ошибок для метрик.
        
        Args:
            error: Исключение
            
        Returns:
            Строка с типом ошибки
        """
        error_str = str(error).lower()
        
        if 'foreign key' in error_str or 'fk_' in error_str:
            return 'fk_violation'
        elif 'timeout' in error_str:
            return 'timeout'
        elif 'connection' in error_str or 'connect' in error_str:
            return 'connection_error'
        elif 'duplicate key' in error_str or 'unique constraint' in error_str:
            return 'duplicate_key'
        elif 'permission denied' in error_str or 'access denied' in error_str:
            return 'permission_denied'
        else:
            return 'unknown'
    
    async def get_batch_stats(self, db_session: AsyncSession) -> Dict[str, Any]:
        """
        Получение статистики для мониторинга.
        
        Args:
            db_session: AsyncSession
            
        Returns:
            Dict со статистикой
        """
        try:
            # Статистика по постам за последний час
            posts_1h_sql = """
            SELECT COUNT(*) as count
            FROM posts 
            WHERE created_at > NOW() - INTERVAL '1 hour'
            """
            result = await db_session.execute(text(posts_1h_sql))
            posts_1h = result.scalar() or 0
            
            # Статистика по пользователям
            users_sql = "SELECT COUNT(*) as count FROM users"
            result = await db_session.execute(text(users_sql))
            users_total = result.scalar() or 0
            
            # Статистика по каналам
            channels_sql = "SELECT COUNT(*) as count FROM channels"
            result = await db_session.execute(text(channels_sql))
            channels_total = result.scalar() or 0
            
            return {
                "posts_inserted_1h": posts_1h,
                "users_total": users_total,
                "channels_total": channels_total,
                "last_check": datetime.now(timezone.utc).isoformat()
            }
            
        except Exception as e:
            self.logger.error("Failed to get batch stats", error=str(e))
            return {
                "posts_inserted_1h": 0,
                "users_total": 0,
                "channels_total": 0,
                "last_check": datetime.now(timezone.utc).isoformat(),
                "error": str(e)
            }
