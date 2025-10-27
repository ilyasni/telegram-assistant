"""
Tracing Middleware для сквозной трассировки запросов.
[C7-ID: API-TRACE-001]

Генерирует trace_id и сохраняет в request.state для использования в API и событиях.
"""

import time
import uuid
from typing import Dict, Any
from fastapi import Request, Response
from fastapi.responses import JSONResponse
import structlog

logger = structlog.get_logger()

class TracingMiddleware:
    """Middleware для генерации и передачи trace_id."""
    
    def __init__(self, app):
        self.app = app
    
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        request = Request(scope, receive)
        start_time = time.time()
        
        # Генерация trace_id
        trace_id = f"req_{uuid.uuid4().hex[:16]}"
        
        # Сохранение в request.state
        request.state.trace_id = trace_id
        
        # Логирование начала запроса
        logger.info("Request started",
                   method=request.method,
                   path=request.url.path,
                   user_id=getattr(request.state, 'user_id', None),
                   ip=request.client.host if request.client else None,
                   trace_id=trace_id)
        
        # Обработка запроса
        response_sent = False
        
        async def send_wrapper(message):
            nonlocal response_sent
            if not response_sent:
                response_sent = True
                
                # Добавление X-Trace-ID в headers
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append([b"x-trace-id", trace_id.encode()])
                    message["headers"] = headers
                
                # Логирование завершения запроса
                latency_ms = int((time.time() - start_time) * 1000)
                status_code = message.get("status", 200) if message.get("type") == "http.response.start" else None
                
                logger.info("Request completed",
                           method=request.method,
                           path=request.url.path,
                           status_code=status_code,
                           latency_ms=latency_ms,
                           trace_id=trace_id)
            
            await send(message)
        
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as e:
            # Логирование ошибки
            latency_ms = int((time.time() - start_time) * 1000)
            logger.error("Request failed",
                        method=request.method,
                        path=request.url.path,
                        error=str(e),
                        latency_ms=latency_ms,
                        trace_id=trace_id)
            raise

def get_trace_id(request: Request) -> str:
    """Получение trace_id из request."""
    return getattr(request.state, 'trace_id', 'unknown')
