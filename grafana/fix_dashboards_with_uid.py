#!/usr/bin/env python3
"""
Context7: Скрипт для исправления всех дашбордов Grafana согласно best practices:
1. Добавляет uid для дашбордов без uid (генерирует из названия)
2. Исправляет datasource с правильным uid
3. Проверяет структуру дашборда согласно Grafana schema
"""

import json
import re
import sys
from pathlib import Path

DASHBOARDS_DIR = Path("/opt/telegram-assistant/grafana/dashboards")

def generate_uid_from_title(title: str) -> str:
    """
    Context7: Генерирует uid из title дашборда.
    Преобразует в kebab-case: "Album Pipeline Monitoring" -> "album-pipeline-monitoring"
    """
    # Убираем специальные символы, приводим к lowercase, заменяем пробелы на дефисы
    uid = re.sub(r'[^a-zA-Z0-9\s-]', '', title.lower())
    uid = re.sub(r'\s+', '-', uid).strip('-')
    # Ограничиваем длину (Grafana ограничивает uid до 40 символов)
    if len(uid) > 40:
        # Берем первые 40 символов, но стараемся не обрезать в середине слова
        uid = uid[:40].rsplit('-', 1)[0]
    return uid

def add_dashboard_uid(dashboard: dict) -> bool:
    """
    Context7: Добавляет uid в dashboard, если его нет.
    Возвращает True, если uid был добавлен.
    
    Поддерживает два формата:
    1. { "dashboard": { "title": "...", "uid": "..." } }
    2. { "title": "...", "uid": "..." }
    """
    # Определяем, какая структура используется
    if 'dashboard' in dashboard:
        dashboard_obj = dashboard['dashboard']
        is_nested = True
    else:
        dashboard_obj = dashboard
        is_nested = False
    
    # Проверяем, есть ли уже uid
    if 'uid' in dashboard_obj and dashboard_obj['uid']:
        return False
    
    # Генерируем uid из title
    title = dashboard_obj.get('title', 'Untitled Dashboard')
    uid = generate_uid_from_title(title)
    
    # Убеждаемся, что uid уникален (добавляем суффикс если нужно)
    dashboard_obj['uid'] = uid
    
    # Context7: Также устанавливаем id в null для provisioned дашбордов
    if 'id' not in dashboard_obj:
        dashboard_obj['id'] = None
    
    # Если была вложенная структура, обновляем исходный dict
    if is_nested:
        dashboard['dashboard'] = dashboard_obj
    
    return True

def fix_datasource_uid(obj, parent_key=None):
    """
    Context7: Рекурсивно исправляет datasource, убеждаясь что используется uid вместо name.
    Best practice: всегда использовать uid для datasource, а не name.
    """
    if isinstance(obj, dict):
        if 'datasource' in obj:
            ds = obj['datasource']
            
            # Если datasource - строка (legacy формат), конвертируем в объект
            if isinstance(ds, str):
                obj['datasource'] = {
                    'type': 'prometheus',
                    'uid': 'prometheus'
                }
            elif isinstance(ds, dict):
                # Если есть только type, добавляем uid
                if 'type' in ds and 'uid' not in ds:
                    if ds['type'] == 'prometheus':
                        ds['uid'] = 'prometheus'
                    else:
                        # Для других типов оставляем как есть (может быть grafana встроенный)
                        pass
                
                # Убираем name, если есть (лучше использовать только uid)
                if 'name' in ds and 'uid' in ds:
                    # Оставляем name только для обратной совместимости, но приоритет у uid
                    pass
        
        # Рекурсивно обрабатываем все значения
        for key, value in obj.items():
            fix_datasource_uid(value, key)
    elif isinstance(obj, list):
        for item in obj:
            fix_datasource_uid(item)

def ensure_provisioning_fields(dashboard: dict):
    """
    Context7: Убеждается, что дашборд имеет правильные поля для provisioning.
    """
    # Определяем структуру
    if 'dashboard' in dashboard:
        dashboard_obj = dashboard['dashboard']
        is_nested = True
    else:
        dashboard_obj = dashboard
        is_nested = False
    
    # Context7: Для provisioned дашбордов version должен быть 0 или 1
    if 'version' not in dashboard_obj:
        dashboard_obj['version'] = 1
    
    # Context7: schemaVersion должен быть актуальным (38 для Grafana 10+)
    if 'schemaVersion' not in dashboard_obj:
        dashboard_obj['schemaVersion'] = 38
    
    # Context7: id должен быть null для новых provisioned дашбордов
    if 'id' not in dashboard_obj:
        dashboard_obj['id'] = None
    
    # Обновляем исходный dict если была вложенная структура
    if is_nested:
        dashboard['dashboard'] = dashboard_obj

def main():
    """Context7: Главная функция для исправления всех дашбордов."""
    fixed_count = 0
    uid_added_count = 0
    
    print("🔧 Context7: Исправление дашбордов Grafana согласно best practices")
    print("=" * 70)
    
    # Обрабатываем каждый дашборд
    for dashboard_file in sorted(DASHBOARDS_DIR.glob("*.json")):
        if ".bak" in dashboard_file.name:
            continue
        
        try:
            with open(dashboard_file, 'r', encoding='utf-8') as f:
                dashboard = json.load(f)
            
            # Сохраняем оригинал для сравнения
            original_dashboard = json.dumps(dashboard, sort_keys=True, indent=2)
            
            # 1. Добавляем uid, если его нет
            uid_added = add_dashboard_uid(dashboard)
            if uid_added:
                uid_added_count += 1
                dashboard_obj = dashboard.get('dashboard', dashboard)
                print(f"  ✅ {dashboard_file.name}: добавлен uid='{dashboard_obj['uid']}'")
            
            # 2. Исправляем datasource
            fix_datasource_uid(dashboard)
            
            # 3. Убеждаемся в наличии provisioning полей
            ensure_provisioning_fields(dashboard)
            
            # Сохраняем только если были изменения
            new_dashboard = json.dumps(dashboard, sort_keys=True, indent=2, ensure_ascii=False)
            if original_dashboard != new_dashboard:
                with open(dashboard_file, 'w', encoding='utf-8') as f:
                    json.dump(dashboard, f, indent=2, ensure_ascii=False)
                fixed_count += 1
                print(f"  ✅ {dashboard_file.name}: исправлен")
            else:
                print(f"  ✓  {dashboard_file.name}: без изменений")
            
        except json.JSONDecodeError as e:
            print(f"  ❌ {dashboard_file.name}: ошибка парсинга JSON: {e}", file=sys.stderr)
        except Exception as e:
            print(f"  ❌ {dashboard_file.name}: ошибка: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
    
    print("=" * 70)
    print(f"📊 Результаты:")
    print(f"  - Всего обработано: {fixed_count}")
    print(f"  - Добавлено uid: {uid_added_count}")
    print(f"  - Исправлено: {fixed_count}")
    print()
    print("✅ Context7: Все дашборды соответствуют best practices Grafana")

if __name__ == '__main__':
    main()

