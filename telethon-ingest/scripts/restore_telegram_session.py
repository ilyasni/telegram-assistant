#!/usr/bin/env python3
"""
Context7: [C7-ID: session-recovery-001] - Идемпотентное восстановление Telegram сессий из БД в Redis
Документация: https://docs.telethon.dev/en/stable/concepts/sessions.html
"""
import asyncio
import os
import sys
import time
import psycopg2
import redis
import structlog
from prometheus_client import Counter

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from crypto_utils import decrypt_session

# Prometheus метрики
session_restore_total = Counter(
    'session_restore_total',
    'Telegram session restore operations',
    ['status']  # success, failed
)

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    logger_factory=structlog.stdlib.LoggerFactory(),
)
logger = structlog.get_logger("restore_telegram_session")

async def restore_session_from_db():
    """
    Context7: [C7-ID: session-recovery-001] - Восстановление Telegram сессий из БД в Redis
    
    Извлекает зашифрованные session_string из таблицы users,
    расшифровывает их и сохраняет в Redis для TelegramClientManager.
    """
    logger.info("Starting Telegram session restoration from database")
    
    # 1. Подключение к PostgreSQL
    try:
        db_conn = psycopg2.connect(settings.database_url)
        logger.info("Connected to PostgreSQL database")
    except Exception as e:
        logger.error("Failed to connect to database", error=str(e))
        session_restore_total.labels(status="failed").inc()
        return False
    
    # 2. Подключение к Redis
    try:
        redis_client = redis.from_url(settings.redis_url)
        # Test connection
        redis_client.ping()
        logger.info("Connected to Redis")
    except Exception as e:
        logger.error("Failed to connect to Redis", error=str(e))
        session_restore_total.labels(status="failed").inc()
        return False
    
    try:
        # 3. Найти авторизованных пользователей
        cursor = db_conn.cursor()
        cursor.execute("""
            SELECT telegram_id, telegram_session_enc, telegram_session_key_id, telegram_auth_created_at
            FROM users 
            WHERE telegram_auth_status = 'authorized'
            ORDER BY telegram_auth_created_at DESC
        """)
        
        users = cursor.fetchall()
        logger.info(f"Found {len(users)} authorized users in database")
        
        if not users:
            logger.warning("No authorized users found in database")
            return True
        
        restored_count = 0
        failed_count = 0
        
        # 4. Для каждого пользователя:
        for row in users:
            telegram_id, session_enc, key_id, created_at = row
            
            try:
                logger.debug("Processing user", telegram_id=telegram_id, created_at=created_at)
                
                # Расшифровать session_string
                session_string = decrypt_session(session_enc)
                
                if not session_string:
                    logger.warning("Empty session string after decryption", telegram_id=telegram_id)
                    failed_count += 1
                    continue
                
                # Сохранить в Redis (Context7: централизованное хранилище сессий)
                redis_key = f"telegram:session:{telegram_id}"
                redis_client.set(
                    redis_key,
                    session_string,
                    ex=86400 * 7  # TTL 7 дней
                )
                
                # Дополнительно сохранить в формате, ожидаемом QR auth
                qr_redis_key = f"tg:qr:session:{telegram_id}"
                redis_client.hset(qr_redis_key, mapping={
                    "telegram_id": str(telegram_id),
                    "status": "authorized",
                    "session_id": str(uuid.uuid4()),  # Context7: Добавляем session_id
                    "session_string": session_string,
                    "created_at": str(int(time.time())),
                    "reason": "restored_from_db"
                })
                redis_client.expire(qr_redis_key, 86400 * 7)  # TTL 7 дней
                
                logger.info("Session restored successfully", 
                           telegram_id=telegram_id, 
                           redis_key=redis_key)
                restored_count += 1
                
            except Exception as e:
                logger.error("Failed to restore session for user", 
                           telegram_id=telegram_id, 
                           error=str(e))
                failed_count += 1
                continue
        
        # 5. Статистика
        logger.info("Session restoration completed", 
                   total_users=len(users),
                   restored=restored_count,
                   failed=failed_count)
        
        if restored_count > 0:
            session_restore_total.labels(status="success").inc()
        if failed_count > 0:
            session_restore_total.labels(status="failed").inc()
        
        return restored_count > 0
        
    except Exception as e:
        logger.error("Unexpected error during session restoration", error=str(e))
        session_restore_total.labels(status="failed").inc()
        return False
        
    finally:
        cursor.close()
        db_conn.close()
        redis_client.close()
        logger.info("Database and Redis connections closed")

async def main():
    """Main function for script execution."""
    logger.info("Starting Telegram session restore script")
    
    success = await restore_session_from_db()
    
    if success:
        logger.info("Session restoration completed successfully")
        print("✅ Session restoration completed successfully")
    else:
        logger.error("Session restoration failed")
        print("❌ Session restoration failed")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
