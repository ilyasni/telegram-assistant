#!/usr/bin/env python3
"""
Скрипт для проверки статуса fallback на OpenRouter при обнаружении фильтров.

Проверяет:
1. Наличие OPENROUTER_API_KEY в окружении
2. Логи на наличие сообщений о фильтрах и fallback
3. Метрики Prometheus
"""

import os
import sys
import subprocess
from pathlib import Path

# Добавляем путь к проекту
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def check_env_vars():
    """Проверка наличия переменных окружения для OpenRouter."""
    print("=" * 60)
    print("1. Проверка переменных окружения")
    print("=" * 60)
    
    api_key = os.getenv('OPENROUTER_API_KEY')
    api_base = os.getenv('OPENROUTER_API_BASE', 'https://openrouter.ai/api/v1')
    model = os.getenv('OPENROUTER_MODEL', 'qwen/qwen-2.5-72b-instruct:free')
    
    if api_key:
        print(f"✅ OPENROUTER_API_KEY: установлен (длина: {len(api_key)} символов)")
        if api_key.startswith('your_') or api_key == 'your_openrouter_api_key_here':
            print("   ⚠️  ВНИМАНИЕ: Используется placeholder значение!")
    else:
        print("❌ OPENROUTER_API_KEY: НЕ установлен")
        print("   Fallback на OpenRouter не будет работать!")
    
    print(f"   OPENROUTER_API_BASE: {api_base}")
    print(f"   OPENROUTER_MODEL: {model}")
    print()
    
    return api_key is not None and not (api_key.startswith('your_') or api_key == 'your_openrouter_api_key_here')

def check_docker_logs():
    """Проверка логов Docker контейнеров на наличие сообщений о фильтрах."""
    print("=" * 60)
    print("2. Проверка логов Docker контейнеров")
    print("=" * 60)
    
    # Определяем контейнеры, которые могут содержать worker'ы
    containers = ['worker', 'api', 'telegram-assistant']
    
    found_containers = []
    try:
        result = subprocess.run(
            ['docker', 'ps', '--format', '{{.Names}}'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            all_containers = result.stdout.strip().split('\n')
            for container in all_containers:
                if any(name in container.lower() for name in containers):
                    found_containers.append(container)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("⚠️  Docker недоступен или команда не выполнена")
        return
    
    if not found_containers:
        print("⚠️  Не найдены контейнеры для проверки")
        return
    
    print(f"Найдено контейнеров: {len(found_containers)}")
    
    # Проверяем логи на наличие сообщений о фильтрах
    keywords = [
        'filter detected',
        'falling back to OpenRouter',
        'OpenRouter fallback',
        'OpenRouter fallback failed',
        'Gigachat filter detected'
    ]
    
    for container in found_containers:
        print(f"\n📦 Контейнер: {container}")
        try:
            # Получаем последние 100 строк логов
            result = subprocess.run(
                ['docker', 'logs', '--tail', '100', container],
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                logs = result.stdout + result.stderr
                found_messages = []
                
                for keyword in keywords:
                    if keyword.lower() in logs.lower():
                        # Находим строки с этим ключевым словом
                        lines = [line for line in logs.split('\n') if keyword.lower() in line.lower()]
                        found_messages.extend(lines[:3])  # Берем первые 3 вхождения
                
                if found_messages:
                    print(f"   ✅ Найдены сообщения о фильтрах/fallback ({len(found_messages)} вхождений):")
                    for msg in found_messages[:5]:  # Показываем первые 5
                        print(f"      {msg[:100]}...")
                else:
                    print("   ℹ️  Сообщений о фильтрах/fallback не найдено в последних 100 строках")
            else:
                print(f"   ⚠️  Не удалось получить логи (код: {result.returncode})")
        except subprocess.TimeoutExpired:
            print(f"   ⚠️  Таймаут при получении логов")
        except Exception as e:
            print(f"   ⚠️  Ошибка: {e}")

def check_prometheus_metrics():
    """Проверка метрик Prometheus (если доступны)."""
    print("\n" + "=" * 60)
    print("3. Проверка метрик Prometheus")
    print("=" * 60)
    
    metrics_to_check = [
        'digest_gigachat_filter_detected_total',
        'digest_openrouter_fallback_total',
        'digest_openrouter_fallback_failed_total'
    ]
    
    # Пытаемся получить метрики через curl (если Prometheus доступен)
    prometheus_url = os.getenv('PROMETHEUS_URL', 'http://localhost:9090')
    
    print(f"Попытка подключения к Prometheus: {prometheus_url}")
    
    for metric in metrics_to_check:
        try:
            import urllib.request
            import urllib.error
            
            url = f"{prometheus_url}/api/v1/query?query={metric}"
            req = urllib.request.Request(url)
            
            with urllib.request.urlopen(req, timeout=5) as response:
                data = response.read().decode('utf-8')
                print(f"   ✅ {metric}: метрика доступна")
                # Можно распарсить JSON и показать значения
        except urllib.error.URLError:
            print(f"   ⚠️  {metric}: Prometheus недоступен или метрика не найдена")
        except Exception as e:
            print(f"   ⚠️  {metric}: ошибка проверки - {e}")

def test_filter_detection():
    """Тест детектирования фильтра."""
    print("\n" + "=" * 60)
    print("4. Тест детектирования фильтра")
    print("=" * 60)
    
    # Импортируем метод детектирования
    try:
        from api.services.digest_service import DigestService
        
        # Создаем экземпляр сервиса (без реальных зависимостей для теста)
        service = DigestService(
            qdrant_url="http://localhost:6333",
            qdrant_client=None
        )
        
        # Тестовые сообщения с фильтром
        test_messages = [
            "Генеративные языковые модели не обладают собственным мнением — их ответы являются обобщением информации, находящейся в открытом доступе.",
            "Обычный текст без фильтра",
            "Разговоры на чувствительные темы могут быть ограничены во избежание неправильного толкования.",
            "К сожалению, ограничены",
        ]
        
        print("Тестирование детектирования фильтра:")
        for i, msg in enumerate(test_messages, 1):
            is_filter = service._is_gigachat_filter_response(msg)
            status = "✅ ДЕТЕКТИРОВАН" if is_filter else "❌ НЕ детектирован"
            print(f"   {i}. {status}")
            print(f"      Текст: {msg[:80]}...")
    except Exception as e:
        print(f"   ⚠️  Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Основная функция."""
    print("\n" + "=" * 60)
    print("ПРОВЕРКА СТАТУСА FALLBACK НА OPENROUTER")
    print("=" * 60)
    print()
    
    env_ok = check_env_vars()
    check_docker_logs()
    check_prometheus_metrics()
    test_filter_detection()
    
    print("\n" + "=" * 60)
    print("ИТОГИ")
    print("=" * 60)
    
    if env_ok:
        print("✅ Переменные окружения настроены")
        print("   Fallback должен работать при обнаружении фильтров")
    else:
        print("❌ Переменные окружения НЕ настроены")
        print("   Fallback НЕ будет работать!")
        print("\n   Для настройки добавьте в .env:")
        print("   OPENROUTER_API_KEY=your_actual_api_key")
        print("   OPENROUTER_MODEL=qwen/qwen-2.5-72b-instruct:free")
    
    print("\n💡 Рекомендации:")
    print("   1. Проверьте логи worker'ов на наличие ошибок fallback")
    print("   2. Убедитесь, что OPENROUTER_API_KEY валиден")
    print("   3. Проверьте метрики Prometheus для статистики fallback")
    print()

if __name__ == '__main__':
    main()
