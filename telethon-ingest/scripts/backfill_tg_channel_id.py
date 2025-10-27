#!/usr/bin/env python3
"""
Скрипт для бекфилла tg_channel_id в таблице channels.
Использует Telegram API для получения channel ID по username.
"""
import asyncio
import sys
import os
import argparse
from typing import Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient
from telethon.utils import get_peer_id
from telethon.errors import UsernameNotOccupiedError, UsernameInvalidError, FloodWaitError
import psycopg2
from psycopg2.extras import RealDictCursor
import structlog
from prometheus_client import Counter, Histogram, start_http_server

from config import settings

logger = structlog.get_logger()

# Prometheus метрики
tgid_backfill_total = Counter(
    'tgid_backfill_total',
    'Total backfill operations',
    ['status']  # ok, skipped, failed
)

tgid_backfill_duration_seconds = Histogram(
    'tgid_backfill_duration_seconds',
    'Backfill operation duration'
)

channels_skipped_total = Counter(
    'channels_skipped_total',
    'Channels skipped during parsing',
    ['reason']  # no_tg_channel_id, no_username, etc
)


async def get_telegram_client() -> TelegramClient:
    """Получение авторизованного Telegram клиента."""
    import redis.asyncio as redis
    redis_client = redis.from_url(settings.redis_url)
    
    # Ищем авторизованную сессию
    keys = await redis_client.keys("tg:qr:session:*")
    session_string = None
    
    for key in keys:
        session_data = await redis_client.hgetall(key)
        if session_data.get(b'status') == b'authorized':
            session_string = session_data.get(b'session_string', b'').decode('utf-8')
            if session_string:
                logger.info("Found authorized session", key=key.decode())
                break
    
    if not session_string:
        raise Exception("No authorized Telegram session found")
    
    from telethon.sessions import StringSession
    session = StringSession(session_string)
    
    client = TelegramClient(
        session=session,
        api_id=settings.master_api_id,
        api_hash=settings.master_api_hash
    )
    
    await client.connect()
    logger.info("Connected to Telegram")
    
    await redis_client.close()
    return client


async def backfill_tg_channel_id(
    db_conn, 
    telegram_client: TelegramClient, 
    batch_size: int = 50,
    dry_run: bool = False,
    only_channel: str = None
) -> Dict[str, int]:
    """
    Бекфилл tg_channel_id для каналов.
    
    Args:
        db_conn: PostgreSQL connection
        telegram_client: Telegram client
        batch_size: Размер батча для обработки
        dry_run: Только симуляция, без обновления БД
        only_channel: Обработать только указанный канал (ID или username)
    """
    cursor = db_conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # Строим запрос
        if only_channel:
            if only_channel.startswith('@'):
                where_clause = "username = %s"
                params = [only_channel]
            else:
                where_clause = "id = %s"
                params = [only_channel]
        else:
            where_clause = "tg_channel_id IS NULL AND username IS NOT NULL AND username != ''"
            params = []
        
        query = f"""
            SELECT id, username, title
            FROM public.channels
            WHERE {where_clause}
            ORDER BY id
            LIMIT %s
        """
        params.append(batch_size)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        logger.info(f"Found {len(rows)} channels to process", dry_run=dry_run)
        
        ok, skipped, failed = 0, 0, 0
        
        for row in rows:
            ch_id = row['id']
            username = row['username'].strip().lstrip('@')
            title = row['title']
            
            logger.info("Processing channel", 
                       channel_id=ch_id, 
                       username=username, 
                       title=title)
            
            try:
                # Получаем entity из Telegram
                entity = await telegram_client.get_entity(username)
                peer_id = int(get_peer_id(entity))  # signed int64
                
                logger.info("Got peer ID from Telegram", 
                           channel_id=ch_id, 
                           username=username, 
                           peer_id=peer_id)
                
                if not dry_run:
                    # Обновляем БД
                    cursor.execute("""
                        UPDATE public.channels
                        SET tg_channel_id = %s
                        WHERE id = %s
                    """, (peer_id, ch_id))
                    db_conn.commit()
                
                ok += 1
                tgid_backfill_total.labels(status='ok').inc()
                
                logger.info("Successfully processed channel", 
                           channel_id=ch_id, 
                           username=username, 
                           peer_id=peer_id)
                
            except (UsernameNotOccupiedError, UsernameInvalidError) as e:
                # username не существует/неверен
                skipped += 1
                tgid_backfill_total.labels(status='skipped').inc()
                logger.warning("Username not found or invalid", 
                              channel_id=ch_id, 
                              username=username, 
                              error=str(e))
                
            except FloodWaitError as e:
                # Уважаем rate-limit
                logger.warning("Flood wait error", 
                              channel_id=ch_id, 
                              username=username, 
                              wait_seconds=e.seconds)
                await asyncio.sleep(e.seconds)
                continue
                
            except Exception as e:
                # Неожиданные ошибки
                failed += 1
                tgid_backfill_total.labels(status='failed').inc()
                logger.error("Unexpected error processing channel", 
                            channel_id=ch_id, 
                            username=username, 
                            error=str(e))
                continue
            
            # Небольшая задержка между запросами
            await asyncio.sleep(0.5)
        
        result = {"ok": ok, "skipped": skipped, "failed": failed}
        logger.info("Backfill completed", **result)
        return result
        
    except Exception as e:
        logger.error("Error in backfill_tg_channel_id", error=str(e))
        raise
    finally:
        cursor.close()


async def main():
    """Основная функция."""
    parser = argparse.ArgumentParser(description='Backfill tg_channel_id for channels')
    parser.add_argument('--batch-size', type=int, default=50, help='Batch size for processing')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode (no DB updates)')
    parser.add_argument('--only', type=str, help='Process only specific channel (ID or @username)')
    parser.add_argument('--prometheus-port', type=int, default=8012, help='Prometheus metrics port')
    
    args = parser.parse_args()
    
    logger.info("Starting tg_channel_id backfill", 
               batch_size=args.batch_size, 
               dry_run=args.dry_run,
               only_channel=args.only)
    
    # Запуск Prometheus метрик сервера
    start_http_server(args.prometheus_port)
    logger.info(f"Prometheus metrics server started on port {args.prometheus_port}")
    
    # Подключение к БД
    db_conn = psycopg2.connect(settings.database_url)
    
    try:
        # Получение Telegram клиента
        telegram_client = await get_telegram_client()
        
        # Выполнение бекфилла
        with tgid_backfill_duration_seconds.time():
            result = await backfill_tg_channel_id(
                db_conn, 
                telegram_client, 
                batch_size=args.batch_size,
                dry_run=args.dry_run,
                only_channel=args.only
            )
        
        logger.info("Backfill completed successfully", **result)
        
        # Проверяем результат
        cursor = db_conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM channels WHERE tg_channel_id IS NULL")
        remaining = cursor.fetchone()[0]
        cursor.close()
        
        logger.info(f"Remaining channels without tg_channel_id: {remaining}")
        
        await telegram_client.disconnect()
        
    except Exception as e:
        logger.error("Backfill failed", error=str(e))
        raise
    finally:
        db_conn.close()


if __name__ == "__main__":
    asyncio.run(main())
