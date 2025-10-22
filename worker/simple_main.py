#!/usr/bin/env python3
"""Минимальный worker для диагностики."""

import asyncio
import sys

async def main():
    """Минимальная точка входа."""
    print("Simple worker starting...", flush=True)
    
    # Простой тест без зависимостей
    print("Simple worker: Test 1", flush=True)
    await asyncio.sleep(0.1)
    print("Simple worker: Test 2", flush=True)
    
    # Тест с Redis
    print("Simple worker: Testing Redis...", flush=True)
    import redis
    r = redis.from_url('redis://redis:6379')
    print("Simple worker: Redis ping:", r.ping(), flush=True)
    
    # Тест с Qdrant
    print("Simple worker: Testing Qdrant...", flush=True)
    from qdrant_client import QdrantClient
    client = QdrantClient(url='http://qdrant:6333')
    print("Simple worker: Qdrant connected", flush=True)
    
    # Тест с DB
    print("Simple worker: Testing DB...", flush=True)
    import psycopg2
    conn = psycopg2.connect('postgresql://postgres:postgres@supabase-db:5432/postgres')
    print("Simple worker: DB connected", flush=True)
    conn.close()
    
    print("Simple worker: All tests passed", flush=True)
    
    # Простой цикл
    print("Simple worker: Starting main loop...", flush=True)
    for i in range(10):
        print(f"Simple worker: Loop {i}", flush=True)
        await asyncio.sleep(1)
    
    print("Simple worker: Finished", flush=True)

if __name__ == "__main__":
    print("Simple worker: Starting...", flush=True)
    asyncio.run(main())
