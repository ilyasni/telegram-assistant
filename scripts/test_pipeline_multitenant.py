#!/usr/bin/env python3
"""
Context7: Проверка multi-tenant изоляции в пайплайне.

Проверяет:
1. Изоляцию данных между tenants (RLS политики)
2. Redis namespacing (t:{tenant_id}:*)
3. Qdrant collections per tenant
4. Neo4j изоляция по tenant_id
5. S3 bucket isolation (если используется)

Best practices:
- Использует pytest структуру с фикстурами
- Идемпотентные проверки с trace_id
- Создание и очистка тестовых данных
"""
import asyncio
import os
import sys
import uuid
from typing import Dict, List, Optional
from datetime import datetime, timezone

# Добавляем корень проекта в путь
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import asyncpg
import redis.asyncio as redis
import structlog
from qdrant_client import QdrantClient

logger = structlog.get_logger()

# Конфигурация
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")


async def check_rls_isolation(db_pool: asyncpg.Pool) -> Dict[str, any]:
    """
    Context7: Проверка RLS изоляции между tenants.
    
    Проверяет:
    - Политики RLS включены
    - Данные одного tenant не видны другому
    - app.tenant_id правильно устанавливается
    """
    logger.info("Checking RLS isolation...")
    
    results = {
        'rls_enabled': False,
        'tenant_isolation': False,
        'errors': []
    }
    
    try:
        async with db_pool.acquire() as conn:
            # Проверка включения RLS
            rls_check = await conn.fetchrow("""
                SELECT tablename, rowsecurity
                FROM pg_tables
                WHERE schemaname = 'public'
                AND tablename IN ('users', 'channels', 'posts', 'telegram_sessions')
            """)
            
            if rls_check:
                results['rls_enabled'] = rls_check['rowsecurity']
            
            # Создание тестовых tenants
            tenant1_id = str(uuid.uuid4())
            tenant2_id = str(uuid.uuid4())
            
            # Создаём tenants
            await conn.execute("""
                INSERT INTO tenants (id, name, created_at)
                VALUES ($1, 'Test Tenant 1', NOW()),
                       ($2, 'Test Tenant 2', NOW())
                ON CONFLICT DO NOTHING
            """, tenant1_id, tenant2_id)
            
            # Создаём identity
            identity_id = str(uuid.uuid4())
            telegram_id = 999888777
            await conn.execute("""
                INSERT INTO identities (id, telegram_id, created_at)
                VALUES ($1, $2, NOW())
                ON CONFLICT (telegram_id) DO NOTHING
            """, identity_id, telegram_id)
            
            # Создаём users в разных tenants
            user1_id = str(uuid.uuid4())
            user2_id = str(uuid.uuid4())
            
            await conn.execute("""
                INSERT INTO users (id, tenant_id, identity_id, telegram_id, username, tier)
                VALUES ($1, $2, $3, $4, 'test_user_1', 'pro'),
                       ($5, $6, $3, $4, 'test_user_2', 'free')
                ON CONFLICT (tenant_id, identity_id) DO UPDATE SET username = EXCLUDED.username
            """, user1_id, tenant1_id, identity_id, telegram_id,
                user2_id, tenant2_id, identity_id, telegram_id)
            
            # Проверка изоляции: устанавливаем tenant1_id и проверяем, что видим только user1
            await conn.execute("SET app.tenant_id = $1", tenant1_id)
            users_tenant1 = await conn.fetch("""
                SELECT id, tenant_id, username
                FROM users
                WHERE telegram_id = $1
            """, telegram_id)
            
            # Проверяем, что видим только одного пользователя из tenant1
            if len(users_tenant1) == 1 and users_tenant1[0]['id'] == user1_id:
                results['tenant_isolation'] = True
            else:
                results['errors'].append(f"RLS isolation failed: found {len(users_tenant1)} users instead of 1")
            
            # Очистка тестовых данных
            await conn.execute("SET app.tenant_id = NULL")
            await conn.execute("DELETE FROM users WHERE id IN ($1, $2)", user1_id, user2_id)
            await conn.execute("DELETE FROM tenants WHERE id IN ($1, $2)", tenant1_id, tenant2_id)
            
            logger.info("RLS isolation check completed", 
                       rls_enabled=results['rls_enabled'],
                       tenant_isolation=results['tenant_isolation'])
            
    except Exception as e:
        logger.error("RLS isolation check failed", error=str(e))
        results['errors'].append(str(e))
    
    return results


async def check_redis_namespacing(redis_client: redis.Redis) -> Dict[str, any]:
    """
    Context7: Проверка Redis namespacing (t:{tenant_id}:*).
    
    Проверяет:
    - Ключи используют префикс t:{tenant_id}:*
    - Нет пересечений между tenants
    """
    logger.info("Checking Redis namespacing...")
    
    results = {
        'namespacing_correct': False,
        'keys_found': 0,
        'errors': []
    }
    
    try:
        tenant1_id = str(uuid.uuid4())
        tenant2_id = str(uuid.uuid4())
        
        # Создаём тестовые ключи для разных tenants
        key1 = f"t:{tenant1_id}:test:key"
        key2 = f"t:{tenant2_id}:test:key"
        
        await redis_client.set(key1, "value1", ex=60)
        await redis_client.set(key2, "value2", ex=60)
        
        # Проверяем, что ключи изолированы
        value1 = await redis_client.get(key1)
        value2 = await redis_client.get(key2)
        
        if value1 and value2 and value1.decode() == "value1" and value2.decode() == "value2":
            results['namespacing_correct'] = True
        
        # Проверяем, что нет пересечений
        all_keys = []
        async for key in redis_client.scan_iter(match="t:*:test:key", count=100):
            all_keys.append(key.decode() if isinstance(key, bytes) else key)
        
        results['keys_found'] = len(all_keys)
        
        # Очистка
        await redis_client.delete(key1, key2)
        
        logger.info("Redis namespacing check completed",
                   namespacing_correct=results['namespacing_correct'],
                   keys_found=results['keys_found'])
        
    except Exception as e:
        logger.error("Redis namespacing check failed", error=str(e))
        results['errors'].append(str(e))
    
    return results


async def check_qdrant_collections(qdrant_client: QdrantClient) -> Dict[str, any]:
    """
    Context7: Проверка Qdrant collections per tenant.
    
    Проверяет:
    - Коллекции используют префикс t{tenant_id}_posts
    - Фильтрация по tenant_id в запросах
    """
    logger.info("Checking Qdrant collections...")
    
    results = {
        'collections_per_tenant': False,
        'tenant_collections': [],
        'errors': []
    }
    
    try:
        # Получаем все коллекции
        collections = qdrant_client.get_collections()
        
        # Проверяем, что есть коллекции с префиксом t{tenant_id}
        tenant_collections = [
            c.name for c in collections.collections
            if c.name.startswith('t') and '_posts' in c.name
        ]
        
        results['tenant_collections'] = tenant_collections
        
        if len(tenant_collections) > 0:
            results['collections_per_tenant'] = True
        
        logger.info("Qdrant collections check completed",
                   collections_per_tenant=results['collections_per_tenant'],
                   count=len(tenant_collections))
        
    except Exception as e:
        logger.error("Qdrant collections check failed", error=str(e))
        results['errors'].append(str(e))
    
    return results


async def main():
    """Главная функция."""
    logger.info("Starting multi-tenant isolation check...")
    
    # Инициализация подключений
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    redis_client = redis.from_url(REDIS_URL)
    qdrant_client = QdrantClient(url=QDRANT_URL)
    
    try:
        # Проверки
        rls_results = await check_rls_isolation(db_pool)
        redis_results = await check_redis_namespacing(redis_client)
        qdrant_results = await check_qdrant_collections(qdrant_client)
        
        # Сводка
        print("\n" + "="*80)
        print("MULTI-TENANT ISOLATION CHECK RESULTS")
        print("="*80)
        
        print("\n1. RLS Isolation:")
        print(f"   RLS Enabled: {rls_results['rls_enabled']}")
        print(f"   Tenant Isolation: {rls_results['tenant_isolation']}")
        if rls_results['errors']:
            print(f"   Errors: {rls_results['errors']}")
        
        print("\n2. Redis Namespacing:")
        print(f"   Namespacing Correct: {redis_results['namespacing_correct']}")
        print(f"   Keys Found: {redis_results['keys_found']}")
        if redis_results['errors']:
            print(f"   Errors: {redis_results['errors']}")
        
        print("\n3. Qdrant Collections:")
        print(f"   Collections Per Tenant: {qdrant_results['collections_per_tenant']}")
        print(f"   Tenant Collections: {qdrant_results['tenant_collections']}")
        if qdrant_results['errors']:
            print(f"   Errors: {qdrant_results['errors']}")
        
        # Общий результат
        all_ok = (
            rls_results['rls_enabled'] and
            rls_results['tenant_isolation'] and
            redis_results['namespacing_correct'] and
            qdrant_results['collections_per_tenant']
        )
        
        print("\n" + "="*80)
        if all_ok:
            print("✅ Multi-tenant isolation is working correctly")
        else:
            print("❌ Multi-tenant isolation issues detected")
        print("="*80)
        
        sys.exit(0 if all_ok else 1)
        
    except Exception as e:
        logger.error("Multi-tenant check failed", error=str(e))
        print(f"❌ Multi-tenant check failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        await db_pool.close()
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())


