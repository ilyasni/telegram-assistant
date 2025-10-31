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
    
    async def _upsert_user(self, db_session: AsyncSession, user_data: Dict[str, Any]) -> None:
        """
        Context7: UPSERT пользователя с ON CONFLICT.
        """
        try:
            # Подготовка данных пользователя
            # [C7-ID: dev-mode-012] Context7 best practice: соответствие реальной схеме БД
            # Таблица users имеет: id, tenant_id, telegram_id, username, created_at, last_active_at, first_name, last_name
            # НЕТ: updated_at
            # [C7-ID: dev-mode-013] telegram_id должен быть int (bigint в БД), не str
            telegram_id = user_data.get('telegram_id')
            if isinstance(telegram_id, str):
                telegram_id = int(telegram_id)
            elif telegram_id is None:
                raise ValueError("telegram_id is required")
            
            user_record = {
                'id': str(uuid.uuid4()),
                'tenant_id': user_data.get('tenant_id') or str(uuid.uuid4()),  # Используем переданный tenant_id или создаем новый
                'telegram_id': telegram_id,  # int для bigint в БД
                'first_name': user_data.get('first_name', ''),
                'last_name': user_data.get('last_name', ''),
                'username': user_data.get('username', ''),
                'created_at': datetime.now(timezone.utc),
                'last_active_at': datetime.now(timezone.utc)
            }
            
            # UPSERT через PostgreSQL ON CONFLICT (без updated_at)
            sql = """
            INSERT INTO users (id, tenant_id, telegram_id, first_name, last_name, username, created_at, last_active_at)
            VALUES (:id, :tenant_id, :telegram_id, :first_name, :last_name, :username, :created_at, :last_active_at)
            ON CONFLICT (telegram_id) 
            DO UPDATE SET
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                username = EXCLUDED.username,
                last_active_at = EXCLUDED.last_active_at
            """
            
            await db_session.execute(text(sql), user_record)
            db_users_upserted_total.inc()
            
            self.logger.debug("User upserted", 
                            telegram_id=user_data.get('telegram_id'))
            
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
            
            channel_record = {
                'id': str(uuid.uuid4()),
                'tg_channel_id': tg_channel_id,  # int для bigint в БД
                'title': channel_data.get('title', ''),
                'username': channel_data.get('username', ''),
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
                    'id': str(uuid.uuid4()),
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
                    'telegram_post_url': post.get('telegram_post_url', '')
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
                    is_silent, is_legacy, noforwards, invert_media, telegram_post_url
                )
                VALUES (
                    :id, :channel_id, :telegram_message_id, :content, :media_urls,
                    :posted_at, :created_at, :is_processed, :has_media, :yyyymm,
                    :views_count, :forwards_count, :reactions_count, :replies_count,
                    :is_pinned, :is_edited, :edited_at, :post_author,
                    :reply_to_message_id, :reply_to_chat_id, :via_bot_id, :via_business_bot_id,
                    :is_silent, :is_legacy, :noforwards, :invert_media, :telegram_post_url
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
            inserted_count = result.rowcount if result.rowcount is not None else 0
            
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
