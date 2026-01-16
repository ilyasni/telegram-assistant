#!/usr/bin/env python3
"""
Массовое заполнение tg_channel_id и access_hash для всех каналов.

Context7: Resolver job для заполнения tg_channel_id и access_hash с правильными лимитерами,
джиттером, lease-блокировками и обработкой FloodWait на уровне аккаунта.

Требования:
- Хранить tg_channel_id = entity.id (положительный channel_id, НЕ peer_id!)
- FloodWait фиксировать на аккаунте (Redis), не на канале
- Lease-блокировки через resolve_lease_until (TTL 10 минут)
- Jitter + лимитеры
- SKIP LOCKED логика (lease вместо in_progress)
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
from typing import Optional, Dict, List
from telethon import TelegramClient, errors
from telethon.tl.types import Channel

# Добавляем пути
PROJECT_ROOT = Path("/opt/telegram-assistant")
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "telethon-ingest"))

import structlog
from services.telegram_client_manager import TelegramClientManager
from services.floodwait_manager import FloodWaitManager

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


def is_terminal_error(error_code: str) -> bool:
    """Проверка, является ли ошибка terminal (не retryable)."""
    terminal_codes = {'username_invalid', 'not_channel', 'banned'}
    return error_code in terminal_codes


async def resolve_channels_job(
    db_url: str,
    redis_url: str,
    telegram_client_manager: TelegramClientManager,
    floodwait_manager: FloodWaitManager,
    batch_size: int = 10,
    dry_run: bool = True,
    jitter_min: float = 1.5,
    jitter_max: float = 4.0
):
    """
    Массовое заполнение tg_channel_id и access_hash для всех каналов.
    
    Context7: Resolver job с правильными лимитерами, джиттером, lease-блокировками
    и обработкой FloodWait на уровне аккаунта.
    
    Args:
        db_url: PostgreSQL connection string
        redis_url: Redis connection string
        telegram_client_manager: TelegramClientManager для получения сессий
        floodwait_manager: FloodWaitManager для проверки глобального FloodWait
        batch_size: Лимит каналов для обработки за запуск (по умолчанию 10)
        dry_run: If True, only check without updating
        jitter_min: Минимальное время jitter между запросами (секунды)
        jitter_max: Максимальное время jitter между запросами (секунды)
    """
    # Подключение к БД
    conn = await asyncpg.connect(db_url)
    
    # Подключение к Redis
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    stats = {
        'processed': 0,
        'updated': 0,
        'errors': 0,
        'skipped': 0,
        'floodwait_aborted': 0
    }
    
    try:
        # Получаем каналы для резолва с lease-блокировкой
        # Context7: Lease-блокировки через resolve_lease_until (TTL 10 минут)
        # Используем UPDATE с WHERE для атомарного получения канала
        query = """
        UPDATE channels
        SET resolve_lease_until = NOW() + INTERVAL '10 minutes'
        WHERE id IN (
            SELECT id
            FROM channels
            WHERE is_active = true
              AND (tg_channel_id IS NULL OR access_hash IS NULL)
              AND username IS NOT NULL
              AND username != ''
              AND resolve_status IS DISTINCT FROM 'error'
              AND (resolve_lease_until IS NULL OR resolve_lease_until < NOW())
              AND (resolve_next_at IS NULL OR resolve_next_at < NOW())
            ORDER BY 
              CASE WHEN tg_channel_id IS NULL THEN 0 ELSE 1 END,
              resolve_next_at ASC NULLS FIRST,
              created_at ASC
            LIMIT $1
        )
        RETURNING 
            id, 
            username, 
            title, 
            tg_channel_id, 
            access_hash,
            preferred_account_id
        """
        
        channels = await conn.fetch(query, batch_size)
        print_info(f"Найдено каналов для резолва: {len(channels)} (лимит: {batch_size})")
        print()
        
        if len(channels) == 0:
            print_success("Нет каналов для резолва")
            return stats
        
        # Context7: Используем IngestAccountPool для выбора аккаунтов
        # Создаем AsyncSession из asyncpg connection для IngestAccountPool
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
        from sqlalchemy.pool import NullPool
        from services.ingest_account_pool import IngestAccountPool
        import redis.asyncio as redis
        
        # Создаем AsyncSession из db_url для IngestAccountPool
        # IngestAccountPool требует AsyncSession, поэтому создаем его отдельно
        db_url_async = db_url.replace("postgresql://", "postgresql+asyncpg://", 1) if db_url.startswith("postgresql://") else db_url
        engine = create_async_engine(db_url_async, poolclass=NullPool)
        async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
        db_session = async_session_factory()
        
        redis_client = redis.from_url(redis_url, decode_responses=True)
        ingest_account_pool = IngestAccountPool(db_session, redis_client)
        
        print_info("Используем пул сервисных аккаунтов для резолва")
        print()
        
        for i, channel in enumerate(channels, 1):
            channel_id = str(channel['id'])
            username = channel['username']
            title = channel['title']
            tg_channel_id_db = channel['tg_channel_id']
            access_hash_db = channel['access_hash']
            preferred_account_id = channel.get('preferred_account_id')
            preferred_ingest_account_id = channel.get('preferred_ingest_account_id')
            
            print_info(f"[{i}/{len(channels)}] Резолв: {username or title} (id: {channel_id})")
            
            # Выбираем аккаунт из пула для resolver задачи
            result = await ingest_account_pool.select_account_for_channel(
                channel_id=channel_id,
                task_type='resolver',
                preferred_account_id=preferred_ingest_account_id
            )
            
            if not result:
                print_warning(f"  Нет доступных аккаунтов из пула, пропускаем")
                stats['skipped'] += 1
                continue
            
            telegram_id, ingest_account_id = result
            
            # Получаем клиент
            client = await telegram_client_manager.get_client(telegram_id)
            account_id = telegram_id
            
            if not client:
                print_warning(f"  Нет доступного клиента для account_id {account_id}, пропускаем")
                stats['skipped'] += 1
                # Снимаем lease-блокировку
                await conn.execute(
                    "UPDATE channels SET resolve_lease_until = NULL WHERE id = $1",
                    channel_id
                )
                continue
            
            # Проверка глобального FloodWait для выбранной сессии
            session_id = str(account_id)
            global_wait = await floodwait_manager.check_global_floodwait(session_id)
            if global_wait and global_wait > 0:
                print_warning(f"  Global FloodWait активен для account_id {account_id}, пропускаем")
                stats['skipped'] += 1
                # Снимаем lease-блокировку
                await conn.execute(
                    "UPDATE channels SET resolve_lease_until = NULL WHERE id = $1",
                    channel_id
                )
                continue
            
            try:
                # Резолвим канал по username
                clean_username = username.lstrip('@')
                entity = await client.get_entity(clean_username)
                
                # Context7: Проверяем, что это канал (не user/chat)
                if not isinstance(entity, Channel):
                    error_code = 'not_channel'
                    print_warning(f"  Entity не является каналом, тип: {type(entity).__name__}")
                    
                    if not dry_run:
                        await conn.execute(
                            """UPDATE channels 
                               SET resolve_status = 'error',
                                   resolve_error_code = $1,
                                   resolve_is_terminal = true,
                                   resolve_lease_until = NULL,
                                   last_resolve_at = NOW(),
                                   last_resolve_error = 'Entity is not a channel'
                               WHERE id = $2""",
                            error_code,
                            channel_id
                        )
                    stats['errors'] += 1
                    continue
                
                # Context7: КРИТИЧНО - храним entity.id (положительный channel_id), НЕ peer_id!
                tg_channel_id_new = entity.id  # Положительный channel_id
                access_hash_new = getattr(entity, 'access_hash', None)
                
                # Обновляем в БД
                if not dry_run:
                    # Context7: Сохраняем preferred_ingest_account_id (FK) и preferred_account_id (legacy)
                    await conn.execute(
                        """UPDATE channels 
                           SET tg_channel_id = COALESCE($1, tg_channel_id),
                               access_hash = COALESCE($2, access_hash),
                               preferred_account_id = COALESCE($3, preferred_account_id),
                               preferred_ingest_account_id = COALESCE($4, preferred_ingest_account_id),
                               resolve_status = 'ok',
                               resolve_error_code = 'ok',
                               resolve_is_terminal = false,
                               resolve_next_at = NULL,
                               resolve_lease_until = NULL,
                               last_resolve_at = NOW(),
                               last_resolve_error = NULL
                           WHERE id = $5""",
                        tg_channel_id_new,
                        access_hash_new,
                        account_id,
                        ingest_account_id,
                        channel_id
                    )
                    print_success(f"  Обновлен: tg_channel_id={tg_channel_id_new}, access_hash={access_hash_new}, account={account_id}")
                else:
                    print_info(f"  [DRY RUN] Был бы обновлен: tg_channel_id={tg_channel_id_new}, access_hash={access_hash_new}, account={account_id}")
                
                stats['updated'] += 1
                
                # Освобождаем inflight в пуле
                if ingest_account_id:
                    await ingest_account_pool.mark_account_complete(ingest_account_id)
                
                # Context7: Jitter между запросами (random.uniform(1.5, 4.0) секунд)
                jitter = random.uniform(jitter_min, jitter_max)
                await asyncio.sleep(jitter)
                
            except errors.FloodWaitError as e:
                # Context7: Обработка FloodWait на уровне аккаунта, НЕ канала
                # Обновляем blocked_until в пуле аккаунтов
                if 'ingest_account_id' in locals() and ingest_account_id:
                    from datetime import timedelta
                    blocked_until = datetime.now(timezone.utc) + timedelta(seconds=e.seconds)
                    error_code = f"FLOOD_WAIT_{e.seconds}"
                    await ingest_account_pool.update_blocked_until(
                        ingest_account_id, blocked_until, error_code
                    )
                    # Освобождаем inflight
                    await ingest_account_pool.mark_account_complete(ingest_account_id)
                
                session_id = str(account_id)
                
                if e.seconds > 60:
                    # Большой FloodWait - фиксируем на аккаунте и выходим
                    print_error(f"  FloodWait {e.seconds}s > 60s - ABORTING script")
                    
                    # Записываем в Redis: tg:floodwait_until:{account_id}
                    await floodwait_manager.set_global_floodwait(session_id, e.seconds)
                    
                    # У канала: resolve_status = 'deferred', resolve_next_at = now() + interval '1 hour', resolve_error_code = 'floodwait'
                    if not dry_run:
                        await conn.execute(
                            """UPDATE channels 
                               SET resolve_status = 'deferred',
                                   resolve_error_code = 'floodwait',
                                   resolve_is_terminal = false,
                                   resolve_next_at = NOW() + INTERVAL '1 hour',
                                   resolve_lease_until = NULL,
                                   last_resolve_at = NOW(),
                                   last_resolve_error = $1
                               WHERE id = $2""",
                            f"FloodWait {e.seconds}s",
                            channel_id
                        )
                    
                    stats['floodwait_aborted'] += 1
                    print_warning(f"  Записан FloodWait на аккаунт {account_id}, выход из скрипта")
                    break
                else:
                    # Малый FloodWait - пропускаем канал, продолжаем
                    print_warning(f"  FloodWait {e.seconds}s - пропускаем канал")
                    
                    if not dry_run:
                        await conn.execute(
                            """UPDATE channels 
                               SET resolve_status = 'deferred',
                                   resolve_error_code = 'floodwait',
                                   resolve_is_terminal = false,
                                   resolve_next_at = NOW() + INTERVAL '1 hour',
                                   resolve_lease_until = NULL,
                                   last_resolve_at = NOW(),
                                   last_resolve_error = $1
                               WHERE id = $2""",
                            f"FloodWait {e.seconds}s",
                            channel_id
                        )
                    
                    stats['errors'] += 1
                    # Небольшой jitter перед следующим каналом
                    await asyncio.sleep(min(e.seconds, 10))
                
            except errors.UsernameNotOccupiedError:
                # Освобождаем inflight при ошибке
                if 'ingest_account_id' in locals() and ingest_account_id:
                    await ingest_account_pool.mark_account_complete(ingest_account_id)
                
                error_code = 'username_invalid'
                print_warning(f"  Username не найден: {username}")
                
                if not dry_run:
                    await conn.execute(
                        """UPDATE channels 
                           SET resolve_status = 'error',
                               resolve_error_code = $1,
                               resolve_is_terminal = true,
                               resolve_lease_until = NULL,
                               last_resolve_at = NOW(),
                               last_resolve_error = 'Username not occupied'
                           WHERE id = $2""",
                        error_code,
                        channel_id
                    )
                stats['errors'] += 1
                
            except errors.ChannelPrivateError:
                # Освобождаем inflight при ошибке
                if 'ingest_account_id' in locals() and ingest_account_id:
                    await ingest_account_pool.mark_account_complete(ingest_account_id)
                
                error_code = 'private'
                print_warning(f"  Канал приватный: {username}")
                
                if not dry_run:
                    await conn.execute(
                        """UPDATE channels 
                           SET resolve_status = 'deferred',
                               resolve_error_code = $1,
                               resolve_is_terminal = true,
                               resolve_lease_until = NULL,
                               last_resolve_at = NOW(),
                               last_resolve_error = 'Channel is private'
                           WHERE id = $2""",
                        error_code,
                        channel_id
                    )
                stats['errors'] += 1
                
            except errors.UsernameInvalidError:
                # Освобождаем inflight при ошибке
                if 'ingest_account_id' in locals() and ingest_account_id:
                    await ingest_account_pool.mark_account_complete(ingest_account_id)
                
                error_code = 'username_invalid'
                print_warning(f"  Невалидный username: {username}")
                
                if not dry_run:
                    await conn.execute(
                        """UPDATE channels 
                           SET resolve_status = 'error',
                               resolve_error_code = $1,
                               resolve_is_terminal = true,
                               resolve_lease_until = NULL,
                               last_resolve_at = NOW(),
                               last_resolve_error = 'Invalid username'
                           WHERE id = $2""",
                        error_code,
                        channel_id
                    )
                stats['errors'] += 1
                
            except Exception as e:
                # Освобождаем inflight при ошибке
                if 'ingest_account_id' in locals() and ingest_account_id:
                    await ingest_account_pool.mark_account_complete(ingest_account_id)
                
                error_type = type(e).__name__
                error_msg = str(e)[:200]
                
                # Context7: Определяем, является ли ошибка terminal или retryable
                if 'migrat' in error_msg.lower() or 'username_changed' in error_msg.lower():
                    error_code = 'migrated_or_username_changed'
                    is_terminal = True
                elif 'banned' in error_msg.lower() or 'blocked' in error_msg.lower():
                    error_code = 'banned'
                    is_terminal = True
                else:
                    error_code = 'rpc_error'
                    is_terminal = False
                
                print_error(f"  Ошибка резолва: {error_type}: {error_msg}")
                
                if not dry_run:
                    await conn.execute(
                        """UPDATE channels 
                           SET resolve_status = CASE WHEN $3 THEN 'error' ELSE 'deferred' END,
                               resolve_error_code = $1,
                               resolve_is_terminal = $3,
                               resolve_next_at = CASE WHEN $3 THEN NULL ELSE NOW() + INTERVAL '1 hour' END,
                               resolve_lease_until = NULL,
                               last_resolve_at = NOW(),
                               last_resolve_error = $4
                           WHERE id = $2""",
                        error_code,
                        channel_id,
                        is_terminal,
                        error_msg
                    )
                stats['errors'] += 1
            
            stats['processed'] += 1
        
    finally:
        await conn.close()
        if 'db_session' in locals():
            await db_session.close()
        if 'engine' in locals():
            await engine.dispose()
        if 'redis_client' in locals():
            await redis_client.aclose()
    
    # Итоговая статистика
    print()
    print_info("=== ИТОГОВАЯ СТАТИСТИКА ===")
    print(f"  Обработано: {stats['processed']}")
    print(f"  Обновлено: {stats['updated']}")
    print(f"  Ошибок: {stats['errors']}")
    print(f"  Пропущено: {stats['skipped']}")
    if stats['floodwait_aborted'] > 0:
        print_warning(f"  FloodWait (скрипт остановлен): {stats['floodwait_aborted']}")
    
    if dry_run:
        print()
        print_warning("РЕЖИМ DRY RUN - изменения не применены")
        print_info("Запустите с --apply для применения изменений")
    
    return stats


async def main():
    """Главная функция."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Массовое заполнение tg_channel_id и access_hash для всех каналов")
    parser.add_argument("--apply", action="store_true", help="Применить изменения (по умолчанию dry-run)")
    parser.add_argument("--batch-size", type=int, default=10, help="Лимит каналов для обработки за запуск (по умолчанию 10)")
    parser.add_argument("--jitter-min", type=float, default=1.5, help="Минимальное время jitter между запросами (секунды)")
    parser.add_argument("--jitter-max", type=float, default=4.0, help="Максимальное время jitter между запросами (секунды)")
    
    args = parser.parse_args()
    
    # Получаем параметры из переменных окружения
    # Context7: Убираем параметры из DATABASE_URL, которые не поддерживаются asyncpg
    db_url_raw = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
    # Удаляем параметры запроса из URL (например, ?connect_timeout=...)
    db_url = db_url_raw.split('?')[0] if '?' in db_url_raw else db_url_raw
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    
    # Context7: Инициализация TelegramClientManager и FloodWaitManager
    # Нужен sync Redis для TelegramClientManager и async Redis для FloodWaitManager
    import redis as redis_sync
    redis_client_sync = redis_sync.from_url(redis_url, decode_responses=False)
    redis_client_async = redis.from_url(redis_url, decode_responses=True)
    
    # Context7: Нужен sync БД connection для TelegramClientManager
    import psycopg2
    db_connection_sync = psycopg2.connect(db_url.replace("postgresql+asyncpg://", "postgresql://").replace("postgresql://", "postgresql://").split('?')[0])
    
    telegram_client_manager = TelegramClientManager(redis_client_sync, db_connection_sync)
    floodwait_manager = FloodWaitManager(redis_client_async)
    
    dry_run = not args.apply
    
    if dry_run:
        print_warning("РЕЖИМ DRY RUN - изменения не будут применены")
        print_info("Используйте --apply для применения изменений")
        print()
    
    stats = await resolve_channels_job(
        db_url=db_url,
        redis_url=redis_url,
        telegram_client_manager=telegram_client_manager,
        floodwait_manager=floodwait_manager,
        batch_size=args.batch_size,
        dry_run=dry_run,
        jitter_min=args.jitter_min,
        jitter_max=args.jitter_max
    )
    
    return stats


if __name__ == "__main__":
    asyncio.run(main())
