"""Основной модуль API сервиса."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from contextlib import asynccontextmanager
import structlog
import os
import time
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST, make_asgi_app
from config import settings
from fastapi.staticfiles import StaticFiles
# from routers import health, channels, posts, tg_auth, users, rag, sessions, admin_invites, tg_webapp_auth
from bot.webhook import router as bot_router, init_bot, ensure_webhook
from bot.handlers import router as bot_handlers

# Middleware imports
from middleware.tracing import TracingMiddleware
from middleware.rate_limiter import RateLimiterMiddleware, init_rate_limiter
from middleware.rls_middleware import RLSMiddleware
# from dependencies.event_bus import init_event_publisher, close_event_publisher

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Prometheus метрики
# Context7: Добавляем tenant_id и tier для multi-tenant мониторинга
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status', 'tenant_id', 'tier'])
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request duration', ['method', 'endpoint', 'tenant_id'])

# Новые метрики для SSL и Neo4j
from prometheus_client import Gauge
ssl_cert_not_after = Gauge('ssl_cert_not_after', 'SSL certificate expiration timestamp (epoch seconds)', ['domain'])
neo4j_connections_active = Gauge('neo4j_connections_active', 'Active Neo4j connections')

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Application startup - lifespan started")
    # [C7-ID: dev-mode-002] Логируем окружение при старте
    logger.info(
        "Runtime environment",
        app_env=os.getenv("APP_ENV", "production"),
        environment=settings.environment,
    )
    
    # Инициализация scheduler для периодических задач
    try:
        from tasks.scheduler_tasks import start_scheduler
        start_scheduler()
        logger.info("Scheduler started for digest and trend tasks")
    except Exception as e:
        logger.error("Failed to start scheduler", error=str(e))
        # Продолжаем без scheduler
    
    # Инициализация Redis для rate limiter
    try:
        logger.info("Initializing rate limiter...")
        await init_rate_limiter(settings.redis_url)
        logger.info("Rate limiter initialized")
    except Exception as e:
        logger.error("Failed to initialize rate limiter", error=str(e))
        # Продолжаем без rate limiting
    
    # Инициализация event publisher (временно отключено)
    # try:
    #     logger.info("Initializing event publisher...")
    #     await init_event_publisher(settings.redis_url)
    #     logger.info("Event publisher initialized")
    # except Exception as e:
    #     logger.error("Failed to initialize event publisher", error=str(e))
    #     # Продолжаем без event publishing
    
    # Инициализация бота
    if app:
        app.state.bot_ready = False
        try:
            logger.info("Starting bot initialization...")
            init_bot()
            app.state.bot_ready = True
            logger.info("Init bot: OK")
            # Ensure Telegram webhook is set up
            try:
                await ensure_webhook()
                logger.info("Ensure webhook: OK")
            except Exception as e:
                logger.error("Ensure webhook failed", error=str(e))
        except Exception as e:
            logger.exception("Init bot: FAILED", error=str(e))
            # Не поднимаем исключение, чтобы приложение могло запуститься
            logger.error("Bot initialization failed, but continuing without bot")
    
    logger.info("Lifespan startup complete, yielding control")
    yield
    
    # Shutdown
    logger.info("Lifespan shutdown started")
    
    # Остановка scheduler
    try:
        from tasks.scheduler_tasks import stop_scheduler
        stop_scheduler()
        logger.info("Scheduler stopped")
    except Exception as e:
        logger.error("Error stopping scheduler", error=str(e))
    
    # Shutdown (временно отключено)
    # try:
    #     await close_event_publisher()
    #     logger.info("Event publisher closed")
    # except Exception as e:
    #     logger.error("Error closing event publisher", error=str(e))
    
    logger.info("Application shutdown")

# Создание FastAPI приложения
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="Telegram Assistant API - Event-driven microservices architecture",
    lifespan=lifespan
)

# Access log middleware для отладки
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    print(f"REQ {request.method} {request.url.path}")
    response = await call_next(request)
    process_time = time.time() - start_time
    print(f"RES {response.status_code} {request.url.path} ({process_time:.3f}s)")
    return response

# Middleware (порядок важен!)
app.add_middleware(TracingMiddleware)  # Первым - генерирует trace_id
# Context7: RLS middleware - устанавливает app.tenant_id для Row Level Security
# Добавляем до rate limiter, чтобы tenant_id был доступен для rate limiting
if settings.feature_rls_enabled:
    app.add_middleware(RLSMiddleware)
app.add_middleware(RateLimiterMiddleware, redis_url=settings.redis_url)  # Проверяет лимиты (использует tenant_id)

# Context7 best practice: CORS whitelist из ENV
# [C7-ID: fastapi-cors-001]
# [C7-ID: dev-mode-004] Fail-fast: проверка несовместимости wildcard с credentials
cors_origins_normalized = [str(o).strip() for o in (settings.cors_origins or [])]

# Если CORS origins не заданы - разрешаем все origins без credentials
# (безопасно для development, но для production нужно задать явные origins)
if not cors_origins_normalized:
    logger.warning("CORS_ORIGINS not set, allowing all origins without credentials")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,  # Без credentials можно использовать wildcard
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],
    )
elif any(o == "*" for o in cors_origins_normalized):
    raise RuntimeError(
        "CORS configuration error: allow_origins=['*'] is incompatible with allow_credentials=True. "
        "Use explicit origins (e.g., 'http://localhost:8000,http://localhost:8080') or disable credentials."
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins_normalized,  # Строгий whitelist из ENV
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # Ограниченный список методов
        allow_headers=["Authorization", "Content-Type", "X-Requested-With"],  # Ограниченный список заголовков
    )


# Context7 best practice: Security headers middleware
# [C7-ID: security-headers-002]
@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Middleware для добавления security headers."""
    response = await call_next(request)
    
    # Security headers
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "img-src https: data:; "
        "script-src 'self' 'unsafe-inline' https://telegram.org https://*.telegram.org; "
        "connect-src 'self' https://api.telegram.org; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    return response


# Context7 best practice: маскирование чувствительных данных в логах
# [C7-ID: security-logs-mask-001]
def mask_sensitive_data(data: dict) -> dict:
    """Маскирование чувствительных данных в логах."""
    sensitive_keys = ['token', 'password', 'secret', 'key', 'session', 'auth', 'jwt']
    masked_data = data.copy()
    
    for key, value in masked_data.items():
        if any(sensitive_key in key.lower() for sensitive_key in sensitive_keys):
            if isinstance(value, str) and len(value) > 8:
                masked_data[key] = value[:4] + "***" + value[-4:]
            else:
                masked_data[key] = "***MASKED***"
    
    return masked_data


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Middleware для логирования запросов с маскированием секретов."""
    start_time = time.time()
    
    # Подготовка данных для логирования
    log_data = {
        "method": request.method,
        "url": str(request.url),
        "client_ip": request.client.host
    }
    
    # Маскирование чувствительных данных
    safe_log_data = mask_sensitive_data(log_data)
    
    # Логирование входящего запроса
    logger.info("Request started", **safe_log_data)
    
    # Обработка запроса
    response = await call_next(request)
    
    # Подсчёт метрик
    process_time = time.time() - start_time
    
    # Context7: Извлекаем tenant_id и tier из JWT для метрик
    tenant_id = "unknown"
    tier = "unknown"
    try:
        from dependencies.auth import extract_tenant_id_from_jwt
        from middleware.rate_limiter import RateLimiterMiddleware
        
        tenant_id = extract_tenant_id_from_jwt(request) or "unknown"
        
        # Пытаемся получить tier из JWT
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header.split(' ', 1)[1]
            try:
                import jwt as jwt_lib
                payload = jwt_lib.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"verify_signature": False})
                tier = payload.get('tier', 'unknown')
            except Exception:
                pass
    except Exception:
        pass  # Fallback к unknown если не удалось извлечь
    
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code,
        tenant_id=tenant_id,
        tier=tier
    ).inc()
    REQUEST_DURATION.labels(
        method=request.method,
        endpoint=request.url.path,
        tenant_id=tenant_id
    ).observe(process_time)
    
    # Логирование ответа
    logger.info("Request completed",
                method=request.method,
                url=str(request.url),
                status_code=response.status_code,
                duration=process_time)
    
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик исключений."""
    logger.error("Unhandled exception", 
                error=str(exc),
                method=request.method,
                url=str(request.url))
    
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


# Подключение роутеров
# Подключаем критичные роутеры
from routers import health, channels, tg_auth, tg_webapp_auth, users, sessions, session_management, posts
from routers import storage  # Context7: Storage Quota Management API
from routers import admin  # [C7-ID: api-admin-001] Admin panel API
from routers import admin_invites  # Admin invites management
from routers import rag  # RAG API endpoints
from routers import digest  # Digest API endpoints
from routers import trends  # Trends API endpoints
app.include_router(health.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
app.include_router(tg_auth.router)  # QR auth endpoints
app.include_router(tg_webapp_auth.router, prefix="/api")  # WebApp auth
app.include_router(users.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(posts.router, prefix="/api")
app.include_router(session_management.router)  # Session management API
app.include_router(storage.router, prefix="/api")  # Context7: Storage Quota Management API
app.include_router(admin.router)  # [C7-ID: api-admin-001] Admin panel API
app.include_router(admin_invites.router)  # Admin invites management
app.include_router(rag.router, prefix="/api")  # RAG API endpoints
app.include_router(digest.router, prefix="/api")  # Digest API endpoints
app.include_router(trends.router, prefix="/api")  # Trends API endpoints
app.include_router(bot_router, prefix="/tg")

# Диагностический код временно убран

# Статические файлы Mini App
# Важно: обслуживаем по обоим путям на случай различий проксирования Caddy
app.mount("/tg/app", StaticFiles(directory="/app/webapp", html=True), name="miniapp_tg")
app.mount("/app", StaticFiles(directory="/app/webapp", html=True), name="miniapp_compat")

# Без редиректа: /tg/app -> index.html
@app.get("/tg/app")
async def miniapp_no_slash_tg():
    from fastapi.responses import FileResponse
    return FileResponse("webapp/index.html")

# Совместимость: /app/ -> index.html
@app.get("/app/")
async def miniapp_root_compat():
    from fastapi.responses import FileResponse
    return FileResponse("webapp/index.html")

# Channels Mini App
@app.get("/app/channels")
async def serve_channels_app():
    """Служить channels.html для Mini App."""
    return FileResponse("webapp/channels.html")


@app.get("/")
async def root():
    """Корневой endpoint."""
    return {
        "message": "Telegram Assistant API",
        "version": settings.api_version,
        "environment": settings.environment
    }


@app.get("/metrics")
async def metrics():
    """Prometheus метрики."""
    from fastapi.responses import Response
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# Единый health-эндпоинт на корне сервиса
from routers.health import health_check as api_health_check  # noqa: E402


@app.get("/health")
async def health():
    return await api_health_check()


@app.get("/health/auth")
async def health_auth():
    """Проверка здоровья QR-авторизации."""
    from routers.health import health_auth as api_health_auth
    return await api_health_auth()


@app.get("/health/bot")
async def health_bot():
    """Проверка готовности бота."""
    return {"bot_ready": bool(getattr(app.state, "bot_ready", False))}


# Удаляем старый @app.on_event("startup") - теперь используется lifespan


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
