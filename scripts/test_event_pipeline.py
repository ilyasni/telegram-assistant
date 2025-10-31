#!/usr/bin/env python3
"""
Context7: Скрипт для комплексного тестирования event-driven пайплайна
Проверяет: генерация событий → Redis Streams → PostPersistenceWorker → PostgreSQL
"""

import asyncio
import os
import json
import uuid
import time
from datetime import datetime, timezone, timedelta
import redis.asyncio as redis
import asyncpg
import structlog

logger = structlog.get_logger()

# Конфигурация
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://telegram_user:your_secure_password_here@localhost:5432/postgres")
STREAM_POST_PARSED = "stream:posts:parsed"
CONSUMER_GROUP_POST_PERSISTENCE = "post_persist_workers"

# Тестовые данные
TEST_CHANNEL_ID = 12345
TEST_TELEGRAM_MESSAGE_ID = 98765
TEST_TENANT_ID = "test-tenant-123"
TEST_USER_ID = "test-user-456"

class EventPipelineTester:
    def __init__(self):
        self.redis_client = None
        self.db_conn = None
        self.test_results = {}

    async def setup(self):
        """Инициализация соединений."""
        logger.info("Setting up test connections")
        
        # Redis
        self.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        await self.redis_client.ping()
        logger.info("Redis connection established")
        
        # PostgreSQL
        db_url_for_connect = DATABASE_URL.replace("postgresql+asyncpg", "postgresql")
        self.db_conn = await asyncpg.connect(db_url_for_connect)
        logger.info("PostgreSQL connection established")

    async def cleanup(self):
        """Закрытие соединений."""
        if self.redis_client:
            await self.redis_client.close()
        if self.db_conn:
            await self.db_conn.close()
        logger.info("Connections closed")

    async def test_redis_streams(self) -> bool:
        """Тест 1: Проверка Redis Streams."""
        logger.info("Testing Redis Streams functionality")
        
        try:
            # Проверяем создание consumer group
            try:
                await self.redis_client.xgroup_create(
                    STREAM_POST_PARSED, 
                    CONSUMER_GROUP_POST_PERSISTENCE, 
                    id='0', 
                    mkstream=True
                )
                logger.info("Consumer group created or already exists")
            except Exception as e:
                if "BUSYGROUP" not in str(e):
                    raise
            
            # Генерируем тестовое событие
            post_id = str(uuid.uuid4())
            event_data = self._create_test_event(post_id)
            
            # Публикуем событие
            message_id = await self.redis_client.xadd(
                STREAM_POST_PARSED, 
                event_data, 
                maxlen=100000, 
                approximate=True
            )
            logger.info("Test event published", post_id=post_id, message_id=message_id)
            
            # Проверяем наличие события в стриме
            messages = await self.redis_client.xrevrange(STREAM_POST_PARSED, count=10)
            found = any(data.get('post_id') == post_id for _, data in messages)
            
            if found:
                logger.info("✅ Redis Streams test passed")
                self.test_results['redis_streams'] = True
                return True
            else:
                logger.error("❌ Redis Streams test failed: event not found")
                self.test_results['redis_streams'] = False
                return False
                
        except Exception as e:
            logger.error("❌ Redis Streams test failed", error=str(e))
            self.test_results['redis_streams'] = False
            return False

    async def test_database_persistence(self) -> bool:
        """Тест 2: Проверка персистентности в БД."""
        logger.info("Testing database persistence")
        
        try:
            # Генерируем уникальный post_id для теста
            post_id = str(uuid.uuid4())
            
            # Создаем тестовый пост напрямую в БД (имитируем PostPersistenceWorker)
            test_data = {
                'post_id': post_id,
                'channel_id': str(TEST_CHANNEL_ID),
                'telegram_message_id': TEST_TELEGRAM_MESSAGE_ID,
                'content': f"Test post for persistence check at {datetime.now(timezone.utc).isoformat()}",
                'media_urls': json.dumps(["http://example.com/test.jpg"]),
                'created_at': datetime.now(timezone.utc),
                'is_processed': True,
                'posted_at': datetime.now(timezone.utc),
                'url': "http://example.com/test_post",
                'has_media': True,
                'views_count': 100,
                'forwards_count': 10,
                'reactions_count': 5,
                'replies_count': 2,
                'is_pinned': False,
                'is_edited': False,
                'edited_at': None,
                'post_author': "Test Author",
                'reply_to_message_id': None,
                'reply_to_chat_id': None,
                'via_bot_id': None,
                'via_business_bot_id': None,
                'is_silent': False,
                'is_legacy': False,
                'noforwards': False,
                'invert_media': False,
                'tg_channel_id': TEST_CHANNEL_ID,
                'content_hash': "test_hash_123",
                'urls': json.dumps(["http://example.com/link1"]),
                'link_count': 1,
                'tenant_id': TEST_TENANT_ID,
                'user_id': TEST_USER_ID
            }
            
            # Вставляем пост
            await self.db_conn.execute("""
                INSERT INTO posts (
                    id, channel_id, telegram_message_id, content, media_urls, created_at, is_processed,
                    posted_at, url, has_media, views_count, forwards_count, reactions_count,
                    replies_count, is_pinned, is_edited, edited_at, post_author,
                    reply_to_message_id, reply_to_chat_id, via_bot_id, via_business_bot_id,
                    is_silent, is_legacy, noforwards, invert_media,
                    tg_channel_id, content_hash, urls, link_count, tenant_id, user_id
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18,
                    $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31, $32
                )
                ON CONFLICT (channel_id, telegram_message_id)
                DO UPDATE SET
                    content = EXCLUDED.content,
                    updated_at = NOW()
            """, 
                uuid.UUID(post_id), test_data['channel_id'], test_data['telegram_message_id'],
                test_data['content'], test_data['media_urls'], test_data['created_at'],
                test_data['is_processed'], test_data['posted_at'], test_data['url'],
                test_data['has_media'], test_data['views_count'], test_data['forwards_count'],
                test_data['reactions_count'], test_data['replies_count'], test_data['is_pinned'],
                test_data['is_edited'], test_data['edited_at'], test_data['post_author'],
                test_data['reply_to_message_id'], test_data['reply_to_chat_id'],
                test_data['via_bot_id'], test_data['via_business_bot_id'], test_data['is_silent'],
                test_data['is_legacy'], test_data['noforwards'], test_data['invert_media'],
                test_data['tg_channel_id'], test_data['content_hash'], test_data['urls'],
                test_data['link_count'], test_data['tenant_id'], test_data['user_id']
            )
            
            # Проверяем, что пост сохранился
            result = await self.db_conn.fetchrow(
                "SELECT id, content, tenant_id FROM posts WHERE id = $1",
                uuid.UUID(post_id)
            )
            
            if result:
                logger.info("✅ Database persistence test passed", 
                           post_id=str(result['id']), 
                           tenant_id=result['tenant_id'])
                self.test_results['database_persistence'] = True
                return True
            else:
                logger.error("❌ Database persistence test failed: post not found")
                self.test_results['database_persistence'] = False
                return False
                
        except Exception as e:
            logger.error("❌ Database persistence test failed", error=str(e))
            self.test_results['database_persistence'] = False
            return False

    async def test_end_to_end_pipeline(self) -> bool:
        """Тест 3: Сквозной тест пайплайна."""
        logger.info("Testing end-to-end pipeline")
        
        try:
            # Генерируем событие
            post_id = str(uuid.uuid4())
            event_data = self._create_test_event(post_id)
            
            # Публикуем в Redis Stream
            message_id = await self.redis_client.xadd(
                STREAM_POST_PARSED, 
                event_data, 
                maxlen=100000, 
                approximate=True
            )
            logger.info("Event published to Redis Stream", post_id=post_id, message_id=message_id)
            
            # Ждем обработки (симулируем работу PostPersistenceWorker)
            logger.info("Waiting for PostPersistenceWorker to process event (10 seconds)...")
            await asyncio.sleep(10)
            
            # Проверяем, что пост появился в БД
            result = await self.db_conn.fetchrow(
                "SELECT id, content, tenant_id FROM posts WHERE id = $1",
                uuid.UUID(post_id)
            )
            
            if result:
                logger.info("✅ End-to-end pipeline test passed", 
                           post_id=str(result['id']), 
                           tenant_id=result['tenant_id'])
                self.test_results['end_to_end'] = True
                return True
            else:
                logger.warning("⚠️ End-to-end pipeline test inconclusive: PostPersistenceWorker may not be running")
                self.test_results['end_to_end'] = False
                return False
                
        except Exception as e:
            logger.error("❌ End-to-end pipeline test failed", error=str(e))
            self.test_results['end_to_end'] = False
            return False

    def _create_test_event(self, post_id: str) -> dict:
        """Создает тестовое событие для Redis Stream."""
        return {
            'post_id': post_id,
            'channel_id': str(TEST_CHANNEL_ID),
            'telegram_message_id': str(TEST_TELEGRAM_MESSAGE_ID),
            'content': f"Test post content at {datetime.now(timezone.utc).isoformat()}",
            'media_urls': json.dumps(["http://example.com/media1.jpg"]),
            'created_at': datetime.now(timezone.utc).isoformat() + 'Z',
            'is_processed': 'true',
            'posted_at': datetime.now(timezone.utc).isoformat() + 'Z',
            'url': "http://example.com/test_post",
            'has_media': 'true',
            'views_count': '100',
            'forwards_count': '10',
            'reactions_count': '5',
            'replies_count': '2',
            'is_pinned': 'false',
            'is_edited': 'false',
            'edited_at': '',
            'post_author': "Test Author",
            'reply_to_message_id': '',
            'reply_to_chat_id': '',
            'via_bot_id': '',
            'via_business_bot_id': '',
            'is_silent': 'false',
            'is_legacy': 'false',
            'noforwards': 'false',
            'invert_media': 'false',
            'tg_channel_id': str(TEST_CHANNEL_ID),
            'content_hash': "test_hash_123",
            'urls': json.dumps(["http://example.com/link1"]),
            'link_count': '1',
            'tenant_id': TEST_TENANT_ID,
            'user_id': TEST_USER_ID
        }

    async def run_all_tests(self):
        """Запускает все тесты."""
        logger.info("Starting Event Pipeline Test Suite")
        
        await self.setup()
        
        try:
            # Тест 1: Redis Streams
            await self.test_redis_streams()
            
            # Тест 2: Database Persistence
            await self.test_database_persistence()
            
            # Тест 3: End-to-end Pipeline
            await self.test_end_to_end_pipeline()
            
            # Результаты
            self.print_results()
            
        finally:
            await self.cleanup()

    def print_results(self):
        """Выводит результаты тестов."""
        logger.info("=" * 50)
        logger.info("EVENT PIPELINE TEST RESULTS")
        logger.info("=" * 50)
        
        total_tests = len(self.test_results)
        passed_tests = sum(1 for result in self.test_results.values() if result)
        
        for test_name, result in self.test_results.items():
            status = "✅ PASS" if result else "❌ FAIL"
            logger.info(f"{test_name}: {status}")
        
        logger.info("-" * 50)
        logger.info(f"Total: {passed_tests}/{total_tests} tests passed")
        
        if passed_tests == total_tests:
            logger.info("🎉 All tests passed! Event pipeline is working correctly.")
        else:
            logger.warning("⚠️ Some tests failed. Check the logs above for details.")

async def main():
    """Основная функция."""
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.dict_tracebacks,
            structlog.dev.ConsoleRenderer()
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    tester = EventPipelineTester()
    await tester.run_all_tests()

if __name__ == "__main__":
    asyncio.run(main())
