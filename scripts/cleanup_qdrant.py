#!/usr/bin/env python3
"""
Скрипт для очистки Qdrant коллекций
"""

import requests
import json
import sys

QDRANT_URL = "http://localhost:6333"

def get_collections():
    """Получить список коллекций"""
    try:
        response = requests.get(f"{QDRANT_URL}/collections")
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Ошибка получения коллекций: {e}")
        return None

def delete_collection(collection_name):
    """Удалить коллекцию"""
    try:
        response = requests.delete(f"{QDRANT_URL}/collections/{collection_name}")
        response.raise_for_status()
        print(f"Коллекция {collection_name} удалена")
        return True
    except Exception as e:
        print(f"Ошибка удаления коллекции {collection_name}: {e}")
        return False

def main():
    print("Очистка Qdrant коллекций...")
    
    # Получаем список коллекций
    collections_data = get_collections()
    if not collections_data:
        print("Не удалось получить список коллекций")
        sys.exit(1)
    
    collections = collections_data.get('result', {}).get('collections', [])
    
    if not collections:
        print("Коллекции не найдены")
        return
    
    print(f"Найдено коллекций: {len(collections)}")
    
    # Удаляем каждую коллекцию
    for collection in collections:
        collection_name = collection['name']
        points_count = collection.get('points_count', 0)
        print(f"Коллекция: {collection_name}, точек: {points_count}")
        
        if points_count > 0:
            delete_collection(collection_name)
        else:
            print(f"Коллекция {collection_name} уже пуста")
    
    print("Очистка Qdrant завершена")

if __name__ == "__main__":
    main()
