"""Основной модуль API сервиса."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import structlog
import time
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from config import settings
from fastapi.staticfiles import StaticFiles
from routers import health, channels, posts, tg_auth, users, rag, sessions, admin_invites
from bot.webhook import router as bot_router, init_bot, ensure_webhook
from bot.handlers import router as bot_handlers

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
REQUEST_COUNT = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
REQUEST_DURATION = Histogram('http_request_duration_seconds', 'HTTP request duration', ['method', 'endpoint'])

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Application startup - lifespan started")
    
    # Инициализация бота
    app.state.bot_ready = False
    try:
        logger.info("Starting bot initialization...")
        init_bot()
        app.state.bot_ready = True
        logger.info("Init bot: OK")
    except Exception as e:
        logger.exception("Init bot: FAILED", error=str(e))
        # Не поднимаем исключение, чтобы приложение могло запуститься
        logger.error("Bot initialization failed, but continuing without bot")
    
    logger.info("Lifespan startup complete, yielding control")
    yield
    logger.info("Lifespan shutdown started")
    
    # Shutdown
    logger.info("Application shutdown")

# Создание FastAPI приложения
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="Telegram Assistant API - Event-driven microservices architecture",
    lifespan=lifespan
)

# Context7 best practice: CORS whitelist из ENV
# [C7-ID: fastapi-cors-001]
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,  # Строгий whitelist из ENV
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
    response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data: blob:; connect-src 'self' https://api.telegram.org; frame-ancestors 'none'"
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
    REQUEST_COUNT.labels(
        method=request.method,
        endpoint=request.url.path,
        status=response.status_code
    ).inc()
    REQUEST_DURATION.labels(
        method=request.method,
        endpoint=request.url.path
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
app.include_router(health.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
app.include_router(posts.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(rag.router, prefix="/api")
app.include_router(sessions.router, prefix="/api")
app.include_router(admin_invites.router)
app.include_router(tg_auth.router)
app.include_router(bot_router, prefix="/tg")

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
