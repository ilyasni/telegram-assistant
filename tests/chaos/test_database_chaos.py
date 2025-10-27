"""
Database Chaos Tests
[C7-ID: TEST-DB-CHAOS-001]

Тестирует устойчивость Supabase/PostgreSQL к различным типам сбоев
"""
import asyncio
import json
import time
import pytest
import pytest_asyncio
import asyncpg
from typing import Dict, Any, List, Optional


class DatabaseChaosTester:
    """Database chaos tester."""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.db_pool: Optional[asyncpg.Pool] = None
        self.results: List[Dict[str, Any]] = []
    
    async def setup(self):
        """Инициализация подключения к БД."""
        self.db_pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=10,
            command_timeout=30
        )
    
    async def cleanup(self):
        """Очистка ресурсов."""
        if self.db_pool:
            await self.db_pool.close()
    
    async def create_test_data(self, count: int = 100):
        """Создание тестовых данных."""
        print(f"Creating {count} test records")
        
        async with self.db_pool.acquire() as conn:
            for i in range(count):
                await conn.execute(
                    """
                    INSERT INTO posts (id, channel_id, message_id, text, created_at)
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    f"chaos-test-{i}",
                    "chaos-channel",
                    i,
                    f"Chaos test message {i}",
                    time.time()
                )
        
        print(f"Created {count} test records")
    
    async def create_enrichment_data(self, post_id: str, count: int = 10):
        """Создание тестовых данных обогащения."""
        print(f"Creating {count} enrichment records for {post_id}")
        
        async with self.db_pool.acquire() as conn:
            for i in range(count):
                await conn.execute(
                    """
                    INSERT INTO post_enrichment (post_id, kind, data, created_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (post_id, kind) DO UPDATE SET
                        data = EXCLUDED.data,
                        updated_at = EXCLUDED.updated_at
                    """,
                    post_id,
                    f"chaos-kind-{i}",
                    json.dumps({"test": f"chaos-data-{i}"}),
                    time.time()
                )
        
        print(f"Created {count} enrichment records")
    
    async def simulate_connection_pool_exhaustion(self):
        """Симуляция исчерпания connection pool."""
        print("🔌 Simulating connection pool exhaustion...")
        
        # Создаём много подключений
        connections = []
        for i in range(50):
            conn = await asyncpg.connect(self.database_url)
            connections.append(conn)
            await conn.fetchval("SELECT 1")
        
        print(f"Created {len(connections)} connections")
        
        # Закрываем все подключения
        for conn in connections:
            await conn.close()
        
        print("✅ Connection pool exhaustion simulated")
    
    async def simulate_long_running_query(self):
        """Симуляция долгого запроса."""
        print("⏳ Simulating long running query...")
        
        async with self.db_pool.acquire() as conn:
            # Выполняем долгий запрос
            await conn.execute("SELECT pg_sleep(5)")
        
        print("✅ Long running query completed")
    
    async def simulate_deadlock(self):
        """Симуляция deadlock."""
        print("🔒 Simulating deadlock...")
        
        async def transaction_1():
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("UPDATE posts SET text = 'tx1' WHERE id = 'chaos-test-1'")
                    await asyncio.sleep(1)
                    await conn.execute("UPDATE posts SET text = 'tx1' WHERE id = 'chaos-test-2'")
        
        async def transaction_2():
            async with self.db_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("UPDATE posts SET text = 'tx2' WHERE id = 'chaos-test-2'")
                    await asyncio.sleep(1)
                    await conn.execute("UPDATE posts SET text = 'tx2' WHERE id = 'chaos-test-1'")
        
        # Запускаем транзакции параллельно
        try:
            await asyncio.gather(transaction_1(), transaction_2())
        except Exception as e:
            print(f"Deadlock detected: {e}")
        
        print("✅ Deadlock simulation completed")
    
    async def simulate_lock_timeout(self):
        """Симуляция lock timeout."""
        print("⏰ Simulating lock timeout...")
        
        async with self.db_pool.acquire() as conn:
            # Устанавливаем короткий lock timeout
            await conn.execute("SET lock_timeout = '1s'")
            
            try:
                # Пытаемся заблокировать таблицу
                await conn.execute("LOCK TABLE posts IN EXCLUSIVE MODE")
            except Exception as e:
                print(f"Lock timeout: {e}")
        
        print("✅ Lock timeout simulation completed")
    
    async def simulate_memory_pressure(self):
        """Симуляция memory pressure."""
        print("💾 Simulating memory pressure...")
        
        # Создаём большие записи
        large_data = "x" * 1024 * 1024  # 1MB
        
        async with self.db_pool.acquire() as conn:
            for i in range(10):
                await conn.execute(
                    """
                    INSERT INTO post_enrichment (post_id, kind, data, created_at)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (post_id, kind) DO UPDATE SET
                        data = EXCLUDED.data,
                        updated_at = EXCLUDED.updated_at
                    """,
                    f"memory-pressure-{i}",
                    "large-data",
                    json.dumps({"large_data": large_data}),
                    time.time()
                )
        
        print("✅ Memory pressure simulated")
    
    async def check_data_consistency(self) -> bool:
        """Проверка консистентности данных."""
        try:
            async with self.db_pool.acquire() as conn:
                # Проверяем, что можем читать данные
                count = await conn.fetchval("SELECT COUNT(*) FROM posts WHERE channel_id = 'chaos-channel'")
                print(f"Found {count} chaos test records")
                
                # Проверяем, что можем читать enrichment данные
                enrichment_count = await conn.fetchval("SELECT COUNT(*) FROM post_enrichment WHERE post_id LIKE 'chaos-test-%'")
                print(f"Found {enrichment_count} enrichment records")
                
                return count > 0 and enrichment_count > 0
        except Exception as e:
            print(f"Data consistency check failed: {e}")
            return False
    
    async def get_database_stats(self) -> Dict[str, Any]:
        """Получение статистики БД."""
        try:
            async with self.db_pool.acquire() as conn:
                stats = {}
                
                # Количество подключений
                stats["connections"] = await conn.fetchval("SELECT COUNT(*) FROM pg_stat_activity")
                
                # Размер БД
                stats["db_size"] = await conn.fetchval("SELECT pg_size_pretty(pg_database_size(current_database()))")
                
                # Количество записей в таблицах
                stats["posts_count"] = await conn.fetchval("SELECT COUNT(*) FROM posts")
                stats["enrichment_count"] = await conn.fetchval("SELECT COUNT(*) FROM post_enrichment")
                
                return stats
        except Exception as e:
            print(f"Error getting database stats: {e}")
            return {}


class TestDatabaseChaos:
    """Database chaos tests."""
    
    @pytest_asyncio.fixture
    async def db_chaos_tester(self):
        """Database chaos tester fixture."""
        tester = DatabaseChaosTester(
            database_url="postgresql://postgres:password@localhost:5432/telegram_assistant"
        )
        await tester.setup()
        yield tester
        await tester.cleanup()
    
    @pytest.mark.asyncio
    async def test_connection_pool_exhaustion(self, db_chaos_tester):
        """Тест исчерпания connection pool."""
        print("🧪 Testing connection pool exhaustion")
        
        # Создаём тестовые данные
        await db_chaos_tester.create_test_data(50)
        
        # Симулируем исчерпание connection pool
        await db_chaos_tester.simulate_connection_pool_exhaustion()
        
        # Проверяем, что БД всё ещё работает
        consistent = await db_chaos_tester.check_data_consistency()
        assert consistent, "Данные не консистентны после исчерпания connection pool"
        
        # Проверяем статистику
        stats = await db_chaos_tester.get_database_stats()
        assert stats["posts_count"] > 0, "Не можем читать данные после connection pool exhaustion"
        
        print("✅ Connection pool exhaustion test passed")
    
    @pytest.mark.asyncio
    async def test_long_running_query_handling(self, db_chaos_tester):
        """Тест обработки долгих запросов."""
        print("🧪 Testing long running query handling")
        
        # Создаём тестовые данные
        await db_chaos_tester.create_test_data(100)
        
        # Симулируем долгий запрос
        start_time = time.time()
        await db_chaos_tester.simulate_long_running_query()
        end_time = time.time()
        
        # Проверяем, что запрос выполнился
        assert end_time - start_time >= 5, "Долгий запрос не выполнился"
        
        # Проверяем, что БД всё ещё работает
        consistent = await db_chaos_tester.check_data_consistency()
        assert consistent, "Данные не консистентны после долгого запроса"
        
        print("✅ Long running query handling test passed")
    
    @pytest.mark.asyncio
    async def test_deadlock_recovery(self, db_chaos_tester):
        """Тест восстановления после deadlock."""
        print("🧪 Testing deadlock recovery")
        
        # Создаём тестовые данные
        await db_chaos_tester.create_test_data(10)
        
        # Симулируем deadlock
        await db_chaos_tester.simulate_deadlock()
        
        # Проверяем, что БД всё ещё работает
        consistent = await db_chaos_tester.check_data_consistency()
        assert consistent, "Данные не консистентны после deadlock"
        
        # Проверяем, что можем создавать новые данные
        await db_chaos_tester.create_enrichment_data("chaos-test-1", 5)
        
        print("✅ Deadlock recovery test passed")
    
    @pytest.mark.asyncio
    async def test_lock_timeout_handling(self, db_chaos_tester):
        """Тест обработки lock timeout."""
        print("🧪 Testing lock timeout handling")
        
        # Создаём тестовые данные
        await db_chaos_tester.create_test_data(20)
        
        # Симулируем lock timeout
        await db_chaos_tester.simulate_lock_timeout()
        
        # Проверяем, что БД всё ещё работает
        consistent = await db_chaos_tester.check_data_consistency()
        assert consistent, "Данные не консистентны после lock timeout"
        
        print("✅ Lock timeout handling test passed")
    
    @pytest.mark.asyncio
    async def test_memory_pressure_handling(self, db_chaos_tester):
        """Тест обработки memory pressure."""
        print("🧪 Testing memory pressure handling")
        
        # Создаём тестовые данные
        await db_chaos_tester.create_test_data(30)
        
        # Симулируем memory pressure
        await db_chaos_tester.simulate_memory_pressure()
        
        # Проверяем, что БД всё ещё работает
        consistent = await db_chaos_tester.check_data_consistency()
        assert consistent, "Данные не консистентны после memory pressure"
        
        # Проверяем статистику
        stats = await db_chaos_tester.get_database_stats()
        assert stats["enrichment_count"] > 0, "Не можем читать enrichment данные после memory pressure"
        
        print("✅ Memory pressure handling test passed")
    
    @pytest.mark.asyncio
    async def test_concurrent_transactions(self, db_chaos_tester):
        """Тест конкурентных транзакций."""
        print("🧪 Testing concurrent transactions")
        
        # Создаём тестовые данные
        await db_chaos_tester.create_test_data(50)
        
        # Создаём несколько конкурентных транзакций
        async def concurrent_transaction(transaction_id: int):
            async with db_chaos_tester.db_pool.acquire() as conn:
                async with conn.transaction():
                    # Обновляем разные записи
                    for i in range(5):
                        post_id = f"chaos-test-{transaction_id * 5 + i}"
                        await conn.execute(
                            "UPDATE posts SET text = $1 WHERE id = $2",
                            f"concurrent-tx-{transaction_id}",
                            post_id
                        )
                    await asyncio.sleep(0.1)  # Небольшая задержка
        
        # Запускаем 5 конкурентных транзакций
        await asyncio.gather(*[concurrent_transaction(i) for i in range(5)])
        
        # Проверяем, что все транзакции выполнились
        consistent = await db_chaos_tester.check_data_consistency()
        assert consistent, "Данные не консистентны после конкурентных транзакций"
        
        print("✅ Concurrent transactions test passed")
    
    @pytest.mark.asyncio
    async def test_rollback_recovery(self, db_chaos_tester):
        """Тест восстановления после rollback."""
        print("🧪 Testing rollback recovery")
        
        # Создаём тестовые данные
        await db_chaos_tester.create_test_data(25)
        
        # Создаём транзакцию с rollback
        async with db_chaos_tester.db_pool.acquire() as conn:
            try:
                async with conn.transaction():
                    # Вставляем данные
                    await conn.execute(
                        "INSERT INTO posts (id, channel_id, message_id, text, created_at) VALUES ($1, $2, $3, $4, $5)",
                        "rollback-test",
                        "rollback-channel",
                        999,
                        "This should be rolled back",
                        time.time()
                    )
                    # Принудительно вызываем rollback
                    raise Exception("Forced rollback")
            except Exception as e:
                print(f"Rollback occurred: {e}")
        
        # Проверяем, что данные не были вставлены
        async with db_chaos_tester.db_pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM posts WHERE id = 'rollback-test'")
            assert count == 0, "Данные не были откачены"
        
        # Проверяем, что БД всё ещё работает
        consistent = await db_chaos_tester.check_data_consistency()
        assert consistent, "Данные не консистентны после rollback"
        
        print("✅ Rollback recovery test passed")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
