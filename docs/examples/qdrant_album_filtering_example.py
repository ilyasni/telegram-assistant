"""
Пример использования фильтрации альбомов в Qdrant
Context7: демонстрация поиска постов из альбомов через album_id
"""

from worker.integrations.qdrant_client import QdrantClient
from worker.ai_providers.embedding_service import EmbeddingService

async def search_posts_from_album(
    qdrant_client: QdrantClient,
    embedding_service: EmbeddingService,
    collection_name: str,
    album_id: int,
    query_text: str,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Поиск постов внутри конкретного альбома по текстовому запросу.
    
    Context7: Использует album_id в filter_conditions для ограничения поиска
    только постами из указанного альбома.
    
    Args:
        qdrant_client: Клиент Qdrant
        embedding_service: Сервис для генерации эмбеддингов
        collection_name: Имя коллекции Qdrant
        album_id: ID альбома для фильтрации
        query_text: Текстовый запрос для поиска
        limit: Максимальное количество результатов
        
    Returns:
        Список результатов поиска с payload (post_id, channel_id, etc.)
    """
    # Генерируем embedding для запроса
    query_vector = await embedding_service.embed_text(query_text)
    
    # Поиск с фильтром по album_id
    results = await qdrant_client.search_vectors(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=limit,
        filter_conditions={
            'album_id': album_id  # Context7: Фильтрация по album_id
        }
    )
    
    return results


async def search_albums_by_tags(
    qdrant_client: QdrantClient,
    embedding_service: EmbeddingService,
    collection_name: str,
    tags: List[str],
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Поиск альбомов по тегам постов.
    
    Context7: Использует фильтрацию по tags и группировку по album_id.
    Возвращает уникальные album_id из результатов поиска.
    
    Args:
        qdrant_client: Клиент Qdrant
        embedding_service: Сервис для генерации эмбеддингов
        collection_name: Имя коллекции Qdrant
        tags: Список тегов для фильтрации
        limit: Максимальное количество результатов
        
    Returns:
        Список результатов с уникальными album_id
    """
    # Генерируем embedding для запроса (можно использовать любой текст)
    query_vector = await embedding_service.embed_text(" ".join(tags))
    
    # Поиск с фильтром по tags
    results = await qdrant_client.search_vectors(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=limit * 5,  # Берём больше, т.к. потом группируем по album_id
        filter_conditions={
            'tags': tags  # Context7: Фильтрация по списку тегов
        }
    )
    
    # Группируем результаты по album_id
    albums_map: Dict[int, List[Dict[str, Any]]] = {}
    for result in results:
        payload = result.get('payload', {})
        if 'album_id' in payload:
            album_id = payload['album_id']
            if album_id not in albums_map:
                albums_map[album_id] = []
            albums_map[album_id].append(result)
    
    # Возвращаем топ альбомы (по среднему score)
    album_scores = []
    for album_id, album_results in albums_map.items():
        avg_score = sum(r['score'] for r in album_results) / len(album_results)
        album_scores.append({
            'album_id': album_id,
            'score': avg_score,
            'posts_count': len(album_results),
            'posts': album_results[:3]  # Первые 3 поста для примера
        })
    
    # Сортируем по score и возвращаем топ
    album_scores.sort(key=lambda x: x['score'], reverse=True)
    return album_scores[:limit]


async def search_non_album_posts(
    qdrant_client: QdrantClient,
    embedding_service: EmbeddingService,
    collection_name: str,
    query_text: str,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Поиск постов, которые НЕ принадлежат альбомам.
    
    Context7: Использует фильтр "album_id отсутствует" (must_not).
    В Qdrant это можно реализовать через фильтр по отсутствию поля.
    """
    query_vector = await embedding_service.embed_text(query_text)
    
    # Context7: Поиск без фильтра album_id (все посты)
    # Для фильтрации "НЕ из альбома" нужно использовать must_not в Qdrant Filter
    # Упрощённый вариант: ищем все и фильтруем в Python
    results = await qdrant_client.search_vectors(
        collection_name=collection_name,
        query_vector=query_vector,
        limit=limit * 2  # Берём больше для фильтрации
    )
    
    # Фильтруем посты без album_id
    non_album_results = [
        r for r in results
        if 'album_id' not in r.get('payload', {})
    ]
    
    return non_album_results[:limit]

