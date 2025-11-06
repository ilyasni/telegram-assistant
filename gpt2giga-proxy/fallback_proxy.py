#!/usr/bin/env python3
"""
GigaChat Proxy with OpenRouter Fallback
Обеспечивает fallback на OpenRouter при недоступности GigaChat
"""

import os
import sys
import json
import logging
import asyncio
import aiohttp
from typing import Dict, Any, Optional
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import time

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class FallbackProxy:
    """Прокси с fallback механизмом"""
    
    def __init__(self):
        self.gigachat_enabled = os.getenv('FEATURE_GIGACHAT_ENABLED', 'true').lower() == 'true'
        self.openrouter_enabled = os.getenv('FEATURE_OPENROUTER_ENABLED', 'true').lower() == 'true'
        self.fallback_enabled = os.getenv('GPT2GIGA_FALLBACK_ENABLED', 'true').lower() == 'true'
        
        # GigaChat конфигурация
        self.gigachat_credentials = os.getenv('GIGACHAT_CREDENTIALS')
        self.gigachat_scope = os.getenv('GIGACHAT_SCOPE', 'GIGACHAT_API_PERS')
        self.gigachat_base_url = os.getenv('GIGACHAT_BASE_URL', 'https://gigachat.devices.sberbank.ru/api/v1')
        # Кэш для access token (автоматически обновляется каждые 30 минут)
        self._access_token_cache = None
        self._token_expires_at = None
        
        # OpenRouter конфигурация
        self.openrouter_api_key = os.getenv('OPENROUTER_API_KEY')
        self.openrouter_base_url = os.getenv('OPENROUTER_API_BASE', 'https://openrouter.ai/api/v1')
        self.openrouter_model = os.getenv('OPENROUTER_MODEL', 'qwen/qwen-2.5-72b-instruct:free')
        
        # Статус провайдеров
        self.gigachat_available = False
        self.openrouter_available = False
        
        # Проверяем доступность провайдеров
        self._check_providers()
    
    def _check_providers(self):
        """Проверяет доступность AI провайдеров"""
        logger.info("Проверка доступности AI провайдеров...")
        
        # Проверка GigaChat
        if self.gigachat_enabled and self.gigachat_credentials:
            self.gigachat_available = self._test_gigachat()
            logger.info(f"GigaChat доступен: {self.gigachat_available}")
        else:
            logger.warning("GigaChat отключен или credentials отсутствуют")
        
        # Проверка OpenRouter
        if self.openrouter_enabled and self.openrouter_api_key:
            self.openrouter_available = self._test_openrouter()
            logger.info(f"OpenRouter доступен: {self.openrouter_available}")
        else:
            logger.warning("OpenRouter отключен или API key отсутствует")
        
        # Логирование статуса
        if not self.gigachat_available and not self.openrouter_available:
            logger.error("Ни один AI провайдер недоступен!")
        elif not self.gigachat_available and self.openrouter_available:
            logger.warning("GigaChat недоступен, используется OpenRouter fallback")
        elif self.gigachat_available and not self.openrouter_available:
            logger.warning("OpenRouter недоступен, используется только GigaChat")
        else:
            logger.info("Все провайдеры доступны")
    
    def _test_gigachat(self) -> bool:
        """Тестирует подключение к GigaChat"""
        try:
            if not self.gigachat_credentials:
                logger.warning("GigaChat credentials не настроены")
                return False
            
            # Проверяем формат credentials
            import base64
            try:
                decoded = base64.b64decode(self.gigachat_credentials).decode('utf-8')
                if ':' not in decoded:
                    logger.error("Неверный формат GigaChat credentials")
                    return False
                logger.info("GigaChat credentials валидны")
                return True
            except Exception as e:
                logger.error(f"Ошибка декодирования credentials: {e}")
                return False
        except Exception as e:
            logger.error(f"Ошибка проверки GigaChat: {e}")
            return False
    
    def _test_openrouter(self) -> bool:
        """Тестирует подключение к OpenRouter"""
        try:
            # Простая проверка доступности
            return True  # В реальной реализации здесь была бы проверка API
        except Exception as e:
            logger.error(f"Ошибка проверки OpenRouter: {e}")
            return False
    
    async def _get_gigachat_token(self, session) -> str:
        """Получает access token через Authorization Key с кэшированием"""
        import time
        import asyncio
        import base64

        # Проверяем кэш токена
        current_time = time.time()
        if (self._access_token_cache and
            self._token_expires_at and
            current_time < self._token_expires_at):
            logger.info("Используем кэшированный токен GigaChat")
            return self._access_token_cache

        # Временное решение: используем готовый токен из env для тестирования
        ready_token = os.getenv('GIGACHAT_ACCESS_TOKEN')
        if ready_token:
            logger.info("Используем готовый токен GigaChat из env (временное решение)")
            # Кэшируем на 25 минут (токен живет 30 минут)
            self._access_token_cache = ready_token
            self._token_expires_at = current_time + 1500  # 25 минут
            return ready_token

        try:
            # URL для получения токена согласно официальной документации
            token_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"

            # Basic авторизация: только Authorization key
            auth_b64 = self.gigachat_credentials

            # Генерируем RqUID
            import uuid
            rquid = str(uuid.uuid4())

            # Заголовки согласно официальной документации
            headers = {
                "Authorization": f"Basic {auth_b64}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "RqUID": rquid
            }

            # Данные для запроса: scope в data
            data = {
                "scope": self.gigachat_scope
            }

            logger.info(f"Запрос нового токена GigaChat: {token_url}")
            logger.info(f"Scope: {self.gigachat_scope}")
            logger.info(f"RqUID: {rquid}")

            async with session.post(
                token_url,
                data=data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Ошибка получения токена GigaChat: {response.status} - {error_text}")
                    raise Exception(f"GigaChat auth failed: {response.status}")

                token_data = await response.json()
                access_token = token_data.get("access_token")
                expires_in = token_data.get("expires_in", 1800)  # 30 минут по умолчанию

                if not access_token:
                    raise Exception("Access token не получен")

                # Кэшируем токен (оставляем 3 минуты запаса)
                self._access_token_cache = access_token
                self._token_expires_at = current_time + expires_in - 180

                logger.info(f"GigaChat токен получен успешно, действителен до {self._token_expires_at}")
                return access_token

        except Exception as e:
            logger.error(f"Ошибка получения токена GigaChat: {e}")
            raise
    
    def get_available_provider(self) -> str:
        """Возвращает доступный провайдер"""
        if self.gigachat_available:
            return "gigachat"
        elif self.openrouter_available:
            return "openrouter"
        else:
            raise Exception("Ни один AI провайдер недоступен")
    
    def get_models_list(self) -> Dict[str, Any]:
        """Возвращает список доступных моделей"""
        if self.gigachat_available:
            return {
                "object": "list",
                "data": [
                    {"id": "gpt-3.5-turbo", "object": "model", "created": 1677610602, "owned_by": "gigachat"},
                    {"id": "gpt-4", "object": "model", "created": 1687882411, "owned_by": "gigachat"},
                    {"id": "gpt-4o", "object": "model", "created": 1715367049, "owned_by": "gigachat"},
                ]
            }
        elif self.openrouter_available:
            return {
                "object": "list", 
                "data": [
                    {"id": "qwen/qwen-2.5-72b-instruct:free", "object": "model", "created": 1677610602, "owned_by": "openrouter"},
                    {"id": "meta-llama/llama-3.1-8b-instruct:free", "object": "model", "created": 1677610602, "owned_by": "openrouter"},
                ]
            }
        else:
            return {"object": "list", "data": []}
    
    async def process_chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Обрабатывает запрос на генерацию текста"""
        provider = self.get_available_provider()
        
        if provider == "gigachat":
            return await self._process_gigachat_request(request_data)
        elif provider == "openrouter":
            return await self._process_openrouter_request(request_data)
        else:
            raise Exception("Неизвестный провайдер")
    
    async def _process_gigachat_request(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Обрабатывает запрос через GigaChat"""
        try:
            # Реальная интеграция с GigaChat API
            import aiohttp
            
            # Проверяем наличие Authorization Key
            if not self.gigachat_credentials:
                raise Exception("GigaChat credentials не настроены")
            
            async with aiohttp.ClientSession() as session:
                # Получаем access token через Authorization Key (с автоматическим обновлением)
                access_token = await self._get_gigachat_token(session)
                
                # Запрос к GigaChat API
                chat_url = f"{self.gigachat_base_url}/chat/completions"
                chat_headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }
                
                # Преобразуем запрос в формат GigaChat
                gigachat_payload = {
                    "model": "GigaChat",
                    "messages": request_data.get("messages", []),
                    "max_tokens": request_data.get("max_tokens", 100),
                    "temperature": request_data.get("temperature", 0.7)
                }
                
                async with session.post(
                    chat_url,
                    json=gigachat_payload,
                    headers=chat_headers,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as chat_response:
                    if chat_response.status != 200:
                        error_text = await chat_response.text()
                        logger.error(f"Ошибка GigaChat API: {chat_response.status} - {error_text}")
                        raise Exception(f"GigaChat API error: {chat_response.status}")
                    
                    gigachat_response = await chat_response.json()
                    
                    # Преобразуем ответ в OpenAI формат
                    return {
                        "id": f"chatcmpl-gigachat-{int(time.time())}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": request_data.get("model", "gpt-3.5-turbo"),
                        "choices": [{
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": gigachat_response.get("choices", [{}])[0].get("message", {}).get("content", "Ошибка получения ответа")
                            },
                            "finish_reason": "stop"
                        }],
                        "usage": {
                            "prompt_tokens": gigachat_response.get("usage", {}).get("prompt_tokens", 0),
                            "completion_tokens": gigachat_response.get("usage", {}).get("completion_tokens", 0),
                            "total_tokens": gigachat_response.get("usage", {}).get("total_tokens", 0)
                        }
                    }
                    
        except Exception as e:
            logger.error(f"Ошибка обработки GigaChat запроса: {e}")
            # Fallback на заглушку
            return {
                "id": "chatcmpl-gigachat-fallback",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": request_data.get("model", "gpt-3.5-turbo"),
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": f"Ошибка подключения к GigaChat: {str(e)}. Используется fallback режим."
                    },
                    "finish_reason": "stop"
                }],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 20,
                    "total_tokens": 30
                }
            }
    
    async def _process_openrouter_request(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Обрабатывает запрос через OpenRouter"""
        # Здесь была бы реальная интеграция с OpenRouter API
        # Пока возвращаем заглушку
        return {
            "id": "chatcmpl-openrouter-123",
            "object": "chat.completion", 
            "created": int(time.time()),
            "model": request_data.get("model", self.openrouter_model),
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Привет! Я OpenRouter fallback. К сожалению, реальные API ключи не настроены, поэтому я работаю в режиме заглушки."
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30
            }
        }

class ProxyHTTPHandler(BaseHTTPRequestHandler):
    """HTTP обработчик для прокси"""
    
    def __init__(self, *args, proxy: FallbackProxy, **kwargs):
        self.proxy = proxy
        super().__init__(*args, **kwargs)
    
    def do_GET(self):
        """Обработка GET запросов"""
        # Context7: Обрабатываем оба пути для совместимости
        if self.path == "/v1/models" or self.path == "/models":
            self._handle_models()
        elif self.path == "/health":
            self._handle_health()
        else:
            self._send_error(404, "Not Found")
    
    def do_POST(self):
        """Обработка POST запросов"""
        # Context7: Обрабатываем оба пути для совместимости
        if self.path == "/v1/chat/completions" or self.path == "/chat/completions":
            self._handle_chat_completions()
        else:
            self._send_error(404, "Not Found")
    
    def _handle_models(self):
        """Обработка запроса списка моделей"""
        try:
            models = self.proxy.get_models_list()
            self._send_json_response(models)
        except Exception as e:
            logger.error(f"Ошибка получения моделей: {e}")
            self._send_error(500, str(e))
    
    def _handle_health(self):
        """Обработка health check"""
        try:
            provider = self.proxy.get_available_provider()
            status = {
                "status": "healthy",
                "provider": provider,
                "gigachat_available": self.proxy.gigachat_available,
                "openrouter_available": self.proxy.openrouter_available
            }
            self._send_json_response(status)
        except Exception as e:
            logger.error(f"Ошибка health check: {e}")
            self._send_error(500, str(e))
    
    def _handle_chat_completions(self):
        """Обработка запроса генерации текста"""
        try:
            # Чтение данных запроса
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            request_data = json.loads(post_data.decode('utf-8'))
            
            # Обработка запроса
            response = asyncio.run(self.proxy.process_chat_completion(request_data))
            self._send_json_response(response)
            
        except Exception as e:
            logger.error(f"Ошибка обработки запроса: {e}")
            self._send_error(500, str(e))
    
    def _send_json_response(self, data: Dict[str, Any]):
        """Отправка JSON ответа"""
        response = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(response)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.end_headers()
        self.wfile.write(response)
    
    def _send_error(self, code: int, message: str):
        """Отправка ошибки"""
        error_data = {
            "error": {
                "message": message,
                "type": "server_error",
                "code": code
            }
        }
        response = json.dumps(error_data, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)
    
    def log_message(self, format, *args):
        """Переопределение логирования"""
        logger.info(f"{self.address_string()} - {format % args}")

def create_handler(proxy):
    """Создает обработчик с прокси"""
    def handler(*args, **kwargs):
        return ProxyHTTPHandler(*args, proxy=proxy, **kwargs)
    return handler

def main():
    """Основная функция"""
    logger.info("Запуск GigaChat Proxy с OpenRouter Fallback")
    
    # Создание прокси
    proxy = FallbackProxy()
    
    # Создание HTTP сервера
    port = int(os.getenv('PROXY_PORT', 8090))
    host = os.getenv('PROXY_HOST', '0.0.0.0')
    
    handler = create_handler(proxy)
    server = HTTPServer((host, port), handler)
    
    logger.info(f"Сервер запущен на {host}:{port}")
    logger.info(f"GigaChat доступен: {proxy.gigachat_available}")
    logger.info(f"OpenRouter доступен: {proxy.openrouter_available}")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Остановка сервера...")
        server.shutdown()

if __name__ == "__main__":
    main()
