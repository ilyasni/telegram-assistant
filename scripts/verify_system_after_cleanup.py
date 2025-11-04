#!/usr/bin/env python3
"""
Проверка системы после очистки данных.

Context7 best practice: Комплексная проверка всех компонентов системы
после очистки тестовых данных для обеспечения работоспособности.
"""

import asyncio
import os
import sys
from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text
import structlog
import redis.asyncio as redis
from qdrant_client import QdrantClient as QdrantSDK
from neo4j import AsyncGraphDatabase
try:
    import httpx
except ImportError:
    httpx = None

sys.path.append('/opt/telegram-assistant')

logger = structlog.get_logger()

class SystemVerifier:
    """Проверка работоспособности системы после очистки."""
    
    def __init__(self):
        self.results = []
        
    def add_result(self, component: str, status: str, message: str, details: Optional[Dict] = None):
        """Добавить результат проверки."""
        self.results.append({
            "component": component,
            "status": status,
            "message": message,
            "details": details or {}
        })
        symbol = "✅" if status == "OK" else "❌" if status == "FAIL" else "⚠️"
        print(f"{symbol} {component}: {message}")
        if details:
            for key, value in details.items():
                print(f"    {key}: {value}")
    
    async def check_postgresql(self):
        """Проверка PostgreSQL."""
        try:
            db_url_env = os.getenv(
                "DATABASE_URL",
                "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
            )
            if db_url_env.startswith("postgresql://") and "+asyncpg" not in db_url_env:
                db_url = db_url_env.replace("postgresql://", "postgresql+asyncpg://", 1)
            else:
                db_url = db_url_env
            
            engine = create_async_engine(db_url)
            
            try:
                async with AsyncSession(engine) as session:
                    # Проверка сохранённых данных
                    result = await session.execute(text("SELECT COUNT(*) FROM users"))
                    users_count = result.scalar()
                    
                    result = await session.execute(text("SELECT COUNT(*) FROM channels WHERE is_active = true"))
                    channels_count = result.scalar()
                    
                    result = await session.execute(text("SELECT COUNT(*) FROM telegram_sessions WHERE status = 'authorized'"))
                    sessions_count = result.scalar()
                    
                    result = await session.execute(text("SELECT COUNT(*) FROM user_channel WHERE is_active = true"))
                    subscriptions_count = result.scalar()
                    
                    # Проверка очищенных данных
                    result = await session.execute(text("SELECT COUNT(*) FROM posts"))
                    posts_count = result.scalar()
                    
                    result = await session.execute(text("SELECT COUNT(*) FROM media_groups"))
                    albums_count = result.scalar()
                    
                    result = await session.execute(text("SELECT COUNT(*) FROM post_enrichment"))
                    enrichment_count = result.scalar()
                    
                    self.add_result(
                        "PostgreSQL",
                        "OK" if posts_count == 0 and albums_count == 0 else "WARN",
                        "База данных в порядке",
                        {
                            "Пользователи": users_count,
                            "Активные каналы": channels_count,
                            "Активные сессии": sessions_count,
                            "Подписки": subscriptions_count,
                            "Посты (очищено)": posts_count,
                            "Альбомы (очищено)": albums_count,
                            "Enrichment (очищено)": enrichment_count,
                        }
                    )
            finally:
                await engine.dispose()
                
        except Exception as e:
            self.add_result("PostgreSQL", "FAIL", f"Ошибка подключения: {str(e)}")
    
    async def check_redis(self):
        """Проверка Redis."""
        try:
            redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
            redis_client = await redis.from_url(redis_url, decode_responses=True)
            
            try:
                await redis_client.ping()
                
                # Проверка стримов
                streams_to_check = [
                    'stream:posts:parsed',
                    'stream:posts:tagged',
                    'stream:posts:enriched',
                    'stream:posts:indexed',
                ]
                
                streams_status = {}
                total_messages = 0
                
                for stream in streams_to_check:
                    length = await redis_client.xlen(stream)
                    streams_status[stream] = length
                    total_messages += length
                
                self.add_result(
                    "Redis",
                    "OK" if total_messages == 0 else "WARN",
                    "Redis работает, стримы очищены" if total_messages == 0 else f"В стримах осталось {total_messages} сообщений",
                    streams_status
                )
            finally:
                await redis_client.aclose()
                
        except Exception as e:
            self.add_result("Redis", "FAIL", f"Ошибка подключения: {str(e)}")
    
    async def check_qdrant(self):
        """Проверка Qdrant."""
        try:
            qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
            client = QdrantSDK(url=qdrant_url)
            
            collections = client.get_collections()
            collections_count = len(collections.collections)
            
            self.add_result(
                "Qdrant",
                "OK" if collections_count == 0 else "WARN",
                f"Найдено коллекций: {collections_count}" if collections_count > 0 else "Все коллекции очищены",
                {"Коллекций": collections_count}
            )
            
        except Exception as e:
            self.add_result("Qdrant", "FAIL", f"Ошибка подключения: {str(e)}")
    
    async def check_neo4j(self):
        """Проверка Neo4j."""
        try:
            neo4j_uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL", "neo4j://neo4j:7687")
            neo4j_user = os.getenv("NEO4J_USER", "neo4j")
            neo4j_password = os.getenv("NEO4J_PASSWORD", "neo4j123")
            
            driver = AsyncGraphDatabase.driver(
                neo4j_uri,
                auth=(neo4j_user, neo4j_password)
            )
            
            try:
                await driver.verify_connectivity()
                
                async with driver.session() as session:
                    result = await session.run("MATCH (n) RETURN count(n) as node_count")
                    record = await result.single()
                    node_count = record["node_count"] if record else 0
                    
                    result = await session.run("MATCH ()-[r]->() RETURN count(r) as rel_count")
                    record = await result.single()
                    rel_count = record["rel_count"] if record else 0
                
                self.add_result(
                    "Neo4j",
                    "OK" if node_count == 0 and rel_count == 0 else "WARN",
                    "Граф очищен" if node_count == 0 else f"Осталось узлов: {node_count}",
                    {
                        "Узлы": node_count,
                        "Отношения": rel_count
                    }
                )
            finally:
                await driver.close()
                
        except Exception as e:
            self.add_result("Neo4j", "FAIL", f"Ошибка подключения: {str(e)}")
    
    async def check_health_endpoints(self):
        """Проверка health endpoints."""
        if httpx is None:
            self.add_result("Health Endpoints", "SKIP", "httpx не установлен")
            return
        
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                endpoints = {
                    "API Health": "http://api:8000/health",
                    "Worker Health": "http://worker:8001/health",
                    "Telethon Health": "http://telethon-ingest:8011/health",
                }
                
                for name, url in endpoints.items():
                    try:
                        response = await client.get(url, timeout=5.0)
                        if response.status_code == 200:
                            self.add_result(name, "OK", f"HTTP {response.status_code}")
                        else:
                            self.add_result(name, "WARN", f"HTTP {response.status_code}")
                    except Exception as e:
                        self.add_result(name, "FAIL", f"Недоступен: {str(e)}")
                        
        except Exception as e:
            self.add_result("Health Endpoints", "FAIL", f"Ошибка проверки: {str(e)}")
    
    async def check_parsing_readiness(self):
        """Проверка готовности к парсингу."""
        try:
            db_url_env = os.getenv(
                "DATABASE_URL",
                "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
            )
            if db_url_env.startswith("postgresql://") and "+asyncpg" not in db_url_env:
                db_url = db_url_env.replace("postgresql://", "postgresql+asyncpg://", 1)
            else:
                db_url = db_url_env
            
            engine = create_async_engine(db_url)
            
            try:
                async with AsyncSession(engine) as session:
                    # Проверка активных каналов
                    result = await session.execute(
                        text("""
                            SELECT COUNT(*) as count
                            FROM channels 
                            WHERE is_active = true
                        """)
                    )
                    active_channels = result.scalar()
                    
                    # Проверка пользователей с сессиями
                    result = await session.execute(
                        text("""
                            SELECT COUNT(DISTINCT ts.user_id) as count
                            FROM telegram_sessions ts
                            WHERE ts.status = 'authorized' AND ts.user_id IS NOT NULL
                        """)
                    )
                    users_with_sessions = result.scalar()
                    
                    # Проверка подписок
                    result = await session.execute(
                        text("""
                            SELECT COUNT(*) as count
                            FROM user_channel
                            WHERE is_active = true
                        """)
                    )
                    active_subscriptions = result.scalar()
                    
                    ready = active_channels > 0 and users_with_sessions > 0
                    
                    self.add_result(
                        "Готовность к парсингу",
                        "OK" if ready else "WARN",
                        "Система готова к парсингу" if ready else "Требуется настройка",
                        {
                            "Активных каналов": active_channels,
                            "Пользователей с сессиями": users_with_sessions,
                            "Активных подписок": active_subscriptions,
                        }
                    )
            finally:
                await engine.dispose()
                
        except Exception as e:
            self.add_result("Готовность к парсингу", "FAIL", f"Ошибка: {str(e)}")
    
    def print_summary(self):
        """Вывод итоговой статистики."""
        print("\n" + "=" * 80)
        print("ИТОГОВАЯ СТАТИСТИКА ПРОВЕРКИ")
        print("=" * 80)
        
        statuses = {"OK": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
        for result in self.results:
            statuses[result["status"]] = statuses.get(result["status"], 0) + 1
        
        print(f"\n✅ OK: {statuses['OK']}")
        print(f"⚠️  WARN: {statuses['WARN']}")
        print(f"❌ FAIL: {statuses['FAIL']}")
        print(f"⏭️  SKIP: {statuses['SKIP']}")
        
        print("\n" + "=" * 80)
        
        if statuses['FAIL'] > 0:
            print("❌ Обнаружены критические проблемы!")
            return False
        elif statuses['WARN'] > 0:
            print("⚠️  Обнаружены предупреждения, но система работает")
            return True
        else:
            print("✅ Все проверки пройдены успешно!")
            return True

async def main():
    """Основная функция."""
    print("=" * 80)
    print("ПРОВЕРКА СИСТЕМЫ ПОСЛЕ ОЧИСТКИ ДАННЫХ")
    print("=" * 80)
    print()
    
    verifier = SystemVerifier()
    
    print("📊 Проверка компонентов...\n")
    
    await verifier.check_postgresql()
    await verifier.check_redis()
    await verifier.check_qdrant()
    await verifier.check_neo4j()
    await verifier.check_health_endpoints()
    await verifier.check_parsing_readiness()
    
    success = verifier.print_summary()
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    asyncio.run(main())

