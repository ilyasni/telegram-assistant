#!/usr/bin/env python3
"""Минимальный worker для диагностики."""

print("Minimal worker starting...", flush=True)

# Простой тест без зависимостей
print("Minimal worker: Test 1", flush=True)

# Тест с Redis
print("Minimal worker: Testing Redis...", flush=True)
import redis
r = redis.from_url('redis://redis:6379')
print("Minimal worker: Redis ping:", r.ping(), flush=True)

# Тест с Qdrant
print("Minimal worker: Testing Qdrant...", flush=True)
from qdrant_client import QdrantClient
client = QdrantClient(url='http://qdrant:6333')
print("Minimal worker: Qdrant connected", flush=True)

# Тест с DB
print("Minimal worker: Testing DB...", flush=True)
import psycopg2
conn = psycopg2.connect('postgresql://postgres:postgres@supabase-db:5432/postgres')
print("Minimal worker: DB connected", flush=True)
conn.close()

print("Minimal worker: All tests passed", flush=True)

# Простой цикл
print("Minimal worker: Starting main loop...", flush=True)
import time
for i in range(10):
    print(f"Minimal worker: Loop {i}", flush=True)
    time.sleep(1)

print("Minimal worker: Finished", flush=True)
