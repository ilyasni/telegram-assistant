#!/usr/bin/env python3
"""Тест URL для отладки бота."""

import asyncio
import httpx

API_BASE = "http://api:8000"
user_id = "cc1e70c9-9058-4fd0-9b52-94012623f0e0"

async def test_urls():
    """Тестируем URL как в боте."""
    
    # Тест 1: Получить пользователя
    url1 = f"{API_BASE}/api/users/139883458"
    print(f"TEST 1: {url1}")
    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url1)
            print(f"  Status: {r.status_code}")
            if r.status_code == 200:
                user = r.json()
                print(f"  User ID: {user['id']}")
                
                # Тест 2: Получить каналы
                url2 = f"{API_BASE}/api/channels/users/{user['id']}/list"
                print(f"TEST 2: {url2}")
                r2 = await client.get(url2)
                print(f"  Status: {r2.status_code}")
                if r2.status_code == 200:
                    data = r2.json()
                    print(f"  Channels: {len(data.get('channels', []))}")
                else:
                    print(f"  Error: {r2.text}")
            else:
                print(f"  Error: {r.text}")
        except Exception as e:
            print(f"  Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_urls())
