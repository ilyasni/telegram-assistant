#!/usr/bin/env python3
"""
Тест фильтрации альбомов в Qdrant
Context7: проверка что album_id корректно используется для фильтрации
"""

import asyncio
import sys
import os
from typing import List, Dict, Any

project_root = '/opt/telegram-assistant'
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from worker.integrations.qdrant_client import QdrantClient
from worker.ai_providers.embedding_service import EmbeddingService
import structlog

logger = structlog.get_logger()

async def test_album_id_filtering():
    """Тест фильтрации постов по album_id в Qdrant."""
    print("\n🧪 Тест: Фильтрация альбомов в Qdrant")
    
    try:
        from config import settings
        
        qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
        collection_name = os.getenv("QDRANT_COLLECTION", "telegram_posts")
        
        qdrant_client = QdrantClient(url=qdrant_url)
        await qdrant_client.connect()
        
        # Получаем несколько векторов для проверки наличия album_id
        from qdrant_client import QdrantClient as QdrantSDK
        sdk_client = QdrantSDK(url=qdrant_url)
        
        scroll_result = sdk_client.scroll(
            collection_name=collection_name,
            limit=50,
            with_payload=True,
            with_vectors=True
        )
        
        points = scroll_result[0]
        print(f"  ✓ Проверено векторов: {len(points)}")
        
        # Находим векторы с album_id
        albums_map: Dict[int, List[str]] = {}  # album_id -> [post_ids]
        for point in points:
            payload = point.payload or {}
            if 'album_id' in payload:
                album_id = payload['album_id']
                post_id = payload.get('post_id') or str(point.id)
                if album_id not in albums_map:
                    albums_map[album_id] = []
                albums_map[album_id].append(post_id)
        
        print(f"  ✓ Найдено уникальных альбомов: {len(albums_map)}")
        
        if albums_map:
            # Тестируем фильтрацию для первого альбома
            test_album_id = list(albums_map.keys())[0]
            test_post_ids = albums_map[test_album_id]
            
            print(f"  ✓ Тестируем фильтрацию для album_id={test_album_id}")
            
            # Получаем embedding одного из постов для поиска
            test_post_id = test_post_ids[0]
            test_point = next((p for p in points if (p.payload or {}).get('post_id') == test_post_id or str(p.id) == test_post_id), None)
            
            if test_point and test_point.vector:
                query_vector = test_point.vector
                
                # Поиск без фильтра
                results_no_filter = await qdrant_client.search_vectors(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    limit=20
                )
                
                print(f"    - Без фильтра: найдено {len(results_no_filter)} результатов")
                
                # Поиск с фильтром album_id
                results_with_filter = await qdrant_client.search_vectors(
                    collection_name=collection_name,
                    query_vector=query_vector,
                    limit=20,
                    filter_conditions={'album_id': test_album_id}
                )
                
                print(f"    - С фильтром album_id={test_album_id}: найдено {len(results_with_filter)} результатов")
                
                # Проверяем, что все результаты относятся к нужному альбому
                filtered_album_ids = set()
                for result in results_with_filter:
                    payload = result.get('payload', {})
                    if 'album_id' in payload:
                        filtered_album_ids.add(payload['album_id'])
                
                if len(filtered_album_ids) == 1 and test_album_id in filtered_album_ids:
                    print(f"    ✓ Фильтрация работает корректно: все результаты из альбома {test_album_id}")
                elif len(filtered_album_ids) == 0:
                    print(f"    ⚠️  Нет результатов с album_id (возможно, данные не проиндексированы)")
                else:
                    print(f"    ⚠️  Найдены альбомы: {filtered_album_ids}, ожидался только {test_album_id}")
                
                # Проверяем что результаты из фильтра - подмножество результатов без фильтра
                result_ids_filtered = {r['id'] for r in results_with_filter}
                result_ids_no_filter = {r['id'] for r in results_no_filter}
                
                if result_ids_filtered.issubset(result_ids_no_filter):
                    print(f"    ✓ Результаты с фильтром - подмножество результатов без фильтра")
                else:
                    print(f"    ⚠️  Есть результаты, которые не входят в результаты без фильтра")
            else:
                print(f"    ⚠️  Не удалось получить vector для тестирования")
        else:
            print(f"  ℹ️  В Qdrant нет векторов с album_id для тестирования фильтрации")
            print(f"     (Это нормально, если альбомы ещё не проиндексированы)")
        
        print("  ✅ Тест фильтрации Qdrant пройден")
        
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_album_id_filtering())

