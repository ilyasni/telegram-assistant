"""
GigaChat Queue Manager - Context7 best practice для управления очередью запросов
Обеспечивает соблюдение лимита в 1 одновременный запрос к GigaChat API
"""

import asyncio
import time
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import json

logger = logging.getLogger(__name__)

class RequestStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class QueueItem:
    """Элемент очереди для GigaChat запросов."""
    id: str
    request_data: Dict[str, Any]
    callback: callable
    created_at: float
    status: RequestStatus = RequestStatus.PENDING
    result: Optional[Any] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3

class GigaChatQueueManager:
    """
    Менеджер очереди для GigaChat API с соблюдением лимита в 1 поток.
    
    Context7 best practice:
    - Семафор для ограничения одновременных запросов
    - Очередь с приоритетами
    - Автоматический retry с экспоненциальным backoff
    - Fallback на OpenRouter при недоступности GigaChat
    """
    
    def __init__(self, max_concurrent: int = 1, fallback_enabled: bool = True):
        self.max_concurrent = max_concurrent
        self.fallback_enabled = fallback_enabled
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.processing_items: Dict[str, QueueItem] = {}
        self.completed_items: Dict[str, QueueItem] = {}
        self.is_running = False
        self.worker_task: Optional[asyncio.Task] = None
        
        # Статистика
        self.stats = {
            'total_requests': 0,
            'completed_requests': 0,
            'failed_requests': 0,
            'retry_requests': 0,
            'fallback_requests': 0
        }
    
    async def start(self):
        """Запуск менеджера очереди."""
        if self.is_running:
            return
            
        self.is_running = True
        self.worker_task = asyncio.create_task(self._worker_loop())
        logger.info("GigaChat Queue Manager started")
    
    async def stop(self):
        """Остановка менеджера очереди."""
        self.is_running = False
        if self.worker_task:
            self.worker_task.cancel()
            try:
                await self.worker_task
            except asyncio.CancelledError:
                pass
        logger.info("GigaChat Queue Manager stopped")
    
    async def add_request(
        self, 
        request_id: str, 
        request_data: Dict[str, Any], 
        callback: callable,
        priority: int = 0
    ) -> str:
        """
        Добавление запроса в очередь.
        
        Args:
            request_id: Уникальный ID запроса
            request_data: Данные запроса
            callback: Функция обратного вызова
            priority: Приоритет (меньше = выше приоритет)
        """
        item = QueueItem(
            id=request_id,
            request_data=request_data,
            callback=callback,
            created_at=time.time()
        )
        
        await self.queue.put((priority, item))
        self.stats['total_requests'] += 1
        
        logger.debug(f"Added request to queue: {request_id}")
        return request_id
    
    async def _worker_loop(self):
        """Основной цикл обработки очереди."""
        while self.is_running:
            try:
                # Получаем следующий элемент из очереди
                priority, item = await asyncio.wait_for(
                    self.queue.get(), 
                    timeout=1.0
                )
                
                # Обрабатываем запрос
                await self._process_item(item)
                
            except asyncio.TimeoutError:
                # Нет элементов в очереди, продолжаем
                continue
            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                await asyncio.sleep(1)
    
    async def _process_item(self, item: QueueItem):
        """Обработка одного элемента очереди."""
        async with self.semaphore:
            try:
                item.status = RequestStatus.PROCESSING
                self.processing_items[item.id] = item
                
                logger.debug(f"Processing request: {item.id}")
                
                # Попытка обработки через GigaChat
                result = await self._process_with_gigachat(item)
                
                if result is not None:
                    # Успешная обработка
                    item.result = result
                    item.status = RequestStatus.COMPLETED
                    self.stats['completed_requests'] += 1
                    
                    # Вызываем callback
                    if item.callback:
                        await item.callback(item.id, result, None)
                        
                else:
                    # Fallback на OpenRouter
                    if self.fallback_enabled:
                        result = await self._process_with_openrouter(item)
                        if result is not None:
                            item.result = result
                            item.status = RequestStatus.COMPLETED
                            self.stats['completed_requests'] += 1
                            self.stats['fallback_requests'] += 1
                            
                            if item.callback:
                                await item.callback(item.id, result, None)
                        else:
                            await self._handle_failure(item, "Both GigaChat and OpenRouter failed")
                    else:
                        await self._handle_failure(item, "GigaChat failed and fallback disabled")
                
            except Exception as e:
                await self._handle_failure(item, str(e))
            finally:
                # Перемещаем в завершенные
                if item.id in self.processing_items:
                    del self.processing_items[item.id]
                self.completed_items[item.id] = item
    
    async def _process_with_gigachat(self, item: QueueItem) -> Optional[Any]:
        """Обработка запроса через GigaChat API."""
        try:
            # Здесь должна быть логика вызова GigaChat API
            # Пока возвращаем None для демонстрации fallback
            logger.debug(f"Processing with GigaChat: {item.id}")
            
            # Имитация задержки API
            await asyncio.sleep(0.1)
            
            # Проверяем, не превышен ли лимит
            if self._is_gigachat_rate_limited():
                logger.warning(f"GigaChat rate limited, skipping: {item.id}")
                return None
            
            # Здесь должен быть реальный вызов GigaChat API
            # result = await gigachat_client.generate_tags(...)
            # return result
            
            return None  # Временно возвращаем None для тестирования fallback
            
        except Exception as e:
            logger.error(f"GigaChat processing error for {item.id}: {e}")
            return None
    
    async def _process_with_openrouter(self, item: QueueItem) -> Optional[Any]:
        """Обработка запроса через OpenRouter API (fallback)."""
        try:
            logger.debug(f"Processing with OpenRouter fallback: {item.id}")
            
            # Здесь должна быть логика вызова OpenRouter API
            # Пока возвращаем заглушку
            await asyncio.sleep(0.1)
            
            # Имитация успешного ответа от OpenRouter
            return {
                "tags": ["технологии", "искусственный интеллект", "машинное обучение"],
                "provider": "openrouter",
                "model": "qwen/qwen-2.5-72b-instruct:free"
            }
            
        except Exception as e:
            logger.error(f"OpenRouter processing error for {item.id}: {e}")
            return None
    
    def _is_gigachat_rate_limited(self) -> bool:
        """Проверка, не превышен ли лимит GigaChat API."""
        # Здесь должна быть логика проверки лимитов
        # Пока возвращаем True для тестирования fallback
        return True
    
    async def _handle_failure(self, item: QueueItem, error: str):
        """Обработка неудачного запроса."""
        item.error = error
        item.retry_count += 1
        
        if item.retry_count < item.max_retries:
            # Повторная попытка
            item.status = RequestStatus.PENDING
            item.error = None
            self.stats['retry_requests'] += 1
            
            # Экспоненциальный backoff
            delay = min(2 ** item.retry_count, 60)
            await asyncio.sleep(delay)
            
            # Возвращаем в очередь
            await self.queue.put((0, item))
            logger.info(f"Retrying request {item.id} (attempt {item.retry_count})")
        else:
            # Исчерпаны попытки
            item.status = RequestStatus.FAILED
            self.stats['failed_requests'] += 1
            
            if item.callback:
                await item.callback(item.id, None, error)
            
            logger.error(f"Request {item.id} failed after {item.max_retries} retries: {error}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики очереди."""
        return {
            **self.stats,
            'queue_size': self.queue.qsize(),
            'processing_count': len(self.processing_items),
            'completed_count': len(self.completed_items)
        }
    
    def get_item_status(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Получение статуса конкретного элемента."""
        if item_id in self.processing_items:
            item = self.processing_items[item_id]
        elif item_id in self.completed_items:
            item = self.completed_items[item_id]
        else:
            return None
        
        return {
            'id': item.id,
            'status': item.status.value,
            'created_at': item.created_at,
            'retry_count': item.retry_count,
            'error': item.error,
            'result': item.result
        }

# Глобальный экземпляр менеджера очереди
queue_manager = GigaChatQueueManager(max_concurrent=1, fallback_enabled=True)
