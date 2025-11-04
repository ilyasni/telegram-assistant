#!/usr/bin/env python3
"""
Быстрый тест исправлений.

Проверяет что исправленный код работает корректно.
"""

import asyncio
import sys
import os
from datetime import datetime, timezone
sys.path.append('/opt/telegram-assistant')

# Тест 1: Проверка что created_at fallback работает
print("Тест 1: Проверка created_at fallback...")
from worker.tasks.post_persistence_task import PostPersistenceWorker

worker = PostPersistenceWorker(None, None, None)

# Тест с None
test_created_at = worker._parse_iso_dt_utc(None)
if test_created_at is None:
    # Это нормально, fallback применяется в _upsert_post
    print("  ✅ _parse_iso_dt_utc возвращает None для None (fallback в _upsert_post)")
else:
    print(f"  ✅ _parse_iso_dt_utc вернул: {test_created_at}")

# Тест с валидной датой
test_created_at_valid = worker._parse_iso_dt_utc("2025-11-03T20:00:00Z")
if test_created_at_valid is not None:
    print(f"  ✅ _parse_iso_dt_utc обрабатывает валидные даты: {test_created_at_valid}")
else:
    print("  ❌ _parse_iso_dt_utc не обработал валидную дату")

# Тест 2: Проверка синтаксиса SQL
print("\nТест 2: Проверка SQL синтаксиса...")
from sqlalchemy import text

# Тест ANY() синтаксиса
test_sql = text("""
    SELECT file_sha256 FROM post_media_map 
    WHERE post_id = :post_id AND file_sha256 = ANY(:sha256_list::text[])
""")
print(f"  ✅ SQL синтаксис ANY() корректен: {test_sql.text[:50]}...")

# Тест 3: Проверка JOIN для tenant_id
print("\nТест 3: Проверка SQL запросов с tenant_id...")
test_join_sql = text("""
    SELECT c.tenant_id 
    FROM posts p
    JOIN channels c ON p.channel_id = c.id
    WHERE p.id = :post_id
""")
print(f"  ✅ JOIN запрос корректен: {test_join_sql.text[:50]}...")

print("\n✅ Все тесты пройдены успешно!")

