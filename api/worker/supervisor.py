"""
Task Supervisor для автоматического перезапуска упавших tasks.
Реализует supervisor pattern с exponential backoff и graceful degradation.
"""

import asyncio
import logging
import time
from typing import Callable, Dict, Optional, Any
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class TaskConfig:
    """Конфигурация task для supervisor."""
    name: str
    task_func: Callable
    max_retries: int = 5
    initial_backoff: float = 1.0
    max_backoff: float = 60.0
    backoff_multiplier: float = 2.0
    health_check_interval: float = 30.0  # секунд

class TaskSupervisor:
    """
    Supervisor для автоматического перезапуска упавших tasks.
    
    Best Practice (из FastStream): Изоляция обязанностей, self-healing, наблюдаемость
    """
    
    def __init__(self):
        self.tasks: Dict[str, asyncio.Task] = {}
        self.task_configs: Dict[str, TaskConfig] = {}
        self.retry_counts: Dict[str, int] = {}
        self.last_success: Dict[str, float] = {}
        self.running = False
        self.start_time = time.time()
    
    def register_task(self, config: TaskConfig):
        """Регистрация task для мониторинга."""
        self.task_configs[config.name] = config
        self.retry_counts[config.name] = 0
        self.last_success[config.name] = 0
        logger.info(f"Registered task: {config.name}")
    
    async def start_task(self, name: str):
        """Запуск отдельного task с автоперезапуском."""
        config = self.task_configs[name]
        
        async def wrapped_task():
            backoff = config.initial_backoff
            
            while self.running:
                try:
                    logger.info(f"Starting task: {name}")
                    start_time = time.time()
                    
                    # Context7: Запуск task в фоне - задачи работают в бесконечном цикле
                    task_coroutine = config.task_func()
                    task_handle = asyncio.create_task(task_coroutine, name=f"{name}_worker")
                    
                    # Отслеживание выполнения задачи
                    while not task_handle.done() and self.running:
                        try:
                            # Проверяем каждые 5 секунд
                            await asyncio.wait_for(asyncio.shield(task_handle), timeout=5.0)
                            # Если задача завершилась, выходим из цикла
                            break
                        except asyncio.TimeoutError:
                            # Задача все еще работает - обновляем last_success
                            self.last_success[name] = time.time()
                            self.retry_counts[name] = 0
                            continue
                    
                    # Успешное выполнение
                    self.last_success[name] = time.time()
                    self.retry_counts[name] = 0
                    backoff = config.initial_backoff
                    
                    logger.info(f"Task {name} completed successfully in {time.time() - start_time:.2f}s")
                    
                except asyncio.CancelledError:
                    logger.info(f"Task cancelled: {name}")
                    break
                except Exception as e:
                    self.retry_counts[name] += 1
                    error_msg = f"Task {name} failed (retry {self.retry_counts[name]}): {e}"
                    
                    if self.retry_counts[name] >= config.max_retries:
                        logger.critical(f"Task {name} exceeded max retries ({config.max_retries}), stopping")
                        break
                    
                    logger.error(error_msg)
                    
                    # Exponential backoff
                    logger.info(f"Retrying {name} in {backoff:.1f}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * config.backoff_multiplier, config.max_backoff)
        
        task = asyncio.create_task(wrapped_task(), name=name)
        self.tasks[name] = task
        return task
    
    async def start_all(self):
        """Запуск всех зарегистрированных tasks."""
        self.running = True
        self.start_time = time.time()
        
        logger.info(f"Starting supervisor with {len(self.task_configs)} tasks")
        
        # Запуск всех tasks
        tasks = []
        for name in self.task_configs.keys():
            task = await self.start_task(name)
            tasks.append(task)
        
        # Мониторинг health check
        health_task = asyncio.create_task(self._health_check_loop())
        tasks.append(health_task)
        
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Supervisor error: {e}")
            raise
    
    async def _health_check_loop(self):
        """Цикл проверки здоровья tasks."""
        while self.running:
            try:
                await asyncio.sleep(30)  # Проверка каждые 30 секунд
                
                for name, config in self.task_configs.items():
                    if name in self.tasks:
                        task = self.tasks[name]
                        
                        # Проверка на зависшие tasks
                        if task.done():
                            logger.warning(f"Task {name} completed unexpectedly, will be restarted")
                            # Task будет автоматически перезапущен в start_task
                        elif time.time() - self.last_success.get(name, 0) > config.health_check_interval * 2:
                            logger.warning(f"Task {name} appears stuck, last success: {self.last_success.get(name, 0)}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Health check error: {e}")
    
    async def stop_all(self):
        """Остановка всех tasks."""
        logger.info("Stopping supervisor...")
        self.running = False
        
        # Отмена всех tasks
        for name, task in self.tasks.items():
            if not task.done():
                logger.info(f"Cancelling task: {name}")
                task.cancel()
        
        # Ожидание завершения
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        
        logger.info("Supervisor stopped")
    
    def get_status(self) -> Dict[str, Any]:
        """Получение статуса supervisor и всех tasks."""
        uptime = time.time() - self.start_time
        
        task_statuses = {}
        for name, task in self.tasks.items():
            task_statuses[name] = {
                'status': 'running' if not task.done() else 'stopped',
                'retry_count': self.retry_counts.get(name, 0),
                'last_success': self.last_success.get(name, 0),
                'uptime': uptime - self.last_success.get(name, 0) if self.last_success.get(name, 0) > 0 else uptime
            }
        
        return {
            'supervisor': {
                'running': self.running,
                'uptime': uptime,
                'total_tasks': len(self.tasks),
                'active_tasks': sum(1 for task in self.tasks.values() if not task.done())
            },
            'tasks': task_statuses
        }
    
    async def restart_task(self, name: str):
        """Ручной перезапуск конкретного task."""
        if name in self.tasks:
            logger.info(f"Manually restarting task: {name}")
            self.tasks[name].cancel()
            await self.tasks[name]
            self.tasks[name] = await self.start_task(name)
        else:
            logger.warning(f"Task {name} not found")
    
    async def get_task_logs(self, name: str, lines: int = 50) -> str:
        """Получение логов конкретного task (заглушка для будущей реализации)."""
        return f"Logs for task {name} (last {lines} lines) - not implemented yet"
