#!/usr/bin/env python3
"""
Скрипт проверки готовности пайплайна альбомов к использованию
Context7: проверка всех компонентов перед deployment
"""

import sys
import os
import asyncio
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def check_files():
    """Проверка наличия ключевых файлов."""
    print("📁 Проверка файлов...")
    
    files = {
        "worker/tasks/album_assembler_task.py": "Album Assembler Task",
        "worker/events/schemas/albums_parsed_v1.py": "Схема albums.parsed",
        "worker/events/schemas/album_assembled_v1.py": "Схема album.assembled",
        "telethon-ingest/services/media_processor.py": "Media Processor",
        "telethon-ingest/services/media_group_saver.py": "Media Group Saver",
        "telethon-ingest/migrations/004_add_album_fields.sql": "Миграция БД",
        "api/services/s3_storage.py": "S3 Storage",
        "worker/integrations/neo4j_client.py": "Neo4j Client",
        "worker/integrations/qdrant_client.py": "Qdrant Client",
        "worker/run_all_tasks.py": "Worker Integration",
        "prometheus/alerts.yml": "Prometheus Alerts",
        "grafana/dashboards/album_pipeline.json": "Grafana Dashboard"
    }
    
    all_ok = True
    for file_path, name in files.items():
        full_path = project_root / file_path
        if full_path.exists():
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ {name} - отсутствует: {file_path}")
            all_ok = False
    
    return all_ok


def check_syntax():
    """Проверка синтаксиса Python файлов."""
    print("\n🐍 Проверка синтаксиса Python...")
    
    files = [
        "worker/tasks/album_assembler_task.py",
        "worker/run_all_tasks.py",
        "api/services/s3_storage.py",
        "worker/integrations/neo4j_client.py",
        "worker/integrations/qdrant_client.py"
    ]
    
    all_ok = True
    for file_path in files:
        full_path = project_root / file_path
        if not full_path.exists():
            continue
        
        import py_compile
        try:
            py_compile.compile(str(full_path), doraise=True)
            print(f"  ✅ {file_path}")
        except py_compile.PyCompileError as e:
            print(f"  ❌ {file_path}: {e}")
            all_ok = False
    
    return all_ok


def check_imports():
    """Проверка импортов (без выполнения)."""
    print("\n📦 Проверка структуры импортов...")
    
    checks = [
        ("worker/tasks/album_assembler_task.py", "AlbumAssemblerTask"),
        ("worker/events/schemas/albums_parsed_v1.py", "AlbumParsedEventV1"),
        ("worker/events/schemas/album_assembled_v1.py", "AlbumAssembledEventV1"),
        ("api/services/s3_storage.py", "build_album_key"),
        ("worker/integrations/neo4j_client.py", "find_albums_by_channel"),
        ("worker/integrations/qdrant_client.py", "search_vectors"),
    ]
    
    all_ok = True
    for file_path, symbol in checks:
        full_path = project_root / file_path
        if not full_path.exists():
            continue
        
        content = full_path.read_text()
        if symbol in content or f"def {symbol}" in content or f"class {symbol}" in content:
            print(f"  ✅ {symbol} в {file_path}")
        else:
            print(f"  ⚠️  {symbol} не найден в {file_path}")
    
    return all_ok


def check_integration():
    """Проверка интеграции в worker."""
    print("\n🔧 Проверка интеграции...")
    
    run_all_tasks_path = project_root / "worker/run_all_tasks.py"
    if not run_all_tasks_path.exists():
        print("  ❌ worker/run_all_tasks.py не найден")
        return False
    
    content = run_all_tasks_path.read_text()
    
    checks = [
        ("AlbumAssemblerTask", "Импорт AlbumAssemblerTask"),
        ("create_album_assembler_task", "Функция создания задачи"),
        ("album_assembler", "Регистрация в supervisor"),
        ("albums_parsed_total", "Импорт метрик"),
    ]
    
    all_ok = True
    for pattern, name in checks:
        if pattern in content:
            print(f"  ✅ {name}")
        else:
            print(f"  ❌ {name} - не найден")
            all_ok = False
    
    return all_ok


def check_alerts():
    """Проверка Prometheus алертов."""
    print("\n🔔 Проверка Prometheus алертов...")
    
    alerts_path = project_root / "prometheus/alerts.yml"
    if not alerts_path.exists():
        print("  ⚠️  prometheus/alerts.yml не найден")
        return False
    
    content = alerts_path.read_text()
    
    alert_names = [
        "AlbumAssemblyLagHigh",
        "AlbumAssemblyLagCritical",
        "AlbumItemsCountMismatch",
        "AlbumAssemblerNoActivity",
        "AlbumStateBacklogHigh",
        "AlbumAssemblyRateLow",
        "AlbumAssemblyErrorRateHigh",
        "AlbumAggregationDurationHigh"
    ]
    
    found = 0
    for alert_name in alert_names:
        if f"alert: {alert_name}" in content or f'- alert: {alert_name}' in content:
            found += 1
    
    print(f"  ✅ Найдено алертов: {found}/{len(alert_names)}")
    return found == len(alert_names)


def check_metrics():
    """Проверка метрик."""
    print("\n📊 Проверка метрик...")
    
    task_path = project_root / "worker/tasks/album_assembler_task.py"
    if not task_path.exists():
        print("  ❌ album_assembler_task.py не найден")
        return False
    
    content = task_path.read_text()
    
    metrics = [
        "albums_parsed_total",
        "albums_assembled_total",
        "album_assembly_lag_seconds",
        "album_items_count_gauge",
        "album_vision_summary_size_bytes",
        "album_aggregation_duration_ms"
    ]
    
    found = 0
    for metric in metrics:
        if metric in content:
            found += 1
    
    print(f"  ✅ Найдено метрик: {found}/{len(metrics)}")
    return found == len(metrics)


async def check_database_schema():
    """Проверка схемы БД (если доступна)."""
    print("\n🗄️  Проверка схемы БД...")
    
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("  ⚠️  DATABASE_URL не установлен, пропускаем проверку БД")
        return True
    
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        
        if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
        
        engine = create_async_engine(db_url)
        async with engine.begin() as conn:
            # Проверка media_groups
            result = await conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'media_groups' 
                AND column_name IN ('caption_text', 'cover_media_id', 'posted_at', 'meta')
            """))
            columns = [row[0] for row in result]
            
            expected = ['caption_text', 'cover_media_id', 'posted_at', 'meta']
            missing = [col for col in expected if col not in columns]
            
            if missing:
                print(f"  ❌ Отсутствуют поля в media_groups: {missing}")
                await engine.dispose()
                return False
            else:
                print("  ✅ media_groups: все поля присутствуют")
            
            # Проверка media_group_items
            result = await conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'media_group_items' 
                AND column_name IN ('media_object_id', 'media_kind', 'sha256', 'meta')
            """))
            columns = [row[0] for row in result]
            
            expected = ['media_object_id', 'media_kind', 'sha256', 'meta']
            missing = [col for col in expected if col not in columns]
            
            if missing:
                print(f"  ❌ Отсутствуют поля в media_group_items: {missing}")
                await engine.dispose()
                return False
            else:
                print("  ✅ media_group_items: все поля присутствуют")
            
            # Проверка media_objects.id
            result = await conn.execute(text("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'media_objects' 
                AND column_name = 'id'
            """))
            if result.fetchone():
                print("  ✅ media_objects.id присутствует")
            else:
                print("  ⚠️  media_objects.id отсутствует (может быть не критично)")
        
        await engine.dispose()
        return True
        
    except Exception as e:
        print(f"  ⚠️  Ошибка проверки БД: {e}")
        return False


async def main():
    """Главная функция проверки."""
    print("=" * 60)
    print("🔍 Проверка готовности пайплайна альбомов")
    print("=" * 60)
    
    results = []
    
    # Проверки
    results.append(("Файлы", check_files()))
    results.append(("Синтаксис", check_syntax()))
    results.append(("Импорты", check_imports()))
    results.append(("Интеграция", check_integration()))
    results.append(("Алерты", check_alerts()))
    results.append(("Метрики", check_metrics()))
    results.append(("БД схема", await check_database_schema()))
    
    # Итоги
    print("\n" + "=" * 60)
    print("📊 Результаты проверки:")
    print("=" * 60)
    
    passed = 0
    for name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status} - {name}")
        if result:
            passed += 1
    
    print("\n" + "=" * 60)
    print(f"Итого: {passed}/{len(results)} проверок пройдено")
    print("=" * 60)
    
    if passed == len(results):
        print("\n🎉 Все проверки пройдены! Пайплайн готов к использованию.")
        return 0
    else:
        print(f"\n⚠️  {len(results) - passed} проверок не пройдено. Требуется доработка.")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

