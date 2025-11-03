#!/usr/bin/env python3
"""
Context7 best practice: Manual channel parsing script for one-off operations.
Используется для ручного запуска парсинга конкретного канала.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

# Context7: Добавляем пути к проекту в начало sys.path
sys.path.insert(0, '/opt/telegram-assistant')
sys.path.insert(0, '/app')

import structlog
import redis.asyncio as redis
import redis as redis_sync
import psycopg2
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text

# Context7: Импорты для инициализации ChannelParser
try:
    # Для запуска из worker контейнера
    from services.channel_parser import ChannelParser, ParserConfig
    from services.telegram_client_manager import TelegramClientManager
    from services.atomic_db_saver import AtomicDBSaver
    from services.rate_limiter import RateLimiter
    from services.media_processor import MediaProcessor
except ImportError:
    # Для запуска из telethon-ingest контейнера
    sys.path.insert(0, '/opt/telegram-assistant/telethon-ingest')
    from services.channel_parser import ChannelParser, ParserConfig
    from services.telegram_client_manager import TelegramClientManager
    from services.atomic_db_saver import AtomicDBSaver
    from services.rate_limiter import RateLimiter
    from services.media_processor import MediaProcessor

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()


async def manual_parse_channel(
    username: str,
    mode: str = "incremental"
):
    """
    Context7 best practice: Ручной парсинг канала с полной инициализацией всех компонентов.
    
    Args:
        username: Username канала (например, 'designsniper')
        mode: Режим парсинга ('historical' или 'incremental')
    """
    print(f"=" * 80)
    print(f"🔄 MANUAL CHANNEL PARSING - Context7 Best Practices")
    print(f"=" * 80)
    print(f"Channel: @{username}")
    print(f"Mode: {mode}")
    print(f"=" * 80)
    
    # Context7: Конфигурация из environment variables
    database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    
    # Context7: Конвертация database_url для asyncpg
    # Убираем connect_timeout и другие параметры, которые asyncpg не поддерживает
    db_url_async = database_url.replace("postgresql://", "postgresql+asyncpg://")
    # Удаляем параметры запроса, которые могут вызвать проблемы с asyncpg
    if '?' in db_url_async:
        db_url_async = db_url_async.split('?')[0]
    
    # Context7: Инициализация БД с таймаутами и pool settings
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "server_settings": {
                "application_name": "manual_channel_parser"
            }
        }
    )
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    db_session = async_session_factory()
    
    # Context7: Инициализация Redis клиента
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    try:
        # Context7: Получение channel_id из БД
        result = await db_session.execute(
            text("""
                SELECT 
                    c.id as channel_id,
                    c.tg_channel_id,
                    c.last_parsed_at
                FROM channels c
                WHERE LTRIM(c.username, '@') = LTRIM(:username, '@') AND c.is_active = true
            """),
            {"username": username}
        )
        channel_row = result.fetchone()
        
        if not channel_row:
            print(f"❌ Канал @{username} не найден или неактивен")
            return
        
        channel_id = str(channel_row.channel_id)
        tg_channel_id = channel_row.tg_channel_id
        
        # Context7: Получение tenant_id из таблицы users (как в ParseAllChannelsTask)
        user_result = await db_session.execute(
            text("""
                SELECT telegram_id, tenant_id
                FROM users
                WHERE telegram_auth_status = 'authorized' AND telegram_id IS NOT NULL
                ORDER BY telegram_auth_created_at DESC
                LIMIT 1
            """)
        )
        user_row = user_result.fetchone()
        if user_row:
            tenant_id = str(user_row.tenant_id)
        else:
            # Fallback: используем default tenant_id
            tenant_id = os.getenv("S3_DEFAULT_TENANT_ID", "default")
        
        print(f"\n✅ Канал найден:")
        print(f"   channel_id: {channel_id}")
        print(f"   tenant_id: {tenant_id}")
        print(f"   telegram_channel_id: {tg_channel_id}")
        print(f"   last_parsed_at: {channel_row.last_parsed_at}")
        
        # Context7: Инициализация TelegramClientManager (требует async Redis и sync БД)
        print(f"\n🔄 Инициализация TelegramClientManager...")
        redis_client_for_manager = redis.from_url(redis_url, decode_responses=False)
        db_connection_sync = psycopg2.connect(database_url.replace("postgresql+asyncpg://", "postgresql://").split('?')[0])
        client_manager = TelegramClientManager(redis_client_for_manager, db_connection_sync)
        
        # Context7: Инициализация MediaProcessor
        # NOTE: MediaProcessor уже импортирован и инициализирует S3 и StorageQuota внутри
        print(f"🔄 Инициализация MediaProcessor...")
        s3_endpoint = os.getenv("S3_ENDPOINT_URL", "https://s3.cloud.ru")
        s3_bucket = os.getenv("S3_BUCKET_NAME", "")
        s3_access_key = os.getenv("S3_ACCESS_KEY_ID", "")
        s3_secret_key = os.getenv("S3_SECRET_ACCESS_KEY", "")
        s3_region = os.getenv("S3_REGION", "ru-central-1")
        
        media_processor = None
        # Context7: MediaProcessor уже импортирован и загрузил все необходимые модули
        # Используем sys.modules для получения уже импортированных классов
        try:
            # Получаем уже импортированные классы из sys.modules
            # чтобы избежать повторной регистрации метрик Prometheus
            if 'api.services.s3_storage' in sys.modules:
                S3StorageService = sys.modules['api.services.s3_storage'].S3StorageService
            else:
                # Если модуль ещё не импортирован, добавляем пути и импортируем
                if '/opt/telegram-assistant/api' not in sys.path:
                    sys.path.insert(0, '/opt/telegram-assistant/api')
                from api.services.s3_storage import S3StorageService
            
            if 'worker.services.storage_quota' in sys.modules:
                StorageQuotaService = sys.modules['worker.services.storage_quota'].StorageQuotaService
            else:
                # Если модуль ещё не импортирован, добавляем пути и импортируем
                if '/opt/telegram-assistant/worker' not in sys.path:
                    sys.path.insert(0, '/opt/telegram-assistant/worker')
                from worker.services.storage_quota import StorageQuotaService
            
            if s3_endpoint and s3_bucket and s3_access_key and s3_secret_key:
                s3_service = S3StorageService(
                    endpoint_url=s3_endpoint,
                    access_key_id=s3_access_key,
                    secret_access_key=s3_secret_key,
                    bucket_name=s3_bucket,
                    region=s3_region
                )
                storage_quota = StorageQuotaService(s3_service)
                
                # Context7: Redis без decode_responses для совместимости с MediaProcessor
                redis_for_media = redis.from_url(redis_url, decode_responses=False)
                media_processor = MediaProcessor(
                    telegram_client=None,  # Будет обновлён при обработке
                    s3_service=s3_service,
                    storage_quota=storage_quota,
                    redis_client=redis_for_media
                )
                print(f"   ✅ MediaProcessor инициализирован")
            else:
                print(f"   ⚠️  MediaProcessor не инициализирован (нет S3 credentials)")
        except (ImportError, AttributeError) as e:
            print(f"   ⚠️  Не удалось импортировать S3 сервисы: {e}")
            print("   Продолжаем без MediaProcessor - медиа обработано не будет")
        except Exception as e:
            print(f"   ⚠️  MediaProcessor не инициализирован (ошибка): {e}")
        
        # Context7: Инициализация RateLimiter
        rate_limiter = RateLimiter(redis_client)
        
        # Context7: Инициализация ParserConfig
        config = ParserConfig()
        
        # Context7: Инициализация ChannelParser со всеми компонентами
        print(f"\n🔄 Инициализация ChannelParser...")
        parser = ChannelParser(
            config=config,
            db_session=db_session,
            event_publisher=None,  # Temporarily disabled
            redis_client=redis_client,
            atomic_saver=AtomicDBSaver(),
            rate_limiter=rate_limiter,
            telegram_client_manager=client_manager,
            media_processor=media_processor
        )
        print(f"   ✅ ChannelParser инициализирован")
        
        # Context7: Определение user_id - используем telegram_id из users
        user_id = str(user_row.telegram_id)
        
        # Context7: Запуск парсинга канала
        print(f"\n🚀 Запуск парсинга канала @{username}...")
        print(f"   channel_id: {channel_id}")
        print(f"   user_id: {user_id}")
        print(f"   tenant_id: {tenant_id}")
        print(f"   mode: {mode}")
        
        start_time = datetime.now(timezone.utc)
        
        result = await parser.parse_channel_messages(
            channel_id=channel_id,
            user_id=user_id,
            tenant_id=tenant_id,
            mode=mode
        )
        
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        
        # Context7: Вывод результатов парсинга
        print(f"\n{'=' * 80}")
        print(f"✅ ПАРСИНГ ЗАВЕРШЁН")
        print(f"{'=' * 80}")
        print(f"Обработано постов: {result.get('processed', 0)}")
        print(f"Пропущено постов: {result.get('skipped', 0)}")
        print(f"Max message date: {result.get('max_date')}")
        print(f"Duration: {duration:.2f}s")
        print(f"{'=' * 80}")
        
        # Context7: Проверка медиа в последних постах
        if result.get('processed', 0) > 0:
            print(f"\n🔍 Проверка обработки медиа...")
            check_result = await db_session.execute(
                text("""
                    SELECT 
                        p.telegram_message_id,
                        p.has_media,
                        COUNT(pmm.file_sha256) as media_count_in_cas
                    FROM posts p
                    LEFT JOIN post_media_map pmm ON pmm.post_id = p.id
                    WHERE p.channel_id = :channel_id
                      AND p.created_at > NOW() - INTERVAL '5 minutes'
                    GROUP BY p.id, p.telegram_message_id, p.has_media
                    ORDER BY p.created_at DESC
                    LIMIT 5
                """),
                {"channel_id": channel_id}
            )
            
            check_rows = check_result.fetchall()
            if check_rows:
                print(f"   Последние {len(check_rows)} поста(ов):")
                for row in check_rows:
                    status = "✅" if not row.has_media or row.media_count_in_cas > 0 else "⚠️ "
                    print(f"   {status} post_id={row.telegram_message_id}, has_media={row.has_media}, media_in_cas={row.media_count_in_cas}")
            else:
                print(f"   ⚠️  Новые посты не найдены")
        
        # Context7: Очистка ресурсов
        try:
            await parser.close()
        except AttributeError:
            pass  # parser.close() не существует
        try:
            await client_manager.close()
        except AttributeError:
            pass  # client_manager.close() не существует
        
        print(f"\n✅ Все ресурсы освобождены")
        print(f"=" * 80)
        
    except Exception as e:
        logger.error(
            "Manual parsing failed",
            username=username,
            error=str(e),
            exc_info=True
        )
        print(f"\n❌ Ошибка при парсинге: {e}")
        raise
    finally:
        await db_session.close()
        await redis_client.aclose()
        await engine.dispose()
        logger.info("Manual parsing script finished")


async def main():
    """Главная функция для запуска из командной строки."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Context7 best practice: Manual channel parsing script"
    )
    parser.add_argument(
        "--username",
        type=str,
        required=True,
        help="Username канала (например, 'designsniper')"
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["historical", "incremental"],
        default="incremental",
        help="Режим парсинга (default: incremental)"
    )
    
    args = parser.parse_args()
    
    await manual_parse_channel(
        username=args.username,
        mode=args.mode
    )


if __name__ == "__main__":
    asyncio.run(main())
