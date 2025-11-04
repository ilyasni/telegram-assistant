#!/usr/bin/env python3
"""
Скрипт проверки доступности GigaChat и всех моделей.
"""

import os
import sys
import requests
import json

def check_proxy_health():
    """Проверка health check прокси."""
    proxy_url = os.getenv("GIGACHAT_PROXY_URL", "http://gpt2giga-proxy:8090")
    
    print(f"🔍 Проверка прокси: {proxy_url}")
    
    # Проверка /v1/models
    try:
        print(f"\n1. Проверка /v1/models...")
        response = requests.get(f"{proxy_url}/v1/models", timeout=10, allow_redirects=True)
        print(f"   Status: {response.status_code}")
        print(f"   URL: {response.url}")
        
        if response.status_code == 200:
            try:
                models = response.json()
                print(f"   ✅ Успешно! Получено моделей: {len(models.get('data', []))}")
                for model in models.get('data', [])[:5]:
                    print(f"      - {model.get('id', 'unknown')}")
                return True
            except:
                print(f"   ⚠️ Ответ не JSON: {response.text[:200]}")
        else:
            print(f"   ❌ Ошибка: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка подключения: {e}")
        return False

def check_embeddings():
    """Проверка модели embeddings."""
    proxy_url = os.getenv("GIGACHAT_PROXY_URL", "http://gpt2giga-proxy:8090")
    
    print(f"\n2. Проверка embeddings...")
    try:
        # Проверка через /v1/embeddings endpoint
        response = requests.post(
            f"{proxy_url}/v1/embeddings",
            json={
                "model": "EmbeddingsGigaR",
                "input": "тест"
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
            allow_redirects=True
        )
        
        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            if 'data' in result and len(result['data']) > 0:
                embedding = result['data'][0].get('embedding', [])
                print(f"   ✅ Успешно! Размерность: {len(embedding)}")
                return True
            else:
                print(f"   ⚠️ Нет данных в ответе: {result}")
                return False
        else:
            print(f"   ❌ Ошибка: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False

def check_chat():
    """Проверка chat модели."""
    proxy_url = os.getenv("GIGACHAT_PROXY_URL", "http://gpt2giga-proxy:8090")
    
    print(f"\n3. Проверка chat модели...")
    try:
        response = requests.post(
            f"{proxy_url}/v1/chat/completions",
            json={
                "model": "GigaChat",
                "messages": [{"role": "user", "content": "Привет"}],
                "max_tokens": 10
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
            allow_redirects=True
        )
        
        print(f"   Status: {response.status_code}")
        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0].get('message', {}).get('content', '')
                print(f"   ✅ Успешно! Ответ: {content[:50]}")
                return True
            else:
                print(f"   ⚠️ Нет данных в ответе: {result}")
                return False
        else:
            print(f"   ❌ Ошибка: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False

def check_environment():
    """Проверка переменных окружения."""
    print(f"\n📋 Переменные окружения:")
    print(f"   GIGACHAT_PROXY_URL: {os.getenv('GIGACHAT_PROXY_URL', 'не установлено')}")
    print(f"   FEATURE_GIGACHAT_ENABLED: {os.getenv('FEATURE_GIGACHAT_ENABLED', 'не установлено')}")
    print(f"   USE_GIGACHAT_PROXY: {os.getenv('USE_GIGACHAT_PROXY', 'не установлено')}")
    print(f"   GIGACHAT_SCOPE: {os.getenv('GIGACHAT_SCOPE', 'не установлено')}")
    print(f"   GIGACHAT_CREDENTIALS: {'установлено' if os.getenv('GIGACHAT_CREDENTIALS') else 'не установлено'}")

if __name__ == '__main__':
    check_environment()
    
    print(f"\n{'='*60}")
    results = []
    
    results.append(("Health Check (/v1/models)", check_proxy_health()))
    results.append(("Embeddings", check_embeddings()))
    results.append(("Chat", check_chat()))
    
    print(f"\n{'='*60}")
    print("📊 Итоги:")
    for name, result in results:
        status = "✅" if result else "❌"
        print(f"   {status} {name}")
    
    all_ok = all(r for _, r in results)
    sys.exit(0 if all_ok else 1)

