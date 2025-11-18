#!/usr/bin/env python3
"""
Проверка всех моделей GigaChat по пайплайну:
1. GigaChat (latest)
2. GigaChat Pro  
3. Embeddings (EmbeddingsGigaR)
"""

import os
import sys
import requests
import json
from typing import Dict, Any

proxy_url = os.getenv("GIGACHAT_PROXY_URL", "http://gpt2giga-proxy:8090")

def check_models_list():
    """Проверка списка доступных моделей."""
    print("🔍 Проверка списка моделей...")
    try:
        response = requests.get(
            f"{proxy_url}/v1/models",
            timeout=10,
            allow_redirects=True
        )
        
        if response.status_code == 200:
            data = response.json()
            models = data.get('data', [])
            print(f"✅ Получено моделей: {len(models)}")
            
            model_ids = [m.get('id', 'unknown') for m in models]
            print("\n📋 Доступные модели:")
            for model_id in model_ids:
                print(f"   - {model_id}")
            
            return model_ids
        else:
            print(f"❌ Ошибка: {response.status_code} - {response.text[:200]}")
            return []
    except Exception as e:
        print(f"❌ Ошибка подключения: {e}")
        return []

def check_chat_model(model_name: str) -> bool:
    """Проверка chat модели."""
    print(f"\n💬 Проверка модели {model_name}...")
    try:
        response = requests.post(
            f"{proxy_url}/v1/chat/completions",
            json={
                "model": model_name,
                "messages": [{"role": "user", "content": "Привет, назови себя одним словом"}],
                "max_tokens": 10,
                "temperature": 0.1
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
            allow_redirects=True
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'choices' in result and len(result['choices']) > 0:
                content = result['choices'][0].get('message', {}).get('content', '')
                print(f"   ✅ Успешно! Ответ: {content[:50]}")
                return True
            else:
                print(f"   ⚠️ Нет ответа в choices: {result}")
                return False
        else:
            print(f"   ❌ Ошибка {response.status_code}: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False

def check_embeddings_model(model_name: str = "EmbeddingsGigaR") -> bool:
    """Проверка модели embeddings."""
    print(f"\n🔢 Проверка модели embeddings: {model_name}...")
    try:
        response = requests.post(
            f"{proxy_url}/v1/embeddings",
            json={
                "model": model_name,
                "input": "тестовый текст для эмбеддинга"
            },
            headers={"Content-Type": "application/json"},
            timeout=30,
            allow_redirects=True
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'data' in result and len(result['data']) > 0:
                embedding = result['data'][0].get('embedding', [])
                print(f"   ✅ Успешно! Размерность: {len(embedding)}")
                if len(embedding) > 0:
                    print(f"   📊 Первые 5 значений: {embedding[:5]}")
                return True
            else:
                print(f"   ⚠️ Нет данных в ответе: {result}")
                return False
        else:
            print(f"   ❌ Ошибка {response.status_code}: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"   ❌ Ошибка: {e}")
        return False

if __name__ == '__main__':
    print("="*60)
    print("Проверка моделей GigaChat по пайплайну")
    print("="*60)
    
    # 1. Проверка списка моделей
    available_models = check_models_list()
    
    # 2. Проверка chat моделей
    print("\n" + "="*60)
    print("Проверка Chat моделей:")
    print("="*60)
    
    chat_results = {}
    for model in ["GigaChat", "GigaChat-Pro", "GigaChat Pro"]:
        chat_results[model] = check_chat_model(model)
    
    # 3. Проверка embeddings
    print("\n" + "="*60)
    print("Проверка Embeddings:")
    print("="*60)
    embeddings_result = check_embeddings_model("EmbeddingsGigaR")
    
    # Итоги
    print("\n" + "="*60)
    print("📊 Итоги:")
    print("="*60)
    
    print("\n💬 Chat модели:")
    for model, result in chat_results.items():
        status = "✅" if result else "❌"
        print(f"   {status} {model}")
    
    print("\n🔢 Embeddings:")
    status = "✅" if embeddings_result else "❌"
    print(f"   {status} EmbeddingsGigaR")
    
    all_chat_ok = any(chat_results.values())
    all_ok = all_chat_ok and embeddings_result
    
    if all_ok:
        print("\n✅ Все проверки пройдены!")
    else:
        print("\n❌ Некоторые проверки не пройдены")
        sys.exit(1)

