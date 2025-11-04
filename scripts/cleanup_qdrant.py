#!/usr/bin/env python3
"""
Скрипт для полной очистки всех Qdrant коллекций.

Context7 best practice: Использование Qdrant API для удаления коллекций.
Удаляет ВСЕ коллекции (все данные тестовые или неактуальные).
"""

import requests
import json
import sys
import os
import argparse

QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")

def get_collections():
    """Получить список коллекций"""
    try:
        response = requests.get(f"{QDRANT_URL}/collections")
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Ошибка получения коллекций: {e}")
        return None

def delete_collection(collection_name, dry_run=False):
    """Удалить коллекцию"""
    if dry_run:
        print(f"DRY-RUN: Коллекция {collection_name} будет удалена")
        return True
    
    try:
        # Context7: DELETE /collections/{name} через Qdrant API
        response = requests.delete(f"{QDRANT_URL}/collections/{collection_name}")
        response.raise_for_status()
        print(f"✅ Коллекция {collection_name} удалена")
        return True
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            print(f"ℹ️  Коллекция {collection_name} уже не существует")
            return True
        print(f"❌ Ошибка удаления коллекции {collection_name}: {e}")
        return False
    except Exception as e:
        print(f"❌ Ошибка удаления коллекции {collection_name}: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="Полная очистка всех Qdrant коллекций")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Режим проверки без реального удаления"
    )
    args = parser.parse_args()
    
    print("=" * 60)
    print("ОЧИСТКА QDRANT КОЛЛЕКЦИЙ")
    print("=" * 60)
    print(f"Qdrant URL: {QDRANT_URL}")
    print(f"Режим: {'DRY-RUN (проверка без удаления)' if args.dry_run else 'РЕАЛЬНОЕ УДАЛЕНИЕ'}")
    print("=" * 60)
    
    if not args.dry_run:
        response = input("\n⚠️  ВНИМАНИЕ: Будут удалены ВСЕ коллекции! Продолжить? (yes/no): ")
        if response.lower() != "yes":
            print("Отменено пользователем")
            sys.exit(0)
    
    # Получаем список коллекций
    print("\n📊 Получение списка коллекций...")
    collections_data = get_collections()
    if not collections_data:
        print("❌ Не удалось получить список коллекций")
        sys.exit(1)
    
    collections = collections_data.get('result', {}).get('collections', [])
    
    if not collections:
        print("ℹ️  Коллекции не найдены")
        return
    
    print(f"\n📦 Найдено коллекций: {len(collections)}")
    
    total_points = 0
    deleted_count = 0
    
    # Удаляем каждую коллекцию
    for collection in collections:
        collection_name = collection['name']
        points_count = collection.get('points_count', 0)
        total_points += points_count
        
        print(f"\n📊 Коллекция: {collection_name}")
        print(f"   Точек: {points_count}")
        
        if delete_collection(collection_name, args.dry_run):
            deleted_count += 1
    
    print("\n" + "=" * 60)
    print("ИТОГОВАЯ СТАТИСТИКА")
    print("=" * 60)
    print(f"Всего коллекций: {len(collections)}")
    print(f"Всего точек: {total_points}")
    print(f"{'Будет удалено' if args.dry_run else 'Удалено'} коллекций: {deleted_count}")
    print("=" * 60)
    
    if args.dry_run:
        print("\n✅ DRY-RUN завершён успешно. Для реального удаления запустите без --dry-run")
    else:
        print("\n✅ Очистка Qdrant завершена успешно!")

if __name__ == "__main__":
    main()
