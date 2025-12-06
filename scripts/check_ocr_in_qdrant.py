#!/usr/bin/env python3
"""
Проверка записи OCR в Qdrant.
Context7: Проверка что OCR текст используется для эмбеддингов и сохранен в payload.
"""

import asyncio
import os
import sys
import json
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api', 'worker'))

async def check_ocr_in_qdrant(post_id: str = None):
    """Проверка записи OCR в Qdrant."""
    
    print("="*70)
    print("🔍 Проверка записи OCR в Qdrant")
    print("="*70)
    
    # Инициализация Qdrant клиента
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    qdrant_client = QdrantClient(url=qdrant_url)
    
    # Получаем список коллекций
    print("\n📊 Шаг 1: Получение списка коллекций...")
    collections = qdrant_client.get_collections()
    collection_names = [c.name for c in collections.collections]
    print(f"   Найдено коллекций: {len(collection_names)}")
    for name in collection_names[:10]:
        print(f"   - {name}")
    
    # Ищем пост с OCR
    if not post_id:
        print("\n📊 Шаг 2: Поиск поста с OCR...")
        import asyncpg
        db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:54322/postgres")
        # Упрощенный парсинг URL
        if "postgresql://" in db_url:
            db_url = db_url.replace("postgresql://", "")
            parts = db_url.split("@")
            if len(parts) == 2:
                auth, rest = parts
                user, password = auth.split(":")
                host_port_db = rest.split("/")
                if len(host_port_db) == 2:
                    host_port = host_port_db[0].split(":")
                    host = host_port[0]
                    port = int(host_port[1]) if len(host_port) > 1 else 5432
                    database = host_port_db[1]
                    
                    conn = await asyncpg.connect(
                        user=user,
                        password=password,
                        host=host,
                        port=port,
                        database=database
                    )
                    
                    row = await conn.fetchrow("""
                        SELECT post_id, data->'ocr'->>'text' as ocr_text
                        FROM post_enrichment
                        WHERE kind = 'vision'
                          AND data->'ocr'->>'text' IS NOT NULL
                          AND data->'ocr'->>'text' != ''
                        LIMIT 1
                    """)
                    
                    if row:
                        post_id = row['post_id']
                        print(f"   ✅ Найден пост: {post_id}")
                        print(f"   OCR текст (первые 100 символов): {row['ocr_text'][:100]}...")
                    else:
                        print("   ⚠️  Посты с OCR не найдены")
                        return
                    
                    await conn.close()
        else:
            print("   ❌ Не удалось подключиться к БД")
            return
    
    # Ищем пост во всех коллекциях
    print(f"\n📊 Шаг 3: Поиск поста {post_id} в Qdrant...")
    found = False
    
    for collection_name in collection_names:
        try:
            # Проверяем наличие поста в коллекции
            scroll_result = qdrant_client.scroll(
                collection_name=collection_name,
                scroll_filter=Filter(
                    must=[
                        FieldCondition(
                            key="post_id",
                            match=MatchValue(value=post_id)
                        )
                    ]
                ),
                limit=1,
                with_payload=True,
                with_vectors=False
            )
            
            if scroll_result[0]:  # Есть результаты
                found = True
                point = scroll_result[0][0]
                payload = point.payload
                
                print(f"\n   ✅ Пост найден в коллекции: {collection_name}")
                print(f"\n   📋 Payload структура:")
                print(f"      - post_id: {payload.get('post_id')}")
                print(f"      - tenant_id: {payload.get('tenant_id')}")
                print(f"      - channel_id: {payload.get('channel_id')}")
                print(f"      - text_short: {payload.get('text_short', '')[:100]}...")
                
                # Проверка Vision данных
                vision = payload.get('vision', {})
                if vision:
                    print(f"\n   🖼️  Vision данные в payload:")
                    print(f"      - is_meme: {vision.get('is_meme')}")
                    print(f"      - labels: {vision.get('labels', [])[:5]}")
                    print(f"      - scene: {vision.get('scene')}")
                    print(f"      - classification: {vision.get('classification')}")
                    
                    # Проверяем наличие OCR текста в payload
                    ocr_in_payload = False
                    for key in payload.keys():
                        if 'ocr' in key.lower():
                            ocr_in_payload = True
                            print(f"      ⚠️  OCR найден в payload (ключ: {key})")
                    
                    if not ocr_in_payload:
                        print(f"      ℹ️  OCR текст НЕ в payload (используется только для эмбеддинга)")
                
                # Получаем OCR из БД для сравнения
                print(f"\n   📝 OCR текст из БД:")
                import asyncpg
                db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:54322/postgres")
                if "postgresql://" in db_url:
                    db_url_clean = db_url.replace("postgresql://", "")
                    parts = db_url_clean.split("@")
                    if len(parts) == 2:
                        auth, rest = parts
                        user, password = auth.split(":")
                        host_port_db = rest.split("/")
                        if len(host_port_db) == 2:
                            host_port = host_port_db[0].split(":")
                            host = host_port[0]
                            port = int(host_port[1]) if len(host_port) > 1 else 5432
                            database = host_port_db[1]
                            
                            conn = await asyncpg.connect(
                                user=user,
                                password=password,
                                host=host,
                                port=port,
                                database=database
                            )
                            
                            ocr_row = await conn.fetchrow("""
                                SELECT 
                                    data->'ocr'->>'text' as ocr_text,
                                    data->'ocr'->>'text_enhanced' as ocr_text_enhanced,
                                    jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) as entities_count
                                FROM post_enrichment
                                WHERE post_id = $1 AND kind = 'vision'
                            """, post_id)
                            
                            if ocr_row:
                                print(f"      - OCR текст: {ocr_row['ocr_text'][:200] if ocr_row['ocr_text'] else 'N/A'}...")
                                print(f"      - Enhanced текст: {'Есть' if ocr_row['ocr_text_enhanced'] else 'Нет'}")
                                print(f"      - Entities: {ocr_row['entities_count']}")
                            
                            await conn.close()
                
                break
                
        except Exception as e:
            # Коллекция может не содержать пост или быть недоступна
            continue
    
    if not found:
        print(f"   ⚠️  Пост {post_id} не найден в Qdrant")
        print("   Возможные причины:")
        print("      - Пост еще не проиндексирован")
        print("      - Пост в другой коллекции")
    
    print("\n" + "="*70)
    print("✅ Проверка завершена")
    print("="*70)


if __name__ == "__main__":
    post_id = sys.argv[1] if len(sys.argv) > 1 else None
    asyncio.run(check_ocr_in_qdrant(post_id))

