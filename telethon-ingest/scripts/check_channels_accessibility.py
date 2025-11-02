#!/usr/bin/env python3
"""
Проверка доступности всех каналов из БД для парсинга.

Проверяет, что каждый канал в БД доступен через Telegram API.
Выводит список доступных и недоступных каналов с причинами недоступности.
"""
import asyncio
import sys
import os
from typing import List, Dict, Optional, Tuple

# Добавляем пути для импортов
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'telethon-ingest'))

import psycopg2
from psycopg2.extras import RealDictCursor
import structlog
from telethon import TelegramClient, errors
from telethon.sessions import StringSession

# Попытка импорта config
try:
    from config import settings
except ImportError:
    # Если config недоступен, используем переменные окружения
    class Settings:
        database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
        master_api_id = int(os.getenv("MASTER_API_ID", "0"))
        master_api_hash = os.getenv("MASTER_API_HASH", "")
    settings = Settings()

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


async def get_telegram_client() -> Optional[TelegramClient]:
    """
    Инициализация Telegram клиента через TelegramClientManager.
    
    Returns:
        TelegramClient или None если не удалось инициализировать
    """
    try:
        import redis.asyncio as redis
        import psycopg2
        
        # Инициализация Redis и БД
        redis_client = redis.from_url(settings.redis_url)
        db_connection = psycopg2.connect(settings.database_url)
        
        # Импортируем TelegramClientManager
        from services.telegram_client_manager import TelegramClientManager
        
        # Создаём менеджер
        client_manager = TelegramClientManager(redis_client, db_connection)
        
        # Получаем telegram_id из БД (любого пользователя с авторизованной сессией)
        cursor = db_connection.cursor()
        cursor.execute("""
            SELECT telegram_id 
            FROM users 
            WHERE telegram_id IS NOT NULL 
            ORDER BY telegram_auth_created_at DESC 
            LIMIT 1
        """)
        result = cursor.fetchone()
        cursor.close()
        
        if not result:
            logger.error("No telegram_id found in users table")
            db_connection.close()
            await redis_client.close()
            return None
        
        telegram_id = result[0]
        logger.info("Using telegram_id for client", telegram_id=telegram_id)
        
        # Получаем клиент через менеджер
        client = await client_manager.get_client(telegram_id)
        
        if not client:
            logger.error("Failed to get Telegram client from manager")
            db_connection.close()
            await redis_client.close()
            return None
        
        logger.info("Telegram client obtained successfully")
        return client
        
    except Exception as e:
        logger.error("Failed to initialize Telegram client", error=str(e), exc_info=True)
        return None


async def check_channel_accessibility(
    client: TelegramClient,
    channel_id: str,
    username: Optional[str],
    tg_channel_id: Optional[int],
    title: str
) -> Tuple[bool, Optional[str]]:
    """
    Проверка доступности канала.
    
    Args:
        client: TelegramClient
        channel_id: UUID канала в БД
        username: Username канала (может быть None)
        tg_channel_id: Telegram ID канала (может быть None)
        title: Название канала
        
    Returns:
        Tuple (is_accessible: bool, error_message: Optional[str])
    """
    try:
        # Пытаемся получить entity канала
        entity = None
        
        if username:
            # Приоритет: username (более надёжный способ)
            clean_username = username.lstrip('@')
            try:
                entity = await client.get_entity(clean_username)
            except errors.UsernameNotOccupiedError:
                return False, "Username not occupied"
            except errors.UsernameInvalidError:
                return False, "Username invalid"
        elif tg_channel_id:
            # Fallback: tg_channel_id
            try:
                entity = await client.get_entity(int(tg_channel_id))
            except errors.ChannelInvalidError:
                return False, "Channel ID invalid"
        else:
            return False, "No username or tg_channel_id"
        
        if entity:
            # Дополнительная проверка: пытаемся получить информацию о канале
            _ = await client.get_messages(entity, limit=1)
            return True, None
        
        return False, "Failed to get entity"
        
    except errors.ChannelPrivateError:
        return False, "Channel is private"
    except errors.ChannelInvalidError:
        return False, "Channel invalid"
    except errors.UsernameNotOccupiedError:
        return False, "Username not occupied"
    except errors.UsernameInvalidError:
        return False, "Username invalid"
    except errors.FloodWaitError as e:
        return False, f"FloodWait: {e.seconds}s"
    except Exception as e:
        return False, f"Error: {str(e)}"


async def check_all_channels(
    only_active: bool = False,
    update_inactive: bool = False
) -> Dict[str, List[Dict]]:
    """
    Проверка всех каналов из БД.
    
    Args:
        only_active: Проверять только активные каналы
        update_inactive: Обновить is_active=False для недоступных каналов
        
    Returns:
        Dict с ключами 'accessible' и 'inaccessible', содержащими списки каналов
    """
    logger.info("Starting channel accessibility check", only_active=only_active)
    
    # Подключение к БД
    conn = psycopg2.connect(settings.database_url)
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Получаем все каналы
        query = """
            SELECT id, tg_channel_id, username, title, is_active
            FROM channels
        """
        if only_active:
            query += " WHERE is_active = true"
        query += " ORDER BY created_at DESC"
        
        cursor.execute(query)
        channels = cursor.fetchall()
        
        logger.info(f"Found {len(channels)} channels to check")
        
        if not channels:
            logger.info("No channels to process")
            return {"accessible": [], "inaccessible": []}
        
        # Инициализация Telegram клиента
        client = await get_telegram_client()
        if not client:
            logger.error("Failed to initialize Telegram client")
            return {"accessible": [], "inaccessible": []}
        
        # Проверяем каждый канал
        accessible = []
        inaccessible = []
        
        for channel in channels:
            logger.info("Checking channel",
                       channel_id=channel['id'],
                       username=channel['username'],
                       title=channel['title'])
            
            is_accessible, error_msg = await check_channel_accessibility(
                client=client,
                channel_id=str(channel['id']),
                username=channel['username'],
                tg_channel_id=channel['tg_channel_id'],
                title=channel['title']
            )
            
            channel_info = {
                "id": str(channel['id']),
                "username": channel['username'],
                "title": channel['title'],
                "tg_channel_id": channel['tg_channel_id'],
                "is_active": channel['is_active']
            }
            
            if is_accessible:
                accessible.append(channel_info)
                logger.info("Channel is accessible",
                           channel_id=channel['id'],
                           username=channel['username'])
            else:
                channel_info["error"] = error_msg
                inaccessible.append(channel_info)
                logger.warning("Channel is inaccessible",
                             channel_id=channel['id'],
                             username=channel['username'],
                             error=error_msg)
                
                # Обновляем is_active=False если требуется
                if update_inactive and channel['is_active']:
                    cursor.execute(
                        "UPDATE channels SET is_active = false WHERE id = %s",
                        (channel['id'],)
                    )
                    conn.commit()
                    logger.info("Updated channel is_active to false",
                               channel_id=channel['id'])
            
            # Небольшая задержка между запросами
            await asyncio.sleep(0.5)
        
        # Закрываем клиент (если он подключён)
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        
        logger.info("Channel accessibility check completed",
                   accessible=len(accessible),
                   inaccessible=len(inaccessible))
        
        return {
            "accessible": accessible,
            "inaccessible": inaccessible
        }
        
    except Exception as e:
        logger.error("Error in check_all_channels", error=str(e))
        raise
    finally:
        cursor.close()
        conn.close()


def print_results(results: Dict[str, List[Dict]]):
    """Вывод результатов проверки в консоль."""
    accessible = results["accessible"]
    inaccessible = results["inaccessible"]
    
    print("\n" + "=" * 80)
    print("РЕЗУЛЬТАТЫ ПРОВЕРКИ ДОСТУПНОСТИ КАНАЛОВ")
    print("=" * 80)
    print(f"\nВсего проверено: {len(accessible) + len(inaccessible)}")
    print(f"✅ Доступных: {len(accessible)}")
    print(f"❌ Недоступных: {len(inaccessible)}")
    
    if accessible:
        print("\n" + "-" * 80)
        print("ДОСТУПНЫЕ КАНАЛЫ:")
        print("-" * 80)
        for channel in accessible:
            print(f"  ✅ {channel['title']} (@{channel['username'] or 'N/A'})")
            print(f"     ID: {channel['id']}, tg_channel_id: {channel['tg_channel_id']}")
    
    if inaccessible:
        print("\n" + "-" * 80)
        print("НЕДОСТУПНЫЕ КАНАЛЫ:")
        print("-" * 80)
        for channel in inaccessible:
            print(f"  ❌ {channel['title']} (@{channel['username'] or 'N/A'})")
            print(f"     ID: {channel['id']}, tg_channel_id: {channel['tg_channel_id']}")
            print(f"     Ошибка: {channel.get('error', 'Unknown')}")
            if channel['is_active']:
                print(f"     ⚠️  Канал помечен как активный, но недоступен!")
    
    print("\n" + "=" * 80)


async def main():
    """Главная функция."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Проверка доступности всех каналов из БД для парсинга"
    )
    parser.add_argument(
        "--only-active",
        action="store_true",
        help="Проверять только активные каналы"
    )
    parser.add_argument(
        "--update-inactive",
        action="store_true",
        help="Обновить is_active=False для недоступных каналов"
    )
    args = parser.parse_args()
    
    try:
        results = await check_all_channels(
            only_active=args.only_active,
            update_inactive=args.update_inactive
        )
        
        print_results(results)
        
        # Возвращаем код выхода: 0 если все доступны, 1 если есть недоступные
        exit_code = 0 if len(results["inaccessible"]) == 0 else 1
        sys.exit(exit_code)
        
    except Exception as e:
        logger.error("Fatal error", error=str(e))
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())

