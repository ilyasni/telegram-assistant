"""
Context7 best practice: Rate limiting через Redis Lua script для атомарности.

Атомарная проверка rate limit без race conditions.
Поддержка различных типов лимитов: per-user, per-channel, global.
"""

import time
from typing import Optional, Dict, Any
import structlog
import redis.asyncio as redis
from prometheus_client import Counter, Gauge

logger = structlog.get_logger()

# Context7: Метрики rate limiting
rate_limit_hits_total = Counter(
    'rate_limit_hits_total',
    'Rate limit hits',
    ['type']  # user, channel, global
)

rate_limit_requests_total = Counter(
    'rate_limit_requests_total',
    'Rate limit requests',
    ['type', 'result']  # user/channel/global, allowed/blocked
)

active_rate_limits = Gauge(
    'active_rate_limits',
    'Currently active rate limits',
    ['type']
)


class RateLimiter:
    """
    Context7: Атомарный rate limiter через Redis Lua.
    
    Features:
    - Lua script для атомарности
    - Поддержка различных типов лимитов
    - Метрики для мониторинга
    - Graceful degradation при ошибках Redis
    """
    
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.logger = logger
        
        # Lua script для атомарной проверки rate limit
        self.rate_limit_lua = """
        local key = KEYS[1]
        local limit = tonumber(ARGV[1])
        local window = tonumber(ARGV[2])
        local current_time = tonumber(ARGV[3])
        
        -- Получаем текущее значение
        local current = redis.call('GET', key)
        if current == false then
            current = 0
        else
            current = tonumber(current)
        end
        
        -- Проверяем, нужно ли обновить TTL
        local ttl = redis.call('TTL', key)
        if ttl == -1 then
            -- Ключ существует но без TTL, устанавливаем
            redis.call('EXPIRE', key, window)
        elseif ttl == -2 then
            -- Ключ не существует, создаем с TTL
            redis.call('SETEX', key, window, 1)
            return {1, window}
        end
        
        -- Увеличиваем счетчик
        local new_count = redis.call('INCR', key)
        
        -- Возвращаем {count, ttl}
        return {new_count, redis.call('TTL', key)}
        """
        
        # Lua script для сброса rate limit
        self.reset_lua = """
        local key = KEYS[1]
        redis.call('DEL', key)
        return 1
        """
        
        # Lua script для получения статистики
        self.stats_lua = """
        local pattern = ARGV[1]
        local keys = redis.call('KEYS', pattern)
        local stats = {}
        
        for i, key in ipairs(keys) do
            local count = redis.call('GET', key)
            local ttl = redis.call('TTL', key)
            table.insert(stats, {key, count or 0, ttl})
        end
        
        return stats
        """
    
    async def check_rate_limit(
        self,
        key: str,
        limit: int,
        window: int = 60,
        rate_limit_type: str = "global"
    ) -> Dict[str, Any]:
        """
        Context7: Атомарная проверка rate limit через Lua.
        
        Args:
            key: Redis ключ для rate limit
            limit: Максимальное количество запросов
            window: Окно времени в секундах
            rate_limit_type: Тип лимита (user, channel, global)
            
        Returns:
            Dict с результатом проверки
        """
        try:
            current_time = int(time.time())
            
            # Выполняем Lua script
            result = await self.redis.eval(
                self.rate_limit_lua,
                1,  # количество ключей
                key,
                limit,
                window,
                current_time
            )
            
            if not result or len(result) != 2:
                # Fallback при ошибке Lua
                return await self._fallback_check(key, limit, window)
            
            current_count, ttl = result
            
            # Обновляем метрики
            rate_limit_requests_total.labels(
                type=rate_limit_type, 
                result='allowed' if current_count <= limit else 'blocked'
            ).inc()
            
            if current_count > limit:
                rate_limit_hits_total.labels(type=rate_limit_type).inc()
            
            return {
                "allowed": current_count <= limit,
                "current_count": current_count,
                "limit": limit,
                "remaining": max(0, limit - current_count),
                "reset_in": ttl,
                "rate_limit_type": rate_limit_type
            }
            
        except Exception as e:
            self.logger.error("Rate limit check failed", 
                            key=key, 
                            error=str(e))
            # Graceful degradation - разрешаем запрос при ошибке Redis
            return {
                "allowed": True,
                "current_count": 0,
                "limit": limit,
                "remaining": limit,
                "reset_in": window,
                "rate_limit_type": rate_limit_type,
                "error": str(e)
            }
    
    async def _fallback_check(
        self, 
        key: str, 
        limit: int, 
        window: int
    ) -> Dict[str, Any]:
        """
        Fallback проверка без Lua script.
        """
        try:
            # Простая проверка через INCR + EXPIRE
            current = await self.redis.incr(key)
            
            if current == 1:
                # Первый запрос, устанавливаем TTL
                await self.redis.expire(key, window)
            
            ttl = await self.redis.ttl(key)
            
            return {
                "allowed": current <= limit,
                "current_count": current,
                "limit": limit,
                "remaining": max(0, limit - current),
                "reset_in": ttl,
                "rate_limit_type": "fallback"
            }
            
        except Exception as e:
            self.logger.error("Fallback rate limit check failed", 
                            key=key, 
                            error=str(e))
            return {
                "allowed": True,
                "current_count": 0,
                "limit": limit,
                "remaining": limit,
                "reset_in": window,
                "rate_limit_type": "error"
            }
    
    async def reset_rate_limit(self, key: str) -> bool:
        """
        Сброс rate limit для ключа.
        
        Args:
            key: Redis ключ для сброса
            
        Returns:
            True если сброс успешен
        """
        try:
            await self.redis.eval(self.reset_lua, 1, key)
            self.logger.debug("Rate limit reset", key=key)
            return True
            
        except Exception as e:
            self.logger.error("Failed to reset rate limit", 
                            key=key, 
                            error=str(e))
            return False
    
    async def get_rate_limit_stats(
        self, 
        pattern: str = "rate_limit:*"
    ) -> Dict[str, Any]:
        """
        Получение статистики rate limits.
        
        Args:
            pattern: Паттерн для поиска ключей
            
        Returns:
            Dict со статистикой
        """
        try:
            result = await self.redis.eval(
                self.stats_lua,
                0,  # без ключей
                pattern
            )
            
            if not result:
                return {"active_limits": 0, "limits": []}
            
            limits = []
            for item in result:
                if len(item) >= 3:
                    limits.append({
                        "key": item[0],
                        "count": int(item[1]) if item[1] else 0,
                        "ttl": int(item[2]) if item[2] else 0
                    })
            
            # Обновляем метрику активных лимитов
            active_rate_limits.set(len(limits))
            
            return {
                "active_limits": len(limits),
                "limits": limits
            }
            
        except Exception as e:
            self.logger.error("Failed to get rate limit stats", 
                            pattern=pattern, 
                            error=str(e))
            return {"active_limits": 0, "limits": [], "error": str(e)}
    
    async def check_user_rate_limit(
        self, 
        user_id: int, 
        limit: int = 20, 
        window: int = 60
    ) -> Dict[str, Any]:
        """
        Проверка rate limit для пользователя.
        
        Args:
            user_id: ID пользователя
            limit: Лимит запросов
            window: Окно времени
            
        Returns:
            Результат проверки
        """
        key = f"rate_limit:user:{user_id}"
        return await self.check_rate_limit(key, limit, window, "user")
    
    async def check_channel_rate_limit(
        self, 
        channel_id: int, 
        limit: int = 10, 
        window: int = 60
    ) -> Dict[str, Any]:
        """
        Проверка rate limit для канала.
        
        Args:
            channel_id: ID канала
            limit: Лимит запросов
            window: Окно времени
            
        Returns:
            Результат проверки
        """
        key = f"rate_limit:channel:{channel_id}"
        return await self.check_rate_limit(key, limit, window, "channel")
    
    async def check_global_rate_limit(
        self, 
        limit: int = 100, 
        window: int = 60
    ) -> Dict[str, Any]:
        """
        Проверка глобального rate limit.
        
        Args:
            limit: Глобальный лимит запросов
            window: Окно времени
            
        Returns:
            Результат проверки
        """
        key = "rate_limit:global"
        return await self.check_rate_limit(key, limit, window, "global")
    
    async def wait_for_rate_limit(
        self,
        key: str,
        limit: int,
        window: int = 60,
        max_wait: int = 300
    ) -> bool:
        """
        Ожидание освобождения rate limit.
        
        Args:
            key: Redis ключ
            limit: Лимит запросов
            window: Окно времени
            max_wait: Максимальное время ожидания
            
        Returns:
            True если лимит освободился
        """
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            result = await self.check_rate_limit(key, limit, window)
            
            if result.get("allowed", False):
                return True
            
            # Ждем до сброса лимита
            reset_in = result.get("reset_in", window)
            if reset_in > 0:
                await asyncio.sleep(min(reset_in, 5))  # Максимум 5 секунд за раз
            else:
                await asyncio.sleep(1)
        
        return False
    
    async def cleanup_expired_limits(self) -> int:
        """
        Очистка истекших rate limits.
        
        Returns:
            Количество очищенных лимитов
        """
        try:
            # Получаем все ключи rate limit
            keys = await self.redis.keys("rate_limit:*")
            cleaned = 0
            
            for key in keys:
                ttl = await self.redis.ttl(key)
                if ttl == -2:  # Ключ не существует
                    await self.redis.delete(key)
                    cleaned += 1
            
            self.logger.debug("Cleaned expired rate limits", count=cleaned)
            return cleaned
            
        except Exception as e:
            self.logger.error("Failed to cleanup expired limits", error=str(e))
            return 0


# Context7: Утилиты для работы с rate limiting
async def create_rate_limiter(redis_client: redis.Redis) -> RateLimiter:
    """
    Создание экземпляра RateLimiter.
    
    Args:
        redis_client: Redis клиент
        
    Returns:
        RateLimiter instance
    """
    return RateLimiter(redis_client)


async def check_parsing_rate_limit(
    rate_limiter: RateLimiter,
    user_id: int,
    channel_id: int,
    user_limit: int = 20,
    channel_limit: int = 10,
    global_limit: int = 100
) -> Dict[str, Any]:
    """
    Комплексная проверка rate limit для парсинга.
    
    Args:
        rate_limiter: RateLimiter instance
        user_id: ID пользователя
        channel_id: ID канала
        user_limit: Лимит для пользователя
        channel_limit: Лимит для канала
        global_limit: Глобальный лимит
        
    Returns:
        Результат проверки всех лимитов
    """
    try:
        # Проверяем все лимиты параллельно
        user_result, channel_result, global_result = await asyncio.gather(
            rate_limiter.check_user_rate_limit(user_id, user_limit),
            rate_limiter.check_channel_rate_limit(channel_id, channel_limit),
            rate_limiter.check_global_rate_limit(global_limit),
            return_exceptions=True
        )
        
        # Обрабатываем исключения
        if isinstance(user_result, Exception):
            user_result = {"allowed": True, "error": str(user_result)}
        if isinstance(channel_result, Exception):
            channel_result = {"allowed": True, "error": str(channel_result)}
        if isinstance(global_result, Exception):
            global_result = {"allowed": True, "error": str(global_result)}
        
        # Определяем общий результат
        all_allowed = (
            user_result.get("allowed", True) and
            channel_result.get("allowed", True) and
            global_result.get("allowed", True)
        )
        
        return {
            "allowed": all_allowed,
            "user_limit": user_result,
            "channel_limit": channel_result,
            "global_limit": global_result,
            "blocked_by": [
                k for k, v in [
                    ("user", user_result),
                    ("channel", channel_result),
                    ("global", global_result)
                ] if not v.get("allowed", True)
            ]
        }
        
    except Exception as e:
        logger.error("Rate limit check failed", 
                    user_id=user_id,
                    channel_id=channel_id,
                    error=str(e))
        return {
            "allowed": True,  # Graceful degradation
            "error": str(e)
        }
