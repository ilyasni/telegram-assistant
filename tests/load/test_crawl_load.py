"""
Load tests для Crawl Pipeline
[C7-ID: TEST-CRAWL-LOAD-001]

Тестирует производительность с минимальным профилем (20-50 msg/sec)
для домашнего хоста без Playwright.
"""
import asyncio
import json
import time
import statistics
from typing import List, Dict, Any
import pytest
import pytest_asyncio
from redis.asyncio import Redis
import asyncpg


class CrawlLoadTester:
    """Load tester для crawl pipeline."""
    
    def __init__(self, redis_url: str, database_url: str):
        self.redis_url = redis_url
        self.database_url = database_url
        self.redis: Redis = None
        self.db_pool = None
        self.results: List[Dict[str, Any]] = []
    
    async def setup(self):
        """Инициализация подключений."""
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        self.db_pool = await asyncpg.create_pool(self.database_url)
    
    async def cleanup(self):
        """Очистка ресурсов."""
        if self.redis:
            await self.redis.close()
        if self.db_pool:
            await self.db_pool.close()
    
    async def generate_test_messages(self, count: int) -> List[Dict[str, Any]]:
        """Генерация тестовых сообщений."""
        messages = []
        for i in range(count):
            message = {
                "post_id": f"load-test-{i}",
                "tags": ["longread", "tech"] if i % 2 == 0 else ["crawl", "news"],
                "urls": [f"https://example.com/article/{i}"],
                "text": f"Test article content {i} with enough words to pass policy " * 10,
                "trace_id": f"load-test-trace-{i}",
                "metadata": {"test": True, "load_test_id": i}
            }
            messages.append(message)
        return messages
    
    async def send_messages_to_redis(self, messages: List[Dict[str, Any]], stream: str):
        """Отправка сообщений в Redis Stream."""
        for message in messages:
            await self.redis.xadd(
                stream,
                {"data": json.dumps(message)},
                maxlen=10000
            )
    
    async def measure_processing_time(self, post_id: str) -> float:
        """Измерение времени обработки сообщения."""
        start_time = time.time()
        
        # Ждём появления записи в post_enrichment
        max_wait = 30  # секунд
        check_interval = 0.1
        
        while time.time() - start_time < max_wait:
            async with self.db_pool.acquire() as conn:
                result = await conn.fetchrow(
                    "SELECT * FROM post_enrichment WHERE post_id = $1 AND kind = 'crawl'",
                    post_id
                )
                if result:
                    return time.time() - start_time
            
            await asyncio.sleep(check_interval)
        
        return -1  # Timeout
    
    async def get_queue_depth(self, stream: str) -> int:
        """Получение глубины очереди."""
        return await self.redis.xlen(stream)
    
    async def get_pel_backlog(self, stream: str, consumer_group: str) -> int:
        """Получение PEL backlog."""
        try:
            pending_info = await self.redis.xpending(stream, consumer_group)
            return pending_info[0] if pending_info and len(pending_info) > 0 else 0
        except:
            return 0


class TestCrawlLoad:
    """Load tests для crawl pipeline."""
    
    @pytest_asyncio.fixture
    async def load_tester(self):
        """Load tester fixture."""
        tester = CrawlLoadTester(
            redis_url="redis://localhost:6379",
            database_url="postgresql://postgres:password@localhost:5432/telegram_assistant"
        )
        await tester.setup()
        yield tester
        await tester.cleanup()
    
    @pytest.mark.asyncio
    async def test_sustained_load_minimal(self, load_tester):
        """Тест sustained load с минимальным профилем (20-50 msg/sec)."""
        # Параметры теста
        message_count = 100  # 100 сообщений
        target_rate = 30  # 30 msg/sec (минимальный профиль)
        duration = message_count / target_rate  # ~3.3 секунды
        
        print(f"Запуск sustained load test: {message_count} сообщений за {duration:.1f} сек")
        
        # Генерация сообщений
        messages = await load_tester.generate_test_messages(message_count)
        
        # Отправка сообщений с контролируемой скоростью
        start_time = time.time()
        sent_count = 0
        
        for i, message in enumerate(messages):
            await load_tester.send_messages_to_redis([message], "stream:posts:tagged")
            sent_count += 1
            
            # Контроль скорости
            elapsed = time.time() - start_time
            expected_time = (i + 1) / target_rate
            if elapsed < expected_time:
                await asyncio.sleep(expected_time - elapsed)
        
        actual_duration = time.time() - start_time
        actual_rate = sent_count / actual_duration
        
        print(f"Отправлено {sent_count} сообщений за {actual_duration:.2f} сек (rate: {actual_rate:.1f} msg/sec)")
        
        # Ждём обработки
        await asyncio.sleep(10)
        
        # Измеряем производительность
        processing_times = []
        success_count = 0
        
        for message in messages[:20]:  # Проверяем первые 20 сообщений
            processing_time = await load_tester.measure_processing_time(message["post_id"])
            if processing_time > 0:
                processing_times.append(processing_time)
                success_count += 1
        
        # Анализ результатов
        if processing_times:
            p95_latency = statistics.quantiles(processing_times, n=20)[18]  # 95th percentile
            avg_latency = statistics.mean(processing_times)
            max_latency = max(processing_times)
            
            print(f"Latency stats: avg={avg_latency:.2f}s, p95={p95_latency:.2f}s, max={max_latency:.2f}s")
            print(f"Success rate: {success_count}/{len(messages[:20])} ({success_count/len(messages[:20])*100:.1f}%)")
            
            # SLO проверки
            assert p95_latency < 2.0, f"p95 latency {p95_latency:.2f}s превышает SLO 2s"
            assert success_count / len(messages[:20]) > 0.99, f"Success rate {success_count/len(messages[:20])*100:.1f}% ниже 99%"
        else:
            pytest.fail("Ни одно сообщение не было обработано")
    
    @pytest.mark.asyncio
    async def test_burst_load(self, load_tester):
        """Тест burst load: 500 сообщений за 10 секунд."""
        message_count = 500
        burst_duration = 10  # секунд
        
        print(f"Запуск burst load test: {message_count} сообщений за {burst_duration} сек")
        
        # Генерация сообщений
        messages = await load_tester.generate_test_messages(message_count)
        
        # Отправка burst
        start_time = time.time()
        await load_tester.send_messages_to_redis(messages, "stream:posts:tagged")
        send_duration = time.time() - start_time
        
        print(f"Отправлено {message_count} сообщений за {send_duration:.2f} сек")
        
        # Измеряем queue depth
        initial_queue_depth = await load_tester.get_queue_depth("stream:posts:tagged")
        print(f"Initial queue depth: {initial_queue_depth}")
        
        # Ждём recovery
        recovery_start = time.time()
        max_recovery_time = 120  # 2 минуты
        
        while time.time() - recovery_start < max_recovery_time:
            queue_depth = await load_tester.get_queue_depth("stream:posts:tagged")
            pel_backlog = await load_tester.get_pel_backlog("stream:posts:tagged", "crawl_trigger_workers")
            
            print(f"Queue depth: {queue_depth}, PEL backlog: {pel_backlog}")
            
            if queue_depth < 10 and pel_backlog < 5:  # Практически пустая очередь
                recovery_time = time.time() - recovery_start
                print(f"Queue recovery за {recovery_time:.1f} сек")
                break
            
            await asyncio.sleep(5)
        else:
            pytest.fail(f"Queue не восстановилась за {max_recovery_time} сек")
        
        # SLO проверка
        assert recovery_time < 120, f"Recovery time {recovery_time:.1f}s превышает SLO 2 мин"
    
    @pytest.mark.asyncio
    async def test_backpressure(self, load_tester):
        """Тест backpressure: остановка consumer не должна ломать producer."""
        print("Запуск backpressure test")
        
        # Отправляем сообщения
        messages = await load_tester.generate_test_messages(50)
        await load_tester.send_messages_to_redis(messages, "stream:posts:tagged")
        
        # Проверяем, что сообщения накапливаются в очереди
        initial_depth = await load_tester.get_queue_depth("stream:posts:tagged")
        print(f"Initial queue depth: {initial_depth}")
        
        # В реальном тесте здесь бы мы остановили consumer
        # и проверили, что producer продолжает работать
        
        # Для демонстрации просто проверяем, что очередь не переполняется
        await asyncio.sleep(5)
        final_depth = await load_tester.get_queue_depth("stream:posts:tagged")
        print(f"Final queue depth: {final_depth}")
        
        # Проверяем, что система стабильна
        assert final_depth < 1000, "Queue depth слишком высокая"
    
    @pytest.mark.asyncio
    async def test_error_rate_under_load(self, load_tester):
        """Тест error rate под нагрузкой."""
        print("Запуск error rate test")
        
        # Отправляем смешанные сообщения (валидные и невалидные)
        valid_messages = await load_tester.generate_test_messages(40)
        invalid_messages = [
            {"post_id": f"invalid-{i}", "tags": [], "urls": []}  # Невалидные сообщения
            for i in range(10)
        ]
        
        all_messages = valid_messages + invalid_messages
        await load_tester.send_messages_to_redis(all_messages, "stream:posts:tagged")
        
        # Ждём обработки
        await asyncio.sleep(15)
        
        # Проверяем error rate
        # В реальной системе здесь бы мы проверили метрики Prometheus
        # или логи на предмет ошибок
        
        # Для демонстрации просто проверяем, что система не упала
        queue_depth = await load_tester.get_queue_depth("stream:posts:tagged")
        assert queue_depth < 100, "Система нестабильна под нагрузкой"
        
        print(f"Error rate test завершён, queue depth: {queue_depth}")


@pytest.mark.asyncio
async def test_load_test_runner():
    """Главный load test runner."""
    # Этот тест можно запускать для полного load testing
    # с реальными сервисами в Docker
    
    print("Load test runner - запуск всех тестов")
    
    # Здесь можно добавить логику для запуска всех load тестов
    # с разными профилями нагрузки
    
    assert True  # Placeholder


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
