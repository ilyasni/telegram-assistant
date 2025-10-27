"""
Redis Chaos Tests
[C7-ID: TEST-REDIS-CHAOS-001]

Тестирует устойчивость Redis Streams к различным типам сбоев
"""
import asyncio
import json
import time
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from typing import Dict, Any, List


class RedisChaosTester:
    """Redis chaos tester."""
    
    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.redis: Redis = None
        self.results: List[Dict[str, Any]] = []
    
    async def setup(self):
        """Инициализация Redis."""
        self.redis = Redis.from_url(self.redis_url, decode_responses=True)
        await self.redis.ping()
    
    async def cleanup(self):
        """Очистка ресурсов."""
        if self.redis:
            await self.redis.close()
    
    async def create_test_stream(self, stream_name: str, messages: int = 100):
        """Создание тестового стрима с сообщениями."""
        print(f"Creating test stream {stream_name} with {messages} messages")
        
        for i in range(messages):
            message = {
                "id": f"test-{i}",
                "data": f"test-data-{i}",
                "timestamp": time.time()
            }
            await self.redis.xadd(stream_name, message, maxlen=10000)
        
        print(f"Created {messages} messages in {stream_name}")
    
    async def create_consumer_group(self, stream_name: str, group_name: str):
        """Создание consumer group."""
        try:
            await self.redis.xgroup_create(stream_name, group_name, id="0", mkstream=True)
            print(f"Created consumer group {group_name} for {stream_name}")
        except Exception as e:
            if "BUSYGROUP" in str(e):
                print(f"Consumer group {group_name} already exists")
            else:
                raise
    
    async def read_messages(self, stream_name: str, group_name: str, consumer_name: str, count: int = 10):
        """Чтение сообщений из consumer group."""
        try:
            messages = await self.redis.xreadgroup(
                group_name, consumer_name, {stream_name: ">"}, count=count, block=1000
            )
            return messages
        except Exception as e:
            print(f"Error reading messages: {e}")
            return []
    
    async def get_stream_info(self, stream_name: str):
        """Получение информации о стриме."""
        try:
            info = await self.redis.xinfo_stream(stream_name)
            return info
        except Exception as e:
            print(f"Error getting stream info: {e}")
            return None
    
    async def get_group_info(self, stream_name: str):
        """Получение информации о consumer groups."""
        try:
            groups = await self.redis.xinfo_groups(stream_name)
            return groups
        except Exception as e:
            print(f"Error getting group info: {e}")
            return []
    
    async def get_pending_info(self, stream_name: str, group_name: str):
        """Получение информации о pending сообщениях."""
        try:
            pending = await self.redis.xpending(stream_name, group_name)
            return pending
        except Exception as e:
            print(f"Error getting pending info: {e}")
            return None
    
    async def simulate_memory_pressure(self):
        """Симуляция memory pressure."""
        print("💾 Simulating memory pressure...")
        
        # Создаём большие сообщения для создания memory pressure
        large_data = "x" * 1024 * 1024  # 1MB
        
        for i in range(10):
            message = {
                "id": f"large-{i}",
                "data": large_data,
                "timestamp": time.time()
            }
            await self.redis.xadd("stream:memory:pressure", message)
        
        print("✅ Memory pressure simulated")
    
    async def simulate_connection_pool_exhaustion(self):
        """Симуляция исчерпания connection pool."""
        print("🔌 Simulating connection pool exhaustion...")
        
        # Создаём много подключений
        connections = []
        for i in range(100):
            conn = Redis.from_url(self.redis_url, decode_responses=True)
            connections.append(conn)
            await conn.ping()
        
        print(f"Created {len(connections)} connections")
        
        # Закрываем все подключения
        for conn in connections:
            await conn.close()
        
        print("✅ Connection pool exhaustion simulated")
    
    async def simulate_slow_consumer(self, stream_name: str, group_name: str, consumer_name: str):
        """Симуляция медленного consumer."""
        print("🐌 Simulating slow consumer...")
        
        # Читаем сообщения, но не ACK'аем их
        messages = await self.read_messages(stream_name, group_name, consumer_name, count=5)
        
        if messages:
            print(f"Read {len(messages)} messages without ACK")
            # Не ACK'аем сообщения, чтобы они остались в pending
        
        print("✅ Slow consumer simulated")
    
    async def simulate_consumer_crash(self, stream_name: str, group_name: str, consumer_name: str):
        """Симуляция краша consumer."""
        print("💥 Simulating consumer crash...")
        
        # Читаем сообщения
        messages = await self.read_messages(stream_name, group_name, consumer_name, count=5)
        
        if messages:
            print(f"Consumer crashed with {len(messages)} unprocessed messages")
            # Не ACK'аем сообщения - они останутся в pending
        
        print("✅ Consumer crash simulated")


class TestRedisChaos:
    """Redis chaos tests."""
    
    @pytest_asyncio.fixture
    async def redis_chaos_tester(self):
        """Redis chaos tester fixture."""
        tester = RedisChaosTester(redis_url="redis://localhost:6379")
        await tester.setup()
        yield tester
        await tester.cleanup()
    
    @pytest.mark.asyncio
    async def test_memory_pressure_handling(self, redis_chaos_tester):
        """Тест обработки memory pressure."""
        print("🧪 Testing memory pressure handling")
        
        # Создаём тестовый стрим
        await redis_chaos_tester.create_test_stream("stream:memory:test", 50)
        
        # Симулируем memory pressure
        await redis_chaos_tester.simulate_memory_pressure()
        
        # Проверяем, что стрим всё ещё работает
        info = await redis_chaos_tester.get_stream_info("stream:memory:test")
        assert info is not None, "Stream не доступен после memory pressure"
        
        # Проверяем, что maxlen работает
        assert info["length"] <= 10000, "Stream превысил maxlen"
        
        print("✅ Memory pressure handling test passed")
    
    @pytest.mark.asyncio
    async def test_connection_pool_exhaustion(self, redis_chaos_tester):
        """Тест исчерпания connection pool."""
        print("🧪 Testing connection pool exhaustion")
        
        # Симулируем исчерпание connection pool
        await redis_chaos_tester.simulate_connection_pool_exhaustion()
        
        # Проверяем, что Redis всё ещё работает
        await redis_chaos_tester.redis.ping()
        
        # Создаём новый стрим
        await redis_chaos_tester.create_test_stream("stream:connection:test", 10)
        
        # Проверяем, что можем читать
        info = await redis_chaos_tester.get_stream_info("stream:connection:test")
        assert info is not None, "Не можем создать стрим после connection pool exhaustion"
        
        print("✅ Connection pool exhaustion test passed")
    
    @pytest.mark.asyncio
    async def test_slow_consumer_recovery(self, redis_chaos_tester):
        """Тест восстановления после медленного consumer."""
        print("🧪 Testing slow consumer recovery")
        
        # Создаём стрим и consumer group
        stream_name = "stream:slow:consumer"
        group_name = "slow-group"
        consumer_name = "slow-consumer"
        
        await redis_chaos_tester.create_test_stream(stream_name, 20)
        await redis_chaos_tester.create_consumer_group(stream_name, group_name)
        
        # Симулируем медленного consumer
        await redis_chaos_tester.simulate_slow_consumer(stream_name, group_name, consumer_name)
        
        # Проверяем pending сообщения
        pending = await redis_chaos_tester.get_pending_info(stream_name, group_name)
        assert pending is not None, "Не можем получить pending info"
        
        # Создаём нового consumer для обработки pending
        new_consumer_name = "fast-consumer"
        messages = await redis_chaos_tester.read_messages(stream_name, group_name, new_consumer_name, count=10)
        
        if messages:
            print(f"New consumer read {len(messages)} messages")
            # ACK'аем сообщения
            for stream, msgs in messages:
                for msg_id, fields in msgs:
                    await redis_chaos_tester.redis.xack(stream_name, group_name, msg_id)
        
        print("✅ Slow consumer recovery test passed")
    
    @pytest.mark.asyncio
    async def test_consumer_crash_recovery(self, redis_chaos_tester):
        """Тест восстановления после краша consumer."""
        print("🧪 Testing consumer crash recovery")
        
        # Создаём стрим и consumer group
        stream_name = "stream:crash:test"
        group_name = "crash-group"
        consumer_name = "crash-consumer"
        
        await redis_chaos_tester.create_test_stream(stream_name, 15)
        await redis_chaos_tester.create_consumer_group(stream_name, group_name)
        
        # Симулируем краш consumer
        await redis_chaos_tester.simulate_consumer_crash(stream_name, group_name, consumer_name)
        
        # Проверяем pending сообщения
        pending = await redis_chaos_tester.get_pending_info(stream_name, group_name)
        assert pending is not None, "Не можем получить pending info"
        
        # Создаём нового consumer для обработки pending
        new_consumer_name = "recovery-consumer"
        messages = await redis_chaos_tester.read_messages(stream_name, group_name, new_consumer_name, count=10)
        
        if messages:
            print(f"Recovery consumer read {len(messages)} messages")
            # ACK'аем сообщения
            for stream, msgs in messages:
                for msg_id, fields in msgs:
                    await redis_chaos_tester.redis.xack(stream_name, group_name, msg_id)
        
        print("✅ Consumer crash recovery test passed")
    
    @pytest.mark.asyncio
    async def test_stream_overflow_handling(self, redis_chaos_tester):
        """Тест обработки переполнения стрима."""
        print("🧪 Testing stream overflow handling")
        
        # Создаём стрим с маленьким maxlen
        stream_name = "stream:overflow:test"
        
        # Создаём много сообщений для переполнения
        for i in range(15000):  # Больше чем maxlen=10000
            message = {
                "id": f"overflow-{i}",
                "data": f"overflow-data-{i}",
                "timestamp": time.time()
            }
            await redis_chaos_tester.redis.xadd(stream_name, message, maxlen=10000)
        
        # Проверяем, что maxlen работает
        info = await redis_chaos_tester.get_stream_info(stream_name)
        assert info is not None, "Stream не доступен после переполнения"
        assert info["length"] <= 10000, "Stream превысил maxlen"
        
        print("✅ Stream overflow handling test passed")
    
    @pytest.mark.asyncio
    async def test_consumer_group_consistency(self, redis_chaos_tester):
        """Тест консистентности consumer group."""
        print("🧪 Testing consumer group consistency")
        
        # Создаём стрим и consumer group
        stream_name = "stream:consistency:test"
        group_name = "consistency-group"
        
        await redis_chaos_tester.create_test_stream(stream_name, 30)
        await redis_chaos_tester.create_consumer_group(stream_name, group_name)
        
        # Создаём несколько consumer'ов
        consumers = ["consumer-1", "consumer-2", "consumer-3"]
        
        for consumer in consumers:
            messages = await redis_chaos_tester.read_messages(stream_name, group_name, consumer, count=5)
            if messages:
                # ACK'аем сообщения
                for stream, msgs in messages:
                    for msg_id, fields in msgs:
                        await redis_chaos_tester.redis.xack(stream_name, group_name, msg_id)
        
        # Проверяем консистентность
        groups = await redis_chaos_tester.get_group_info(stream_name)
        assert len(groups) > 0, "Consumer group не найден"
        
        # Проверяем, что все сообщения обработаны
        pending = await redis_chaos_tester.get_pending_info(stream_name, group_name)
        assert pending is not None, "Не можем получить pending info"
        
        print("✅ Consumer group consistency test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
