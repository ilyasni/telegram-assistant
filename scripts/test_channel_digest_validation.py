#!/usr/bin/env python3
"""
Скрипт для проверки валидации и логики канального дайджеста.
Context7: Проверка соответствия best practices.
"""

import sys
import json
from pathlib import Path

# Добавляем путь к модулям
sys.path.insert(0, str(Path(__file__).parent.parent / "api"))

def check_digest_service():
    """Проверка DigestService на соответствие Context7."""
    print("=" * 60)
    print("Проверка DigestService")
    print("=" * 60)
    
    issues = []
    
    # Читаем файл
    service_file = Path(__file__).parent.parent / "api" / "services" / "digest_service.py"
    content = service_file.read_text()
    
    # 1. Проверка метрик (низкая кардинальность)
    if 'channel_id' in content and 'labels' in content:
        # Ищем метрики с channel_id в labels
        lines = content.split('\n')
        for i, line in enumerate(lines, 1):
            if 'channel_digest' in line and 'labels' in line and 'channel_id' in line:
                issues.append(f"⚠️  Строка {i}: channel_id в labels метрики (высокая кардинальность)")
    
    # 2. Проверка логирования с tenant_id
    required_log_fields = ['tenant_id', 'user_id', 'channel_id', 'period']
    for field in required_log_fields:
        if f'logger.info' in content and f'"{field}"' not in content and f"'{field}'" not in content:
            # Проверяем, есть ли хотя бы в одном месте
            if field == 'period':
                # period может быть period_days
                if 'period_days' not in content:
                    issues.append(f"⚠️  Нет логирования поля '{field}' в некоторых местах")
    
    # 3. Проверка обработки ошибок
    if 'except Exception' in content:
        # Проверяем, что ошибки логируются
        exception_blocks = content.count('except Exception')
        logger_error_blocks = content.count('logger.error') + content.count('logger.warning')
        if exception_blocks > logger_error_blocks * 2:
            issues.append(f"⚠️  Возможно, не все исключения логируются ({exception_blocks} блоков except, {logger_error_blocks} логов)")
    
    # 4. Проверка кеширования
    if '_get_cached_digest' not in content or '_save_to_cache' not in content:
        issues.append("❌ Отсутствуют методы кеширования")
    else:
        print("✅ Методы кеширования присутствуют")
    
    # 5. Проверка токен-бюджета
    if '_estimate_tokens' not in content:
        issues.append("❌ Отсутствует метод оценки токенов")
    else:
        print("✅ Метод оценки токенов присутствует")
    
    # 6. Проверка двухступенчатого саммари для месяца
    if '_generate_map_reduce_digest' not in content:
        issues.append("❌ Отсутствует метод двухступенчатого саммари")
    else:
        print("✅ Метод двухступенчатого саммари присутствует")
    
    if issues:
        print("\n⚠️  Обнаружены проблемы:")
        for issue in issues:
            print(f"  {issue}")
    else:
        print("\n✅ Все проверки пройдены")
    
    return len(issues) == 0


def check_api_endpoint():
    """Проверка API endpoint."""
    print("\n" + "=" * 60)
    print("Проверка API Endpoint")
    print("=" * 60)
    
    issues = []
    
    # Читаем файл
    channels_file = Path(__file__).parent.parent / "api" / "routers" / "channels.py"
    content = channels_file.read_text()
    
    # 1. Проверка user-scoped паттерна
    if '/users/{user_id}/channels/{channel_id}/digest' not in content:
        issues.append("❌ Endpoint не использует user-scoped паттерн")
    else:
        print("✅ Endpoint использует user-scoped паттерн")
    
    # 2. Проверка валидации доступа
    if 'user_channel' not in content.lower() or 'JOIN' not in content:
        issues.append("❌ Отсутствует валидация доступа через user_channel JOIN")
    else:
        print("✅ Валидация доступа через JOIN присутствует")
    
    # 3. Проверка валидации периода
    if 'period' in content and ('Query' not in content or 'ge=1' not in content or 'le=30' not in content):
        issues.append("⚠️  Возможно неполная валидация периода")
    else:
        print("✅ Валидация периода присутствует")
    
    # 4. Проверка tenant_id в логах
    if 'tenant_id' not in content or 'logger' not in content:
        issues.append("⚠️  Возможно отсутствие tenant_id в логах")
    else:
        print("✅ tenant_id используется в логах")
    
    if issues:
        print("\n⚠️  Обнаружены проблемы:")
        for issue in issues:
            print(f"  {issue}")
    else:
        print("\n✅ Все проверки пройдены")
    
    return len(issues) == 0


def check_bot_integration():
    """Проверка bot integration."""
    print("\n" + "=" * 60)
    print("Проверка Bot Integration")
    print("=" * 60)
    
    issues = []
    
    # Читаем файл
    base_file = Path(__file__).parent.parent / "api" / "bot" / "handlers" / "base.py"
    content = base_file.read_text()
    
    # 1. Проверка кнопки в клавиатуре
    if 'channel:digest:' not in content:
        issues.append("❌ Отсутствует callback для дайджеста")
    else:
        print("✅ Callback для дайджеста присутствует")
    
    # 2. Проверка обработчика
    if 'on_channel_digest' not in content:
        issues.append("❌ Отсутствует обработчик on_channel_digest")
    else:
        print("✅ Обработчик on_channel_digest присутствует")
    
    # 3. Проверка обработки ошибок
    if '_generate_channel_digest' in content:
        if 'httpx.HTTPStatusError' not in content or 'httpx.TimeoutException' not in content:
            issues.append("⚠️  Возможно неполная обработка ошибок HTTP")
        else:
            print("✅ Обработка HTTP ошибок присутствует")
    
    if issues:
        print("\n⚠️  Обнаружены проблемы:")
        for issue in issues:
            print(f"  {issue}")
    else:
        print("\n✅ Все проверки пройдены")
    
    return len(issues) == 0


def main():
    """Главная функция проверки."""
    print("🔍 Проверка реализации канального дайджеста на соответствие Context7\n")
    
    results = []
    results.append(("DigestService", check_digest_service()))
    results.append(("API Endpoint", check_api_endpoint()))
    results.append(("Bot Integration", check_bot_integration()))
    
    print("\n" + "=" * 60)
    print("Итоговые результаты")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{name:20} {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        print("\n🎉 Все проверки пройдены успешно!")
        return 0
    else:
        print("\n⚠️  Обнаружены проблемы, требующие внимания")
        return 1


if __name__ == "__main__":
    sys.exit(main())
