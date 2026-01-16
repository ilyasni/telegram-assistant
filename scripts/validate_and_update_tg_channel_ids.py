#!/usr/bin/env python3
"""
Скрипт для проверки и обновления неверных tg_channel_id.

Context7: Проверяет все каналы с tg_channel_id, пытается получить entity,
и обновляет tg_channel_id если он неверный.
"""

import os
import sys
import asyncio
import asyncpg
import random
import time
import redis.asyncio as redis
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

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


async def check_global_floodwait(redis_client, session_id: str) -> Optional[float]:
    """
    Проверка глобального FloodWait для сессии.
    
    Returns:
        Оставшееся время в секундах или None если нет блокировки
    """
    if not redis_client:
        return None
    
    key = f"tg:floodwait_until:{session_id}"
    try:
        unlock_time_str = await redis_client.get(key)
        if unlock_time_str:
            unlock_time = float(unlock_time_str)
            wait_time = unlock_time - time.time()
            if wait_time > 0:
                return wait_time
    except Exception:
        pass
    return None


async def validate_and_update_tg_channel_ids(
    db_url: str,
    api_id: int,
    api_hash: str,
    session_path: str,
    dry_run: bool = True,
    limit: int = 5
):
    """
    Проверка и обновление неверных tg_channel_id.
    
    Context7: Circuit breaker при большом FloodWait, ограничение параллелизма,
    пакетность и кэш для предотвращения перегрузки Telegram API.
    
    Args:
        db_url: PostgreSQL connection string
        api_id: Telegram API ID
        api_hash: Telegram API hash
        session_path: Path to Telegram session file
        dry_run: If True, only check without updating
        limit: Лимит каналов для обработки за запуск (по умолчанию 5)
    """
    # Подключение к Redis для проверки глобального FloodWait
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    redis_client = None
    try:
        redis_client = redis.from_url(redis_url, decode_responses=True)
    except Exception as e:
        print_warning(f"Не удалось подключиться к Redis: {e}")
    
    # Подключение к БД
    conn = await asyncpg.connect(db_url)
    
    # Получаем каналы с неверным tg_channel_id (заблокированные) или без недавнего резолва
    query = """
    SELECT 
        id,
        username,
        title,
        tg_channel_id,
        blocked_until,
        last_parsed_at
    FROM channels
    WHERE is_active = true
        AND tg_channel_id IS NOT NULL
        AND (
            (blocked_until IS NOT NULL AND blocked_until > NOW())
            OR (last_parsed_at IS NULL OR last_parsed_at < NOW() - INTERVAL '24 hours')
        )
    ORDER BY blocked_until ASC NULLS LAST, last_parsed_at DESC NULLS FIRST
    LIMIT $1;
    """
    
    channels = await conn.fetch(query, limit)
    print_info(f"Найдено каналов для проверки: {len(channels)} (лимит: {limit})")
    print()
    
    if len(channels) == 0:
        print_success("Нет каналов для проверки")
        await conn.close()
        if redis_client:
            await redis_client.close()
        return
    
    # Подключение к Telegram
    client = TelegramClient(session_path, api_id, api_hash)
    await client.connect()
    
    if not await client.is_user_authorized():
        print_error("Telegram клиент не авторизован")
        await client.disconnect()
        await conn.close()
        if redis_client:
            await redis_client.close()
        return
    
    # Получаем session_id для проверки глобального FloodWait
    session_id = "default"
    try:
        if client.is_connected() and await client.is_user_authorized():
            me = await client.get_me()
            if me and hasattr(me, 'id'):
                session_id = str(me.id)
    except Exception:
        pass
    
    # Проверка глобального FloodWait перед началом
    global_wait = await check_global_floodwait(redis_client, session_id)
    if global_wait and global_wait > 0:
        wait_until = datetime.now(timezone.utc) + timedelta(seconds=global_wait)
        print_error(f"Global FloodWait active. Resume after: {wait_until.isoformat()}")
        await client.disconnect()
        await conn.close()
        if redis_client:
            await redis_client.close()
        return
    
    stats = {
        'total': len(channels),
        'valid': 0,
        'invalid': 0,
        'updated': 0,
        'errors': 0,
        'skipped': 0
    }
    
    try:
        for i, channel in enumerate(channels, 1):
            channel_id = channel['id']
            username = channel['username']
            tg_channel_id_db = channel['tg_channel_id']
            title = channel['title']
            
            print_info(f"[{i}/{len(channels)}] Проверка: {username or title} (tg_id: {tg_channel_id_db})")
            
            # Проверка кэша: пропускаем каналы, которые резолвили недавно (используем last_parsed_at как прокси)
            if channel['last_parsed_at']:
                age_hours = (datetime.now(timezone.utc) - channel['last_parsed_at']).total_seconds() / 3600
                if age_hours < 24:
                    print_info(f"  Пропущен (резолвили {age_hours:.1f} часов назад)")
                    stats['skipped'] += 1
                    continue
            
            # Пытаемся получить entity по tg_channel_id
            try:
                entity = await client.get_entity(int(tg_channel_id_db))
                # Проверяем, что это правильный канал
                if hasattr(entity, 'username') and entity.username:
                    if entity.username == (username.lstrip('@') if username else None):
                        print_success(f"  tg_channel_id валидный")
                        stats['valid'] += 1
                        # Снимаем блокировку если была
                        if channel['blocked_until']:
                            if not dry_run:
                                await conn.execute(
                                    "UPDATE channels SET blocked_until = NULL WHERE id = $1",
                                    channel_id
                                )
                                print_success(f"  Блокировка снята")
                        # Джиттер между успешными резолвами
                        await asyncio.sleep(random.uniform(2, 5))
                        continue
                
                # Если username не совпадает, но entity получен - возможно, канал изменил username
                print_warning(f"  tg_channel_id работает, но username не совпадает")
                stats['valid'] += 1
                # Джиттер
                await asyncio.sleep(random.uniform(2, 5))
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
                            # Context7: Сохраняем всегда entity.id (положительный channel_id), НЕ peer_id!
                            # Это позволяет избежать проблем с InputPeerChannel который требует положительный channel_id
                            if hasattr(entity, 'id') and entity.id is not None:
                                # Храним entity.id (положительный) напрямую, без конверсии в peer_id
                                new_tg_channel_id = entity.id
                                
                                print_success(f"  Найден правильный tg_channel_id: {new_tg_channel_id}")
                                
                                # Context7: Сохраняем access_hash и preferred_account_id при обновлении
                                access_hash = None
                                if hasattr(entity, 'access_hash'):
                                    access_hash = entity.access_hash
                                
                                account_id = None
                                try:
                                    if client.is_connected() and await client.is_user_authorized():
                                        me = await client.get_me()
                                        if me and hasattr(me, 'id'):
                                            account_id = me.id
                                except Exception:
                                    pass
                                
                                if not dry_run:
                                    # Обновляем в БД: tg_channel_id, access_hash, preferred_account_id, снимаем блокировку
                                    # Context7: Сохраняем resolve_status, resolve_error_code, resolve_is_terminal
                                    if access_hash and account_id:
                                        await conn.execute(
                                            """UPDATE channels 
                                               SET tg_channel_id = $1, 
                                                   access_hash = $2,
                                                   preferred_account_id = $3,
                                                   blocked_until = NULL,
                                                   resolve_status = 'ok',
                                                   resolve_error_code = 'ok',
                                                   resolve_is_terminal = false,
                                                   resolve_attempts = 0,
                                                   last_resolve_at = NOW(),
                                                   last_resolve_error = NULL
                                               WHERE id = $4""",
                                            new_tg_channel_id,
                                            access_hash,
                                            account_id,
                                            channel_id
                                        )
                                    else:
                                        await conn.execute(
                                            """UPDATE channels 
                                               SET tg_channel_id = $1, 
                                                   blocked_until = NULL,
                                                   resolve_status = 'ok',
                                                   resolve_error_code = 'ok',
                                                   resolve_is_terminal = false,
                                                   resolve_attempts = 0,
                                                   last_resolve_at = NOW(),
                                                   last_resolve_error = NULL
                                               WHERE id = $2""",
                                            new_tg_channel_id,
                                            channel_id
                                        )
                                    print_success(f"  Обновлен в БД (tg_channel_id, access_hash, preferred_account_id) и блокировка снята")
                                else:
                                    print_info(f"  [DRY RUN] Был бы обновлен на {new_tg_channel_id} (access_hash: {access_hash}, account_id: {account_id})")
                                stats['updated'] += 1
                                
                                # Джиттер между успешными резолвами
                                await asyncio.sleep(random.uniform(2, 5))
                            else:
                                print_error(f"  Entity не имеет валидного ID")
                                stats['errors'] += 1
                                
                        except errors.FloodWaitError as e:
                            # Context7: Политика переключения для repair скриптов
                            # При FloodWait > 60-120 сек - останавливаем скрипт (не переключаемся на другую сессию)
                            # Это предотвращает каскадный FloodWait на всех сессиях
                            should_abort = e.seconds > 120  # Для repair: строгий лимит 120 сек
                            
                            if should_abort:
                                print_error(f"  FloodWait {e.seconds}s - ABORTING script (repair context)")
                                resume_after = datetime.now(timezone.utc) + timedelta(seconds=e.seconds)
                                print_info(f"  Resume after: {resume_after.isoformat()}")
                                print_warning("  НЕ переключаемся на другую сессию - защита от каскадного FloodWait")
                                stats['errors'] += 1
                                # Context7: FloodWait фиксируем на аккаунте (Redis), не на канале
                                # Устанавливаем глобальный circuit breaker
                                if redis_client:
                                    try:
                                        unlock_time = time.time() + e.seconds
                                        await redis_client.setex(
                                            f"tg:floodwait_until:{session_id}",
                                            e.seconds + 60,
                                            str(unlock_time)
                                        )
                                    except Exception:
                                        pass
                                return
                            
                            # Малый FloodWait - пропускаем канал
                            print_error(f"  FloodWait при получении по username: {e.seconds} секунд")
                            print_warning(f"  Пропускаем канал из-за FloodWait")
                            stats['errors'] += 1
                            # Ждем небольшой FloodWait (но не больше 60 секунд)
                            await asyncio.sleep(min(e.seconds, 60))
                            
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
        if redis_client:
            await redis_client.close()
    
    # Итоговая статистика
    print()
    print_info("=== ИТОГОВАЯ СТАТИСТИКА ===")
    print(f"  Всего проверено: {stats['total']}")
    print(f"  Валидных: {stats['valid']}")
    print(f"  Неверных: {stats['invalid']}")
    print(f"  Обновлено: {stats['updated']}")
    print(f"  Ошибок: {stats['errors']}")
    print(f"  Пропущено (кэш): {stats['skipped']}")
    
    if dry_run:
        print()
        print_warning("РЕЖИМ DRY RUN - изменения не применены")
        print_info("Запустите с --apply для применения изменений")


async def main():
    """Главная функция."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Проверка и обновление неверных tg_channel_id")
    parser.add_argument("--apply", action="store_true", help="Применить изменения (по умолчанию dry-run)")
    parser.add_argument("--limit", type=int, default=5, help="Лимит каналов для проверки (по умолчанию 5)")
    
    args = parser.parse_args()
    
    # Получаем параметры из переменных окружения
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
    api_id = int(os.getenv("TELEGRAM_API_ID", "0"))
    api_hash = os.getenv("TELEGRAM_API_HASH", "")
    session_path = os.getenv("TELEGRAM_SESSION_PATH", "/app/sessions/default.session")
    
    if api_id == 0 or not api_hash:
        print_error("TELEGRAM_API_ID и TELEGRAM_API_HASH должны быть установлены")
        sys.exit(1)
    
    dry_run = not args.apply
    
    if dry_run:
        print_warning("РЕЖИМ DRY RUN - изменения не будут применены")
        print_info("Используйте --apply для применения изменений")
        print()
    
    await validate_and_update_tg_channel_ids(
        db_url=db_url,
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        dry_run=dry_run,
        limit=args.limit
    )


if __name__ == "__main__":
    asyncio.run(main())
