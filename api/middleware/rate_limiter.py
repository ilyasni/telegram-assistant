"""
Rate Limiting Middleware с Lua-скриптом для атомарности.
[C7-ID: API-RATELIMIT-001]

Поддерживает per-user, per-IP и global лимиты с sliding window.
"""

import asyncio
import time
import uuid
from typing import Dict, Any, Optional, Tuple
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import redis.asyncio as redis
import structlog
from sqlalchemy.orm import Session
from models.database import get_db, User
from prometheus_client import Counter

logger = structlog.get_logger()

# Метрики Prometheus (Context7: multi-tenant лейблы)
rate_limit_exceeded_total = Counter(
    'api_rate_limit_exceeded_total',
    'Rate limit exceeded',
    ['route', 'scope', 'tenant_id', 'tier']  # tenant_id и tier для multi-tenant мониторинга
)

# Матрица лимитов по endpoint и tier
RATE_LIMITS = {
    "POST /channels/users/{user_id}/subscribe": {
        "free": 10, "pro": 60, "premium": 120,
        "ip": 60, "global": 6000
    },
    "DELETE /channels/users/{user_id}/unsubscribe/{channel_id}": {
        "free": 20, "pro": 120, "premium": 240,
        "ip": 60, "global": 6000
    },
    "GET /channels/users/{user_id}/list": {
        "free": 60, "pro": 300, "premium": 600,
        "ip": 120, "global": 12000
    },
    "GET /channels/users/{user_id}/stats": {
        "free": 30, "pro": 150, "premium": 300,
        "ip": 60, "global": 6000
    },
    "POST /auth/telegram-webapp": {
        "free": 5, "pro": 20, "premium": 50,
        "ip": 10, "global": 1000
    }
}

class RateLimiter:
    """Rate limiter с Lua-скриптом для атомарности."""
    
    def __init__(self, redis_client: redis.Redis, script_sha: str, window_sec: int = 60):
        self.redis = redis_client
        self.script_sha = script_sha
        self.window_ms = window_sec * 1000
    
    async def check(self, key: str, limit: int) -> Tuple[bool, Dict[str, Any]]:
        """Проверка лимита через Lua-скрипт."""
        try:
            now_ms = int(time.time() * 1000)
            result = await self.redis.evalsha(
                self.script_sha, 1, key, now_ms, self.window_ms, limit
            )
            
            allowed = result[0] == 1
            remaining = result[1]
            reset = result[2]
            
            return allowed, {
                "limit": limit,
                "remaining": remaining,
                "reset": reset
            }
            
        except Exception as e:
            logger.error("Rate limit check failed", key=key, error=str(e))
            # В случае ошибки - разрешаем запрос
            return True, {"limit": limit, "remaining": limit, "reset": int(time.time()) + 60}
    
    def get_key(self, scope: str, identifier: str, route: str, tenant_id: Optional[str] = None) -> str:
        """Генерация ключа для Redis с префиксом tenant (Context7)."""
        if tenant_id:
            return f"t:{tenant_id}:rl:{scope}:{identifier}:{route}"
        return f"rl:{scope}:{identifier}:{route}"

class RateLimiterMiddleware:
    """FastAPI middleware для rate limiting."""
    
    def __init__(self, app, redis_url: str):
        self.app = app
        self.redis_url = redis_url
        self.redis_client: Optional[redis.Redis] = None
        self.rate_limiter: Optional[RateLimiter] = None
        self.script_sha: Optional[str] = None
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        request = Request(scope, receive)

        # Bypass for Telegram webhook
        if request.url.path.startswith("/tg/bot/webhook"):
            await self.app(scope, receive, send)
            return
        
        # Инициализация при первом запросе
        if not self.redis_client:
            await self._init_redis()
        
        # Проверка rate limit
        try:
            allowed, rate_info = await self._check_rate_limit(request)
            
            if not allowed:
                # Rate limit превышен
                response = JSONResponse(
                    status_code=429,
                    content={
                        "error": "rate_limit_exceeded",
                        "detail": "Route limit exceeded",
                        "limit": rate_info["limit"],
                        "remaining": rate_info["remaining"],
                        "reset": rate_info["reset"],
                        "trace_id": getattr(request.state, 'trace_id', 'unknown')
                    }
                )
                
                # Добавление headers
                response.headers["X-RateLimit-Limit"] = str(rate_info["limit"])
                response.headers["X-RateLimit-Remaining"] = str(rate_info["remaining"])
                response.headers["X-RateLimit-Reset"] = str(rate_info["reset"])
                
                await response(scope, receive, send)
                return
        
        except Exception as e:
            logger.error("Rate limit middleware error", error=str(e))
            # В случае ошибки - пропускаем запрос
        
        # Продолжение обработки
        await self.app(scope, receive, send)
    
    async def _init_redis(self):
        """Инициализация Redis и загрузка Lua-скрипта."""
        try:
            self.redis_client = redis.from_url(self.redis_url)
            
            # Загрузка Lua-скрипта
            with open('/app/middleware/lua_sliding_window.lua', 'r') as f:
                script_content = f.read()
            
            self.script_sha = await self.redis_client.script_load(script_content)
            self.rate_limiter = RateLimiter(self.redis_client, self.script_sha)
            
            logger.info("Rate limiter initialized", script_sha=self.script_sha)
            
        except Exception as e:
            logger.error("Failed to initialize rate limiter", error=str(e))
            raise
    
    async def _check_rate_limit(self, request: Request) -> Tuple[bool, Dict[str, Any]]:
        """Проверка rate limit для запроса (Context7: per-tenant + per-membership)."""
        route = f"{request.method} {request.url.path}"
        
        # Получение лимитов для маршрута
        route_limits = RATE_LIMITS.get(route)
        if not route_limits:
            return True, {}  # Нет лимитов для этого маршрута
        
        # Извлечение данных из JWT (tenant_id, membership_id, tier)
        jwt_payload = self._extract_jwt_payload(request)
        tenant_id = jwt_payload.get("tenant_id") if jwt_payload else None
        membership_id = jwt_payload.get("membership_id") if jwt_payload else None
        user_tier = jwt_payload.get("tier", "free") if jwt_payload else "free"
        
        # Получение user_id (для обратной совместимости)
        user_id = jwt_payload.get("sub") if jwt_payload else self._extract_user_id(request)
        
        # Context7: Проверка лимитов в порядке: membership -> tenant -> ip -> global
        # Используем минимум из двух ключей для tenant и membership
        checks = []
        
        # Per-membership limit (если есть)
        if membership_id and tenant_id:
            checks.append(("membership", membership_id, route_limits.get(user_tier, route_limits["free"]), tenant_id))
        
        # Per-tenant limit (более мягкий)
        if tenant_id:
            checks.append(("tenant", tenant_id, route_limits.get(user_tier, route_limits["free"]) * 2, tenant_id))
        
        # Per-user limit (fallback, без tenant префикса)
        if user_id:
            checks.append(("user", user_id, route_limits.get(user_tier, route_limits["free"]), None))
        
        # Per-IP limit
        checks.append(("ip", request.client.host, route_limits["ip"], None))
        
        # Global limit
        checks.append(("global", "global", route_limits["global"], None))
        
        for scope, identifier, limit, tenant_prefix in checks:
            key = self.rate_limiter.get_key(scope, identifier, route, tenant_prefix)
            allowed, rate_info = await self.rate_limiter.check(key, limit)
            
            if not allowed:
                # Логирование превышения лимита
                logger.warning("Rate limit exceeded",
                             route=route,
                             scope=scope,
                             identifier=identifier,
                             tenant_id=tenant_id,
                             membership_id=membership_id,
                             limit=limit,
                             remaining=rate_info["remaining"])
                
                # Метрики с лейблами (Context7: tenant_id и tier для мониторинга)
                rate_limit_exceeded_total.labels(
                    route=route,
                    scope=scope,
                    tenant_id=tenant_id or "unknown",
                    tier=user_tier
                ).inc()
                
                return False, rate_info
        
        return True, {}
    
    def _extract_user_id(self, request: Request) -> Optional[str]:
        """Извлечение user_id из запроса (legacy, для обратной совместимости)."""
        # Из path параметра
        if hasattr(request, 'path_params') and 'user_id' in request.path_params:
            return request.path_params['user_id']
        
        # Из JWT токена (если есть)
        jwt_payload = self._extract_jwt_payload(request)
        if jwt_payload:
            return jwt_payload.get('sub')
        
        return None
    
    def _extract_jwt_payload(self, request: Request) -> Optional[dict]:
        """Извлечение полного JWT payload из запроса."""
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return None
        
        token = auth_header.split(' ', 1)[1]
        try:
            parts = token.split('.')
            if len(parts) != 3:
                return None
            import base64, json
            payload_b64 = parts[1]
            payload_b64 += '=' * (4 - len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(payload_b64))
            
            # Проверка времени истечения
            if payload.get('exp', 0) < int(time.time()):
                return None
            
            return payload
        except Exception:
            return None
    
    async def _get_user_tier(self, user_id: Optional[str], db: Optional[Session] = None) -> str:
        """Получение tier пользователя из БД (Context7)."""
        if not user_id:
            return "free"
        
        # Попытка получить из БД (если сессия доступна)
        if db:
            try:
                # Пытаемся найти по telegram_id (int) или по UUID membership_id
                try:
                    tg_id = int(user_id)
                    user = db.query(User).filter(User.telegram_id == tg_id).first()
                except (ValueError, TypeError):
                    from uuid import UUID
                    try:
                        membership_uuid = UUID(user_id)
                        user = db.query(User).filter(User.id == membership_uuid).first()
                    except (ValueError, TypeError):
                        user = None
                
                if user and user.tier:
                    return user.tier
            except Exception as e:
                logger.debug("Failed to get tier from DB", user_id=user_id, error=str(e))
        
        return "free"

# Глобальная инициализация
_rate_limiter_middleware: Optional[RateLimiterMiddleware] = None

async def init_rate_limiter(redis_url: str):
    """Инициализация rate limiter."""
    global _rate_limiter_middleware
    _rate_limiter_middleware = RateLimiterMiddleware(None, redis_url)
    await _rate_limiter_middleware._init_redis()

def get_rate_limiter() -> Optional[RateLimiterMiddleware]:
    """Получение rate limiter instance."""
    return _rate_limiter_middleware
