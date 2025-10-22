"""Основной модуль API сервиса."""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import structlog
import time
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from config import settings
from routers import health, channels, posts

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

# Создание FastAPI приложения
app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description="Telegram Assistant API - Event-driven microservices architecture"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    """Middleware для логирования запросов."""
    start_time = time.time()
    
    # Логирование входящего запроса
    logger.info("Request started", 
                method=request.method,
                url=str(request.url),
                client_ip=request.client.host)
    
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
