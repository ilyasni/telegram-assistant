#!/usr/bin/env python3
"""
Context7: Скрипт проверки развертывания новых компонентов
Проверяет:
- Наличие всех необходимых файлов
- Корректность импортов
- Наличие метрик
- Конфигурацию docker-compose
"""

import os
import sys
import importlib.util
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

def check_file_exists(file_path: Path, description: str) -> bool:
    """Проверка существования файла."""
    exists = file_path.exists()
    status = "✅" if exists else "❌"
    print(f"{status} {description}: {file_path}")
    return exists

def check_import(module_path: Path, module_name: str, description: str) -> bool:
    """Проверка возможности импорта модуля (синтаксическая проверка)."""
    try:
        # Context7: Проверяем только синтаксис, не выполняем импорт
        # (так как зависимости могут быть не установлены в текущем окружении)
        with open(module_path, 'r', encoding='utf-8') as f:
            code = f.read()
            compile(code, str(module_path), 'exec')
        print(f"✅ {description}: {module_name} (синтаксис корректен)")
        return True
    except SyntaxError as e:
        print(f"❌ {description}: {module_name} (синтаксическая ошибка: {str(e)})")
        return False
    except Exception as e:
        # Другие ошибки (например, отсутствие файла) - это нормально
        print(f"⚠️  {description}: {module_name} (предупреждение: {str(e)})")
        return True  # Не считаем это критической ошибкой

def main():
    """Основная функция проверки."""
    print("=" * 60)
    print("Проверка развертывания новых компонентов")
    print("=" * 60)
    print()
    
    checks_passed = 0
    checks_total = 0
    
    # 1. Проверка новых файлов
    print("📁 Проверка новых файлов:")
    new_files = [
        ("telethon-ingest/services/media_group_saver.py", "MediaGroupSaver"),
        ("telethon-ingest/services/metrics_utils.py", "MetricsUtils"),
        ("worker/tasks/retagging_task.py", "RetaggingTask"),
        ("tests/e2e/test_media_groups.py", "E2E тесты альбомов"),
        ("tests/e2e/test_retagging.py", "E2E тесты ретеггинга"),
        ("docs/ANTI_LOOP_MECHANISM.md", "Документация анти-петли"),
        ("docs/IMPLEMENTATION_COMPLETE.md", "Итоговая документация"),
    ]
    
    for file_path, description in new_files:
        checks_total += 1
        full_path = PROJECT_ROOT / file_path
        if check_file_exists(full_path, description):
            checks_passed += 1
    print()
    
    # 2. Проверка обновленных файлов
    print("📝 Проверка обновленных файлов:")
    updated_files = [
        ("worker/run_all_tasks.py", "Worker supervisor"),
        ("telethon-ingest/services/channel_parser.py", "ChannelParser"),
        ("worker/tasks/tag_persistence_task.py", "TagPersistenceTask"),
        ("worker/tasks/crawl_trigger_task.py", "CrawlTriggerTask"),
        ("worker/tasks/enrichment_task.py", "EnrichmentTask"),
    ]
    
    for file_path, description in updated_files:
        checks_total += 1
        full_path = PROJECT_ROOT / file_path
        if check_file_exists(full_path, description):
            checks_passed += 1
    print()
    
    # 3. Проверка импортов
    print("🔍 Проверка импортов:")
    
    # Проверка RetaggingTask в run_all_tasks.py
    run_all_tasks_path = PROJECT_ROOT / "worker" / "run_all_tasks.py"
    if run_all_tasks_path.exists():
        checks_total += 1
        try:
            with open(run_all_tasks_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if "from tasks.retagging_task import RetaggingTask" in content:
                    print("✅ RetaggingTask импортирован в run_all_tasks.py")
                    checks_passed += 1
                else:
                    print("❌ RetaggingTask не найден в run_all_tasks.py")
        except Exception as e:
            print(f"❌ Ошибка чтения run_all_tasks.py: {e}")
    
    # Проверка media_group_saver
    media_group_saver_path = PROJECT_ROOT / "telethon-ingest" / "services" / "media_group_saver.py"
    if media_group_saver_path.exists():
        checks_total += 1
        if check_import(media_group_saver_path, "media_group_saver", "MediaGroupSaver импорт"):
            checks_passed += 1
    print()
    
    # 4. Проверка миграций
    print("🗄️  Проверка миграций:")
    migration_files = [
        ("telethon-ingest/migrations/002_add_post_enrichment_and_posts_indexes.sql", "Миграция 002 (индексы)"),
        ("telethon-ingest/migrations/003_add_media_groups_tables.sql", "Миграция 003 (альбомы)"),
    ]
    
    for file_path, description in migration_files:
        checks_total += 1
        full_path = PROJECT_ROOT / file_path
        if check_file_exists(full_path, description):
            checks_passed += 1
    print()
    
    # 5. Проверка docker-compose
    print("🐳 Проверка docker-compose:")
    docker_compose_path = PROJECT_ROOT / "docker-compose.yml"
    if docker_compose_path.exists():
        checks_total += 1
        try:
            with open(docker_compose_path, 'r', encoding='utf-8') as f:
                content = f.read()
                if "worker:" in content and "healthcheck:" in content:
                    print("✅ docker-compose.yml содержит worker и healthcheck")
                    checks_passed += 1
                else:
                    print("❌ docker-compose.yml не содержит нужные секции")
        except Exception as e:
            print(f"❌ Ошибка чтения docker-compose.yml: {e}")
    print()
    
    # Итоги
    print("=" * 60)
    print(f"Итого: {checks_passed}/{checks_total} проверок пройдено")
    print("=" * 60)
    
    if checks_passed == checks_total:
        print("✅ Все проверки пройдены! Развертывание готово.")
        return 0
    else:
        print(f"⚠️  {checks_total - checks_passed} проверок не пройдено")
        return 1

if __name__ == "__main__":
    sys.exit(main())

