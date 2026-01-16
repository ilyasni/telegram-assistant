"""
Context7: Пул сервисных аккаунтов для ingestion публичных каналов.

Управление пулом сервисных аккаунтов с:
- Защитой от FloodWait через blocked_until
- Отслеживанием inflight операций в Redis
- Разделением ролей (read/resolver/both)
- LRU выбором аккаунтов
"""

import asyncio
import time
from typing import List, Optional, Tuple, Dict, Any
from datetime import datetime, timezone
from uuid import UUID
import structlog
import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


class IngestAccountPool:
    """
    Context7: Пул сервисных аккаунтов для ingestion.
    
    Features:
    - Фильтрация по is_active, blocked_until, role
    - Отслеживание inflight операций в Redis
    - LRU выбор аккаунтов
    - Обновление blocked_until при FloodWait
    """
    
    def __init__(
        self,
        db_session: AsyncSession,
        redis_client: redis.Redis,
        debounce_seconds: int = 60
    ):
        """
        Инициализация IngestAccountPool.
        
        Args:
            db_session: AsyncSession для работы с БД
            redis_client: Async Redis клиент для оперативного состояния
            debounce_seconds: Интервал обновления last_used_at в Postgres (по умолчанию 60 сек)
        """
        self.db_session = db_session
        self.redis = redis_client
        self.debounce_seconds = debounce_seconds
        self._last_used_cache: Dict[UUID, float] = {}  # Кеш для debounce
    
    async def get_available_accounts(
        self,
        task_type: str = 'read'
    ) -> List[Dict[str, Any]]:
        """
        Получить список доступных аккаунтов для задачи.
        
        Context7: Фильтрует аккаунты по:
        - is_active = true
        - blocked_until IS NULL OR blocked_until <= NOW()
        - role содержит task_type ('read', 'resolver', или 'both')
        - inflight < max_concurrent_channels (из Redis)
        
        Args:
            task_type: Тип задачи ('read' или 'resolver')
        
        Returns:
            Список словарей с данными аккаунтов, отсортированный по priority, inflight, LRU
        """
        try:
            # SQL запрос для получения активных незаблокированных аккаунтов
            query = text("""
                SELECT 
                    id,
                    telegram_id,
                    is_active,
                    priority,
                    role,
                    max_concurrent_channels,
                    blocked_until,
                    last_error_code,
                    last_error_at
                FROM ingest_accounts
                WHERE is_active = true
                  AND (blocked_until IS NULL OR blocked_until <= NOW())
                  AND (role = :task_type OR role = 'both')
                ORDER BY priority ASC, last_used_at ASC NULLS FIRST
            """)
            
            result = await self.db_session.execute(
                query,
                {"task_type": task_type}
            )
            accounts = result.fetchall()
            
            # Получаем inflight из Redis для каждого аккаунта
            available_accounts = []
            current_time = time.time()
            
            for account in accounts:
                account_id = account.id
                telegram_id = account.telegram_id
                max_concurrent = account.max_concurrent_channels
                
                # Получаем inflight из Redis
                inflight_key = f"ingest:acct:{account_id}:inflight"
                try:
                    inflight_str = await self.redis.get(inflight_key)
                    inflight = int(inflight_str) if inflight_str else 0
                except Exception as e:
                    logger.warning("Failed to get inflight from Redis",
                                 account_id=str(account_id),
                                 error=str(e))
                    inflight = 0
                
                # Проверяем лимит max_concurrent_channels
                if max_concurrent is not None and inflight >= max_concurrent:
                    logger.debug("Account at max concurrent limit",
                               account_id=str(account_id),
                               inflight=inflight,
                               max_concurrent=max_concurrent)
                    continue
                
                # Получаем last_used_ts из Redis
                last_used_key = f"ingest:acct:{account_id}:last_used_ts"
                try:
                    last_used_str = await self.redis.get(last_used_key)
                    last_used_ts = float(last_used_str) if last_used_str else 0.0
                except Exception:
                    last_used_ts = 0.0
                
                available_accounts.append({
                    'id': account_id,
                    'telegram_id': telegram_id,
                    'priority': account.priority,
                    'role': account.role,
                    'max_concurrent_channels': max_concurrent,
                    'inflight': inflight,
                    'last_used_ts': last_used_ts,
                    'blocked_until': account.blocked_until,
                    'last_error_code': account.last_error_code,
                    'last_error_at': account.last_error_at,
                })
            
            # Сортировка: priority ASC, inflight ASC, last_used_ts ASC (LRU)
            available_accounts.sort(
                key=lambda x: (x['priority'], x['inflight'], x['last_used_ts'])
            )
            
            logger.debug("Available accounts",
                        task_type=task_type,
                        count=len(available_accounts))
            
            return available_accounts
            
        except Exception as e:
            logger.error("Failed to get available accounts",
                        task_type=task_type,
                        error=str(e))
            return []
    
    async def select_account_for_channel(
        self,
        channel_id: str,
        task_type: str = 'read',
        preferred_account_id: Optional[UUID] = None
    ) -> Optional[Tuple[int, UUID]]:
        """
        Выбрать аккаунт для канала.
        
        Context7: Выбирает аккаунт с учетом:
        - preferred_account_id (если задан и доступен)
        - least(inflight) + LRU из пула
        
        Args:
            channel_id: UUID канала в БД
            task_type: Тип задачи ('read' или 'resolver')
            preferred_account_id: UUID предпочтительного аккаунта (из ingest_accounts.id)
        
        Returns:
            Tuple (telegram_id, ingest_account_id) или None если нет доступных аккаунтов
        """
        try:
            # Если задан preferred_account_id, проверяем его доступность
            if preferred_account_id:
                preferred = await self._get_account_by_id(preferred_account_id)
                if preferred and await self._is_account_available(preferred, task_type):
                    # Увеличиваем inflight
                    await self.mark_account_inflight(preferred_account_id, increment=1)
                    # Обновляем last_used_ts
                    await self._update_last_used_ts(preferred_account_id)
                    logger.debug("Using preferred account",
                               channel_id=channel_id,
                               account_id=str(preferred_account_id),
                               telegram_id=preferred['telegram_id'])
                    return (preferred['telegram_id'], preferred_account_id)
            
            # Получаем доступные аккаунты из пула
            available_accounts = await self.get_available_accounts(task_type)
            
            if not available_accounts:
                logger.warning("No available accounts for channel",
                             channel_id=channel_id,
                             task_type=task_type)
                return None
            
            # Выбираем первый (уже отсортирован по priority, inflight, LRU)
            selected = available_accounts[0]
            selected_id = selected['id']
            telegram_id = selected['telegram_id']
            
            # Увеличиваем inflight
            await self.mark_account_inflight(selected_id, increment=1)
            # Обновляем last_used_ts
            await self._update_last_used_ts(selected_id)
            
            logger.debug("Selected account from pool",
                        channel_id=channel_id,
                        account_id=str(selected_id),
                        telegram_id=telegram_id,
                        priority=selected['priority'],
                        inflight=selected['inflight'] + 1)
            
            return (telegram_id, selected_id)
            
        except Exception as e:
            logger.error("Failed to select account for channel",
                        channel_id=channel_id,
                        task_type=task_type,
                        error=str(e))
            return None
    
    async def mark_account_inflight(
        self,
        ingest_account_id: UUID,
        increment: int = 1
    ) -> int:
        """
        Обновить счетчик inflight операций в Redis.
        
        Args:
            ingest_account_id: UUID аккаунта из ingest_accounts
            increment: Изменение счетчика (1 для увеличения, -1 для уменьшения)
        
        Returns:
            Новое значение счетчика
        """
        try:
            inflight_key = f"ingest:acct:{ingest_account_id}:inflight"
            
            if increment > 0:
                new_value = await self.redis.incr(inflight_key)
            else:
                new_value = await self.redis.decr(inflight_key)
                # Context7: Защита от отрицательных значений (может быть из-за ошибок в логике освобождения)
                if new_value < 0:
                    logger.warning("Inflight counter became negative, resetting to 0",
                                 account_id=str(ingest_account_id),
                                 new_value=new_value)
                    new_value = 0
                    await self.redis.set(inflight_key, 0)
            
            # Устанавливаем TTL 1 час (на случай если счетчик "зависнет")
            await self.redis.expire(inflight_key, 3600)
            
            return new_value
            
        except Exception as e:
            logger.error("Failed to update inflight counter",
                        account_id=str(ingest_account_id),
                        increment=increment,
                        error=str(e))
            return 0
    
    async def mark_account_complete(
        self,
        ingest_account_id: UUID
    ) -> None:
        """
        Уменьшить счетчик inflight при завершении операции.
        
        Args:
            ingest_account_id: UUID аккаунта из ingest_accounts
        """
        await self.mark_account_inflight(ingest_account_id, increment=-1)
    
    async def update_blocked_until(
        self,
        ingest_account_id: UUID,
        until: datetime,
        error_code: Optional[str] = None
    ) -> None:
        """
        Обновить blocked_until и last_error_* в БД.
        
        Context7: Используется при FloodWait для блокировки аккаунта.
        
        Args:
            ingest_account_id: UUID аккаунта из ingest_accounts
            until: Время до которого аккаунт заблокирован
            error_code: Код ошибки (например, "FLOOD_WAIT_420")
        """
        try:
            query = text("""
                UPDATE ingest_accounts
                SET blocked_until = :until,
                    last_error_code = :error_code,
                    last_error_at = NOW()
                WHERE id = :account_id
            """)
            
            await self.db_session.execute(
                query,
                {
                    "until": until,
                    "error_code": error_code,
                    "account_id": ingest_account_id
                }
            )
            await self.db_session.commit()
            
            # Context7: Также обновляем в Redis для быстрой проверки
            redis_key = f"ingest:acct:{ingest_account_id}:blocked_until"
            if until and until > datetime.now(timezone.utc):
                # Устанавливаем ключ с TTL до истечения блокировки
                ttl_seconds = int((until - datetime.now(timezone.utc)).total_seconds())
                await self.redis.set(redis_key, until.isoformat(), ex=ttl_seconds)
            else:
                # Удаляем ключ если блокировка истекла или None
                await self.redis.delete(redis_key)
            
            logger.info("Updated blocked_until for account",
                       account_id=str(ingest_account_id),
                       until=until.isoformat() if until else None,
                       error_code=error_code)
            
        except Exception as e:
            logger.error("Failed to update blocked_until",
                        account_id=str(ingest_account_id),
                        error=str(e))
            await self.db_session.rollback()
    
    async def get_account_by_telegram_id(
        self,
        telegram_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Получить аккаунт по telegram_id.
        
        Args:
            telegram_id: Telegram ID аккаунта
        
        Returns:
            Словарь с данными аккаунта или None
        """
        try:
            query = text("""
                SELECT 
                    id,
                    telegram_id,
                    is_active,
                    priority,
                    role,
                    max_concurrent_channels,
                    blocked_until,
                    last_error_code,
                    last_error_at
                FROM ingest_accounts
                WHERE telegram_id = :telegram_id
            """)
            
            result = await self.db_session.execute(
                query,
                {"telegram_id": telegram_id}
            )
            row = result.fetchone()
            
            if not row:
                return None
            
            return {
                'id': row.id,
                'telegram_id': row.telegram_id,
                'is_active': row.is_active,
                'priority': row.priority,
                'role': row.role,
                'max_concurrent_channels': row.max_concurrent_channels,
                'blocked_until': row.blocked_until,
                'last_error_code': row.last_error_code,
                'last_error_at': row.last_error_at,
            }
            
        except Exception as e:
            logger.error("Failed to get account by telegram_id",
                        telegram_id=telegram_id,
                        error=str(e))
            return None
    
    async def _get_account_by_id(
        self,
        account_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Получить аккаунт по UUID."""
        try:
            query = text("""
                SELECT 
                    id,
                    telegram_id,
                    is_active,
                    priority,
                    role,
                    max_concurrent_channels,
                    blocked_until,
                    last_error_code,
                    last_error_at
                FROM ingest_accounts
                WHERE id = :account_id
            """)
            
            result = await self.db_session.execute(
                query,
                {"account_id": account_id}
            )
            row = result.fetchone()
            
            if not row:
                return None
            
            return {
                'id': row.id,
                'telegram_id': row.telegram_id,
                'is_active': row.is_active,
                'priority': row.priority,
                'role': row.role,
                'max_concurrent_channels': row.max_concurrent_channels,
                'blocked_until': row.blocked_until,
                'last_error_code': row.last_error_code,
                'last_error_at': row.last_error_at,
            }
            
        except Exception as e:
            logger.error("Failed to get account by id",
                        account_id=str(account_id),
                        error=str(e))
            return None
    
    async def _is_account_available(
        self,
        account: Dict[str, Any],
        task_type: str
    ) -> bool:
        """Проверить доступность аккаунта для задачи."""
        # Проверка is_active
        if not account.get('is_active'):
            return False
        
        # Проверка blocked_until
        blocked_until = account.get('blocked_until')
        if blocked_until and blocked_until > datetime.now(timezone.utc):
            return False
        
        # Проверка role
        role = account.get('role', 'both')
        if role != 'both' and role != task_type:
            return False
        
        # Проверка max_concurrent_channels (из Redis)
        account_id = account['id']
        max_concurrent = account.get('max_concurrent_channels')
        if max_concurrent is not None:
            try:
                inflight_key = f"ingest:acct:{account_id}:inflight"
                inflight_str = await self.redis.get(inflight_key)
                inflight = int(inflight_str) if inflight_str else 0
                if inflight >= max_concurrent:
                    return False
            except Exception:
                pass
        
        return True
    
    async def _update_last_used_ts(
        self,
        ingest_account_id: UUID
    ) -> None:
        """
        Обновить last_used_ts в Redis и (с debounce) в Postgres.
        
        Context7: Оперативное обновление в Redis, медленное в Postgres (debounce).
        """
        current_time = time.time()
        
        # Всегда обновляем в Redis
        last_used_key = f"ingest:acct:{ingest_account_id}:last_used_ts"
        try:
            await self.redis.set(last_used_key, str(current_time))
            await self.redis.expire(last_used_key, 86400)  # TTL 24 часа
        except Exception as e:
            logger.warning("Failed to update last_used_ts in Redis",
                         account_id=str(ingest_account_id),
                         error=str(e))
        
        # Обновляем в Postgres с debounce
        last_update = self._last_used_cache.get(ingest_account_id, 0)
        if current_time - last_update >= self.debounce_seconds:
            try:
                query = text("""
                    UPDATE ingest_accounts
                    SET last_used_at = NOW()
                    WHERE id = :account_id
                """)
                await self.db_session.execute(
                    query,
                    {"account_id": ingest_account_id}
                )
                await self.db_session.commit()
                self._last_used_cache[ingest_account_id] = current_time
            except Exception as e:
                logger.warning("Failed to update last_used_at in Postgres",
                             account_id=str(ingest_account_id),
                             error=str(e))
                await self.db_session.rollback()
