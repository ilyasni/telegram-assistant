#!/usr/bin/env python3
"""
Простой HTTP Health Check Server для worker сервисов
Context7: Минимальный сервер без внешних зависимостей
"""

import asyncio
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import structlog

logger = structlog.get_logger()

class SimpleHealthHandler(BaseHTTPRequestHandler):
    """Простой HTTP handler для health checks."""
    
    def do_GET(self):
        """Обработка GET запросов."""
        if self.path == '/ping':
            self._handle_ping()
        elif self.path == '/health':
            self._handle_health()
        elif self.path == '/health/live':
            self._handle_health()
        else:
            self._handle_not_found()
    
    def _handle_ping(self):
        """Простой ping endpoint."""
        try:
            response = {
                "status": "pong",
                "timestamp": time.time(),
                "service": "telegram-assistant-worker"
            }
            self._send_json_response(200, response)
        except Exception as e:
            logger.error("Ping failed", error=str(e))
            self._send_json_response(500, {"error": str(e)})
    
    def _handle_health(self):
        """Liveness probe."""
        try:
            response = {
                "status": "alive",
                "timestamp": time.time(),
                "service": "telegram-assistant-worker"
            }
            self._send_json_response(200, response)
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            self._send_json_response(503, {"error": str(e)})
    
    def _handle_not_found(self):
        """404 handler."""
        self._send_json_response(404, {"error": "Not found"})
    
    def _send_json_response(self, status_code, data):
        """Отправка JSON ответа."""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))
    
    def log_message(self, format, *args):
        """Отключение стандартного логирования HTTP сервера."""
        pass

class SimpleHealthServer:
    """Простой HTTP сервер для health checks."""
    
    def __init__(self, host: str = "0.0.0.0", port: int = 8080):
        self.host = host
        self.port = port
        self.server = None
        self.thread = None
    
    def start(self):
        """Запуск сервера в отдельном потоке."""
        try:
            self.server = HTTPServer((self.host, self.port), SimpleHealthHandler)
            self.thread = Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            
            logger.info(
                "Simple health check server started",
                host=self.host,
                port=self.port,
                endpoints=["/ping", "/health", "/health/live"]
            )
            
            return True
        except Exception as e:
            logger.error("Failed to start simple health server", error=str(e))
            return False
    
    def stop(self):
        """Остановка сервера."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            logger.info("Simple health check server stopped")

# Context7: Глобальный экземпляр сервера
_health_server = None

def start_simple_health_server(host: str = "0.0.0.0", port: int = 8080):
    """Запуск простого health сервера."""
    global _health_server
    if _health_server is None:
        _health_server = SimpleHealthServer(host, port)
        return _health_server.start()
    return True

def stop_simple_health_server():
    """Остановка простого health сервера."""
    global _health_server
    if _health_server:
        _health_server.stop()
        _health_server = None
