#!/usr/bin/env python3
"""
Скрипт валидации изменений в коде.
Проверяет корректность импортов, использование новых классов и методов.
"""

import sys
import os

# Добавляем пути для импорта
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'worker'))
sys.path.insert(0, os.path.join(project_root, 'shared', 'python'))

def test_vision_enrichment_schema():
    """Проверка Pydantic схемы VisionEnrichment."""
    try:
        from events.schemas.posts_vision_v1 import VisionEnrichment
        
        # Тест минимальных обязательных полей
        test_data = {
            'classification': 'photo',
            'description': 'Test description with enough characters',
            'is_meme': False
        }
        v = VisionEnrichment(**test_data)
        print("✅ VisionEnrichment schema: минимальная валидация OK")
        
        # Тест полных данных
        full_data = {
            'classification': 'meme',
            'description': 'A funny meme with text overlay',
            'is_meme': True,
            'labels': ['humor', 'comedy', 'text'],
            'objects': ['person', 'laptop'],
            'scene': 'indoor',
            'ocr': {'text': 'Hello world', 'engine': 'gigachat', 'confidence': 0.95},
            'nsfw_score': 0.1,
            'aesthetic_score': 0.7,
            'dominant_colors': ['#ff0000', '#00ff00']
        }
        v2 = VisionEnrichment(**full_data)
        print("✅ VisionEnrichment schema: полная валидация OK")
        
        return True
    except Exception as e:
        print(f"❌ VisionEnrichment schema validation failed: {e}")
        return False

def test_imports():
    """Проверка корректности импортов."""
    errors = []
    
    try:
        from events.schemas.posts_vision_v1 import VisionEnrichment, VisionAnalysisResult
        print("✅ Импорт VisionEnrichment OK")
    except Exception as e:
        errors.append(f"VisionEnrichment import: {e}")
        print(f"❌ Импорт VisionEnrichment failed: {e}")
    
    try:
        from prometheus_client import Counter
        # Проверка что метрики могут быть созданы
        test_metric = Counter('test_validation_metric', 'Test', ['label'])
        print("✅ Prometheus метрики OK")
    except Exception as e:
        errors.append(f"Prometheus metrics: {e}")
        print(f"❌ Prometheus метрики failed: {e}")
    
    return len(errors) == 0

def check_file_structure():
    """Проверка структуры файлов."""
    files_to_check = [
        'worker/events/schemas/posts_vision_v1.py',
        'worker/ai_adapters/gigachat_vision.py',
        'worker/tasks/vision_analysis_task.py',
        'crawl4ai/enrichment_engine.py',
        'crawl4ai/crawl4ai_service.py',
        'api/services/s3_storage.py',
        'worker/tasks/indexing_task.py',
        'worker/integrations/neo4j_client.py'
    ]
    
    missing_files = []
    for file_path in files_to_check:
        full_path = os.path.join(project_root, file_path)
        if not os.path.exists(full_path):
            missing_files.append(file_path)
            print(f"❌ Файл не найден: {file_path}")
        else:
            print(f"✅ Файл существует: {file_path}")
    
    return len(missing_files) == 0

def main():
    print("=" * 60)
    print("Валидация изменений в коде")
    print("=" * 60)
    print()
    
    results = []
    
    print("1. Проверка структуры файлов:")
    results.append(("Структура файлов", check_file_structure()))
    print()
    
    print("2. Проверка импортов:")
    results.append(("Импорты", test_imports()))
    print()
    
    print("3. Проверка VisionEnrichment схемы:")
    results.append(("VisionEnrichment схема", test_vision_enrichment_schema()))
    print()
    
    print("=" * 60)
    print("Итоги валидации:")
    print("=" * 60)
    
    all_passed = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{status}: {name}")
        if not passed:
            all_passed = False
    
    print()
    if all_passed:
        print("✅ Все проверки пройдены успешно!")
        return 0
    else:
        print("❌ Некоторые проверки не пройдены")
        return 1

if __name__ == "__main__":
    sys.exit(main())

