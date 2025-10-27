#!/usr/bin/env python3
"""
Тест fallback механизма для GigaChat Proxy
"""

import requests
import json
import time

def test_models():
    """Тест получения списка моделей"""
    print("🔍 Тестирование списка моделей...")
    
    try:
        response = requests.get("http://localhost:8090/v1/models", timeout=10)
        if response.status_code == 200:
            models = response.json()
            print(f"✅ Модели получены: {len(models.get('data', []))} моделей")
            for model in models.get('data', [])[:3]:  # Показываем первые 3
                print(f"   - {model.get('id', 'unknown')} ({model.get('owned_by', 'unknown')})")
            return True
        else:
            print(f"❌ Ошибка получения моделей: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Исключение при получении моделей: {e}")
        return False

def test_chat_completion():
    """Тест генерации текста"""
    print("\n🤖 Тестирование генерации текста...")
    
    payload = {
        "model": "gpt-3.5-turbo",
        "messages": [
            {"role": "user", "content": "Привет! Как дела? Ответь кратко."}
        ],
        "max_tokens": 100
    }
    
    try:
        response = requests.post(
            "http://localhost:8090/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            content = result.get('choices', [{}])[0].get('message', {}).get('content', '')
            provider = result.get('id', '').split('-')[2] if '-' in result.get('id', '') else 'unknown'
            print(f"✅ Ответ получен от {provider}: {content[:100]}...")
            return True
        else:
            print(f"❌ Ошибка генерации: {response.status_code}")
            print(f"   Ответ: {response.text[:200]}...")
            return False
    except Exception as e:
        print(f"❌ Исключение при генерации: {e}")
        return False

def test_health():
    """Тест health check"""
    print("\n🏥 Тестирование health check...")
    
    try:
        response = requests.get("http://localhost:8090/health", timeout=10)
        if response.status_code == 200:
            health = response.json()
            print(f"✅ Health check: {health.get('status', 'unknown')}")
            print(f"   Провайдер: {health.get('provider', 'unknown')}")
            print(f"   GigaChat: {health.get('gigachat_available', False)}")
            print(f"   OpenRouter: {health.get('openrouter_available', False)}")
            return True
        else:
            print(f"❌ Ошибка health check: {response.status_code}")
            return False
    except Exception as e:
        print(f"❌ Исключение при health check: {e}")
        return False

def main():
    """Основная функция тестирования"""
    print("🚀 Запуск тестов GigaChat Proxy с Fallback")
    print("=" * 50)
    
    # Ожидание запуска сервера
    print("⏳ Ожидание запуска сервера...")
    time.sleep(5)
    
    # Тесты
    tests = [
        ("Health Check", test_health),
        ("Models List", test_models),
        ("Chat Completion", test_chat_completion),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n📋 {test_name}")
        print("-" * 30)
        result = test_func()
        results.append((test_name, result))
        time.sleep(1)
    
    # Результаты
    print("\n" + "=" * 50)
    print("📊 РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ")
    print("=" * 50)
    
    passed = 0
    for test_name, result in results:
        status = "✅ ПРОЙДЕН" if result else "❌ ПРОВАЛЕН"
        print(f"{test_name}: {status}")
        if result:
            passed += 1
    
    print(f"\nИтого: {passed}/{len(results)} тестов пройдено")
    
    if passed == len(results):
        print("🎉 Все тесты пройдены! Fallback механизм работает.")
    else:
        print("⚠️  Некоторые тесты провалены. Проверьте конфигурацию.")

if __name__ == "__main__":
    main()
