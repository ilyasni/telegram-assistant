"""
HTTP Health Server для Worker сервиса.
Предоставляет health endpoints для мониторинга.
"""

import asyncio
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Dict, Any
import structlog
from health import check_integrations

logger = structlog.get_logger()

class WorkerHealthHandler(BaseHTTPRequestHandler):
    """HTTP handler для health checks Worker'а."""
    
    def do_GET(self):
        """Обработка GET запросов."""
        if self.path == "/health":
            self._handle_health()
        elif self.path == "/health/detailed":
            self._handle_detailed_health()
        elif self.path == "/health/tasks":
            self._handle_tasks_health()
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
                "service": "worker",
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
        """Детальный health check с проверкой интеграций."""
        try:
            # Запускаем проверку интеграций в новом event loop
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                integrations = loop.run_until_complete(check_integrations())
            finally:
                loop.close()
            
            # Определяем общий статус
            all_healthy = all(
                check.get("status") in ["ok", "healthy"] 
                for check in integrations.values()
            )
            
            response = {
                "status": "healthy" if all_healthy else "degraded",
                "service": "worker",
                "timestamp": time.time(),
                "integrations": integrations
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
    
    def _handle_tasks_health(self):
        """Context7: Health check для всех задач через supervisor."""
        try:
            # Получаем supervisor из глобального контекста
            supervisor = getattr(self.server, 'supervisor', None)
            
            if supervisor is None:
                response = {
                    "status": "unknown",
                    "error": "Supervisor not available",
                    "timestamp": time.time()
                }
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response).encode())
                return
            
            # Context7: Получаем статус всех задач через supervisor.get_status()
            task_status = supervisor.get_status()
            
            # Определяем общий статус
            all_running = all(
                task_info.get('status') == 'running'
                for task_info in task_status.get('tasks', {}).values()
            )
            
            response = {
                "status": "healthy" if all_running else "degraded",
                "service": "worker",
                "timestamp": time.time(),
                "supervisor": task_status.get('supervisor', {}),
                "tasks": task_status.get('tasks', {})
            }
            
            status_code = 200 if all_running else 503
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response, default=str).encode())
            
        except Exception as e:
            logger.error("Tasks health check failed", error=str(e), exc_info=True)
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            error_response = {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": time.time()
            }
            self.wfile.write(json.dumps(error_response).encode())
    
    def _handle_metrics(self):
        """Prometheus метрики для Worker'а."""
        try:
            # Базовые метрики Worker'а
            metrics = [
                "# HELP worker_uptime_seconds Worker uptime in seconds",
                "# TYPE worker_uptime_seconds gauge",
                f"worker_uptime_seconds {time.time() - getattr(self.server, 'start_time', time.time())}",
                "",
                "# HELP worker_health_status Worker health status (1=healthy, 0=unhealthy)",
                "# TYPE worker_health_status gauge",
                "worker_health_status 1",
                "",
                "# HELP worker_http_requests_total Total HTTP requests to worker health endpoint",
                "# TYPE worker_http_requests_total counter",
                "worker_http_requests_total{method=\"GET\",endpoint=\"/health\"} 1",
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

class WorkerHealthServer:
    """HTTP сервер для health checks Worker'а."""
    
    def __init__(self, port: int = 8001, supervisor=None):
        self.port = port
        self.server = None
        self.thread = None
        self.running = False
        self.supervisor = supervisor
    
    def start(self):
        """Запуск health сервера в отдельном потоке."""
        if self.running:
            return
        
        try:
            self.server = HTTPServer(('0.0.0.0', self.port), WorkerHealthHandler)
            self.server.start_time = time.time()
            # Context7: Передаем supervisor в server для доступа из handler
            self.server.supervisor = self.supervisor
            
            self.thread = Thread(target=self._run_server, daemon=True)
            self.thread.start()
            
            self.running = True
            logger.info(f"Worker health server started on port {self.port}")
            
        except Exception as e:
            logger.error(f"Failed to start worker health server: {e}")
            raise
    
    def _run_server(self):
        """Запуск HTTP сервера."""
        try:
            self.server.serve_forever()
        except Exception as e:
            logger.error(f"Worker health server error: {e}")
    
    def stop(self):
        """Остановка health сервера."""
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        
        self.running = False
        logger.info("Worker health server stopped")

# Глобальный экземпляр сервера (будет инициализирован с supervisor)
health_server = None

def start_health_server(supervisor=None):
    """Context7: Запуск health сервера с supervisor для мониторинга задач."""
    global health_server
    if health_server is None:
        health_server = WorkerHealthServer(supervisor=supervisor)
    health_server.supervisor = supervisor  # Обновляем supervisor
    health_server.start()

def stop_health_server():
    """Остановка health сервера."""
    global health_server
    if health_server:
        health_server.stop()
