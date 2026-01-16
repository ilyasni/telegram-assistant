#!/usr/bin/env python3
"""
Скрипт для обновления неверных tg_channel_id для заблокированных каналов.

Context7: Проверяет заблокированные каналы, пытается получить entity по username,
и обновляет tg_channel_id если он неверный.
"""

import sys
import asyncio
import os
import asyncpg
from pathlib import Path

# Добавляем пути
PROJECT_ROOT = Path("/opt/telegram-assistant")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "telethon-ingest"))

from telethon import TelegramClient, errors
from telethon.tl.types import PeerChannel
from telethon import utils
import structlog

logger = structlog.get_logger()

# Цвета для вывода
class Colors:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'

def print_success(text: str):
    print(f"{Colors.GREEN}✅ {text}{Colors.RESET}")

def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠️  {text}{Colors.RESET}")

def print_error(text: str):
    print(f"{Colors.RED}❌ {text}{Colors.RESET}")

def print_info(text: str):
    print(f"{Colors.BLUE}ℹ️  {text}{Colors.RESET}")


async def update_invalid_tg_channel_ids():
    """Обновление неверных tg_channel_id для заблокированных каналов."""
    # Получаем параметры из переменных окружения
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    session_path = os.getenv("TELEGRAM_SESSION_PATH", "/app/sessions/default.session")
    
    if api_id == 0 or not api_hash:
        print_error("TELEGRAM_API_ID и TELEGRAM_API_HASH должны быть установлены")
        sys.exit(1)
    
    print_info(f"Подключение к БД: {db_url.split('@')[1] if '@' in db_url else 'unknown'}")
    print_info(f"API ID: {api_id}")
    print_info(f"Session: {session_path}")
    print()
    
    # Подключение к БД
    conn = await asyncpg.connect(db_url)
    
    # Получаем каналы с неверным tg_channel_id (заблокированные)
    query = """
    SELECT 
        id,
        username,
        title,
        tg_channel_id,
        blocked_until
    FROM channels
    WHERE is_active = true
        AND tg_channel_id IS NOT NULL
        AND blocked_until IS NOT NULL
        AND blocked_until > NOW()
    ORDER BY blocked_until ASC
    LIMIT 50;
    """
    
    channels = await conn.fetch(query)
    print_info(f"Найдено заблокированных каналов: {len(channels)}")
    print()
    
    if len(channels) == 0:
        print_success("Нет заблокированных каналов для обновления")
        await conn.close()
        return
    
    # Подключение к Telegram
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    
    if not await client.is_user_authorized():
        print_error("Telegram клиент не авторизован")
        await client.disconnect()
        await conn.close()
        return
    
    stats = {'total': len(channels), 'valid': 0, 'invalid': 0, 'updated': 0, 'errors': 0}
    
    try:
        for i, channel in enumerate(channels, 1):
            channel_id = channel['id']
            username = channel['username']
            tg_channel_id_db = channel['tg_channel_id']
            title = channel['title']
            
            print_info(f"[{i}/{len(channels)}] Проверка: {username or title} (tg_id: {tg_channel_id_db})")
            
            # Пытаемся получить entity по tg_channel_id
            try:
                entity = await client.get_entity(int(tg_channel_id_db))
                print_success(f"  tg_channel_id валидный")
                stats['valid'] += 1
                # Снимаем блокировку
                await conn.execute(
                    "UPDATE channels SET blocked_until = NULL WHERE id = $1",
                    channel_id
                )
                print_success(f"  Блокировка снята")
                continue
            except Exception as e:
                error_str = str(e).lower()
                is_entity_not_found = "could not find the input entity" in error_str
                
                if is_entity_not_found:
                    print_warning(f"  tg_channel_id неверный: {str(e)[:50]}")
                    stats['invalid'] += 1
                    
                    # Пытаемся получить entity по username
                    if username:
                        try:
                            clean_username = username.lstrip('@')
                            entity = await client.get_entity(clean_username)
                            
                            # Получаем правильный tg_channel_id
                            if hasattr(entity, 'id') and entity.id is not None:
                                if hasattr(entity, 'broadcast') or hasattr(entity, 'megagroup'):
                                    new_tg_channel_id = utils.get_peer_id(PeerChannel(entity.id))
                                else:
                                    new_tg_channel_id = entity.id
                                
                                print_success(f"  Найден правильный tg_channel_id: {new_tg_channel_id}")
                                
                                # Обновляем в БД
                                await conn.execute(
                                    "UPDATE channels SET tg_channel_id = $1, blocked_until = NULL WHERE id = $2",
                                    new_tg_channel_id,
                                    channel_id
                                )
                                print_success(f"  Обновлен в БД и блокировка снята")
                                stats['updated'] += 1
                            else:
                                print_error(f"  Entity не имеет валидного ID")
                                stats['errors'] += 1
                                
                        except errors.FloodWaitError as e:
                            print_error(f"  FloodWait при получении по username: {e.seconds} секунд")
                            print_warning(f"  Пропускаем из-за FloodWait")
                            stats['errors'] += 1
                            # Ждем FloodWait (но не больше 60 секунд)
                            wait_time = min(e.seconds, 60)
                            print_info(f"  Ожидание {wait_time} секунд...")
                            await asyncio.sleep(wait_time)
                            
                        except Exception as e2:
                            print_error(f"  Ошибка при получении по username: {str(e2)[:50]}")
                            stats['errors'] += 1
                    else:
                        print_warning(f"  Нет username для fallback")
                        stats['errors'] += 1
                else:
                    print_error(f"  Другая ошибка: {str(e)[:50]}")
                    stats['errors'] += 1
            
            print()
    
    finally:
        await client.disconnect()
        await conn.close()
    
    # Итоговая статистика
    print()
    print_info("=== ИТОГОВАЯ СТАТИСТИКА ===")
    print(f"  Всего проверено: {stats['total']}")
    print(f"  Валидных (блокировка снята): {stats['valid']}")
    print(f"  Неверных: {stats['invalid']}")
    print(f"  Обновлено: {stats['updated']}")
    print(f"  Ошибок: {stats['errors']}")


if __name__ == "__main__":
    asyncio.run(update_invalid_tg_channel_ids())
