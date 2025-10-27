"""
HTTP Health Server для Crawl4AI сервиса.
Предоставляет health endpoints для мониторинга.
"""

import asyncio
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Dict, Any
import structlog

logger = structlog.get_logger()

class Crawl4AIHealthHandler(BaseHTTPRequestHandler):
    """HTTP handler для health checks Crawl4AI."""
    
    def do_GET(self):
        """Обработка GET запросов."""
        if self.path == "/health":
            self._handle_health()
        elif self.path == "/health/detailed":
            self._handle_detailed_health()
        elif self.path == "/metrics":
            self._handle_metrics()
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
    
    def _handle_health(self):
        """Базовый health check."""
        try:
            # Простая проверка - если сервер отвечает, значит работает
            response = {
                "status": "healthy",
                "service": "crawl4ai",
                "timestamp": time.time(),
                "uptime": time.time() - getattr(self.server, 'start_time', time.time())
            }
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            error_response = {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": time.time()
            }
            self.wfile.write(json.dumps(error_response).encode())
    
    def _handle_detailed_health(self):
        """Детальный health check с проверкой компонентов."""
        try:
            # Проверяем доступность Playwright
            playwright_ok = self._check_playwright()
            
            # Проверяем Redis подключение
            redis_ok = self._check_redis()
            
            # Проверяем конфигурацию
            config_ok = self._check_config()
            
            all_healthy = playwright_ok and redis_ok and config_ok
            
            response = {
                "status": "healthy" if all_healthy else "degraded",
                "service": "crawl4ai",
                "timestamp": time.time(),
                "components": {
                    "playwright": "ok" if playwright_ok else "error",
                    "redis": "ok" if redis_ok else "error",
                    "config": "ok" if config_ok else "error"
                }
            }
            
            status_code = 200 if all_healthy else 503
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())
            
        except Exception as e:
            logger.error("Detailed health check failed", error=str(e))
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            error_response = {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": time.time()
            }
            self.wfile.write(json.dumps(error_response).encode())
    
    def _check_playwright(self) -> bool:
        """Проверка доступности Playwright."""
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                browser.close()
            return True
        except Exception:
            return False
    
    def _check_redis(self) -> bool:
        """Проверка подключения к Redis."""
        try:
            import redis
            r = redis.from_url("redis://redis:6379")
            r.ping()
            return True
        except Exception:
            return False
    
    def _check_config(self) -> bool:
        """Проверка конфигурации."""
        try:
            import os
            return bool(os.getenv("REDIS_URL"))
        except Exception:
            return False
    
    def _handle_metrics(self):
        """Prometheus метрики для Crawl4AI."""
        try:
            # Базовые метрики Crawl4AI
            metrics = [
                "# HELP crawl4ai_uptime_seconds Crawl4AI uptime in seconds",
                "# TYPE crawl4ai_uptime_seconds gauge",
                f"crawl4ai_uptime_seconds {time.time() - getattr(self.server, 'start_time', time.time())}",
                "",
                "# HELP crawl4ai_health_status Crawl4AI health status (1=healthy, 0=unhealthy)",
                "# TYPE crawl4ai_health_status gauge",
                "crawl4ai_health_status 1",
                "",
                "# HELP crawl4ai_http_requests_total Total HTTP requests to crawl4ai health endpoint",
                "# TYPE crawl4ai_http_requests_total counter",
                "crawl4ai_http_requests_total{method=\"GET\",endpoint=\"/health\"} 1",
            ]
            
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write("\n".join(metrics).encode())
            
        except Exception as e:
            logger.error("Metrics generation failed", error=str(e))
            self.send_response(500)
            self.end_headers()
            self.wfile.write(f"Error generating metrics: {str(e)}".encode())
    
    def log_message(self, format, *args):
        """Отключаем стандартное логирование HTTP запросов."""
        pass

class Crawl4AIHealthServer:
    """HTTP сервер для health checks Crawl4AI."""
    
    def __init__(self, port: int = 8080):
        self.port = port
        self.server = None
        self.thread = None
        self.running = False
    
    def start(self):
        """Запуск health сервера в отдельном потоке."""
        if self.running:
            return
        
        try:
            self.server = HTTPServer(('0.0.0.0', self.port), Crawl4AIHealthHandler)
            self.server.start_time = time.time()
            
            self.thread = Thread(target=self._run_server, daemon=True)
            self.thread.start()
            
            self.running = True
            logger.info(f"Crawl4AI health server started on port {self.port}")
            
        except Exception as e:
            logger.error(f"Failed to start crawl4ai health server: {e}")
            raise
    
    def _run_server(self):
        """Запуск HTTP сервера."""
        try:
            self.server.serve_forever()
        except Exception as e:
            logger.error(f"Crawl4AI health server error: {e}")
    
    def stop(self):
        """Остановка health сервера."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        
        self.running = False
        logger.info("Crawl4AI health server stopped")

# Глобальный экземпляр сервера
health_server = Crawl4AIHealthServer()

def start_health_server():
    """Запуск health сервера (для использования в main.py)."""
    health_server.start()

def stop_health_server():
    """Остановка health сервера."""
    health_server.stop()
