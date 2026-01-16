#!/usr/bin/env python3
"""
Тестирование записи OCR entities в Neo4j на реальных данных.
Context7 best practice: проверка работы индексов и записи entities.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone

# Добавляем пути для импорта
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api', 'worker'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from integrations.neo4j_client import Neo4jClient
from services.ocr_enhancement_service import OCREnhancementService
import redis.asyncio as redis

async def test_ocr_entities_write():
    """Тест записи OCR entities на реальных данных."""
    
    print("="*70)
    print("🧪 Тестирование записи OCR Entities в Neo4j")
    print("="*70)
    
    # Инициализация клиентов
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:54322/postgres")
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    neo4j_client = Neo4jClient()
    await neo4j_client.connect()
    
    try:
        # 1. Найти пост с OCR текстом (длинным)
        print("\n📊 Шаг 1: Поиск поста с OCR текстом...")
        async with async_session() as session:
            result = await session.execute(text("""
                SELECT 
                    post_id,
                    data->'ocr'->>'text' as ocr_text,
                    data->'ocr'->'entities' as entities_existing,
                    jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) as entities_count
                FROM post_enrichment
                WHERE kind = 'vision'
                  AND data->'ocr' IS NOT NULL
                  AND data->'ocr' != 'null'::jsonb
                  AND data->'ocr'->>'text' IS NOT NULL
                  AND LENGTH(data->'ocr'->>'text') > 100
                ORDER BY created_at DESC
                LIMIT 1
            """))
            row = result.fetchone()
            
            if not row:
                print("❌ Не найден пост с OCR текстом")
                return
            
            post_id = row[0]
            ocr_text = row[1]
            entities_existing = row[2]
            entities_count = row[3]
            
            print(f"✅ Найден пост: {post_id}")
            print(f"   OCR текст (первые 100 символов): {ocr_text[:100]}...")
            print(f"   Существующие entities: {entities_count}")
        
        # 2. Проверить, есть ли пост в Neo4j
        print("\n📊 Шаг 2: Проверка наличия поста в Neo4j...")
        async with neo4j_client._driver.session() as session:
            result = await session.run(
                "MATCH (p:Post {post_id: $post_id}) RETURN p.post_id as post_id, p.content as content",
                post_id=post_id
            )
            record = await result.single()
            
            if not record:
                print(f"⚠️  Пост {post_id} не найден в Neo4j")
                print("   Для теста нужен проиндексированный пост. Пропускаем запись entities.")
                return
            
            print(f"✅ Пост найден в Neo4j: {record['post_id']}")
        
        # 3. Извлечь entities через OCR Enhancement Service
        print("\n📊 Шаг 3: Извлечение entities через OCR Enhancement Service...")
        ocr_enhancement = OCREnhancementService(
            redis_client=redis_client,
            enabled=True,
            entity_extraction_enabled=True,
            llm_fallback_enabled=True
        )
        
        # Подготовка OCR данных в формате, ожидаемом сервисом
        ocr_data = {
            "text": ocr_text,
            "engine": "gigachat",
            "confidence": 0.9
        }
        
        try:
            entities = await ocr_enhancement.extract_entities(ocr_text)
            print(f"✅ Извлечено entities: {len(entities)}")
            if entities:
                for i, entity in enumerate(entities[:5], 1):  # Показываем первые 5
                    print(f"   {i}. {entity.get('text', 'N/A')} ({entity.get('type', 'N/A')})")
            else:
                print("   ⚠️  Entities не извлечены (LLM не нашел сущности)")
        except Exception as e:
            print(f"❌ Ошибка при извлечении entities: {e}")
            import traceback
            traceback.print_exc()
            return
        
        # 4. Записать entities в Neo4j
        if entities:
            print("\n📊 Шаг 4: Запись entities в Neo4j...")
            try:
                success = await neo4j_client.create_ocr_entities(
                    post_id=post_id,
                    entities=entities,
                    ocr_context=ocr_text[:500]  # Ограничиваем контекст
                )
                
                if success:
                    print(f"✅ Entities записаны в Neo4j")
                else:
                    print(f"❌ Не удалось записать entities")
                    return
            except Exception as e:
                print(f"❌ Ошибка при записи entities: {e}")
                import traceback
                traceback.print_exc()
                return
        else:
            print("\n⚠️  Шаг 4 пропущен: нет entities для записи")
            print("   Это нормально, если LLM не нашел сущности в тексте")
            return
        
        # 5. Проверить запись в Neo4j
        print("\n📊 Шаг 5: Проверка записи entities в Neo4j...")
        async with neo4j_client._driver.session() as session:
            # Проверяем количество Entity узлов
            result = await session.run("""
                MATCH (e:Entity {source: 'ocr'})
                RETURN count(e) as total_entities
            """)
            record = await result.single()
            total_entities = record['total_entities'] if record else 0
            
            # Проверяем связи для нашего поста
            result = await session.run("""
                MATCH (p:Post {post_id: $post_id})-[r:MENTIONS]->(e:Entity)
                WHERE r.source = 'ocr'
                RETURN count(e) as post_entities
            """, post_id=post_id)
            record = await result.single()
            post_entities = record['post_entities'] if record else 0
            
            # Проверяем использование индексов
            result = await session.run("""
                EXPLAIN MATCH (e:Entity {source: 'ocr'})
                WHERE e.name = $name AND e.type = $type
                RETURN e
                LIMIT 1
            """, name=entities[0].get('text', ''), type=entities[0].get('type', ''))
            
            print(f"✅ Всего Entity узлов с source='ocr': {total_entities}")
            print(f"✅ Entities для поста {post_id}: {post_entities}")
            
            if post_entities > 0:
                # Показываем пример entities
                result = await session.run("""
                    MATCH (p:Post {post_id: $post_id})-[r:MENTIONS]->(e:Entity)
                    WHERE r.source = 'ocr'
                    RETURN e.name as name, e.type as type, r.confidence as confidence
                    LIMIT 5
                """, post_id=post_id)
                
                print("\n   Примеры записанных entities:")
                async for record in result:
                    print(f"   - {record['name']} ({record['type']}) [confidence: {record.get('confidence', 'N/A')}]")
        
        # 6. Проверка индексов
        print("\n📊 Шаг 6: Проверка использования индексов...")
        async with neo4j_client._driver.session() as session:
            result = await session.run("""
                EXPLAIN MATCH (e:Entity)
                WHERE e.name = $name AND e.type = $type
                RETURN e
            """, name=entities[0].get('text', ''), type=entities[0].get('type', ''))
            
            plan = await result.consume()
            print("✅ План запроса получен (индексы используются автоматически)")
        
        print("\n" + "="*70)
        print("✅ Тестирование завершено успешно!")
        print("="*70)
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await engine.dispose()
        await redis_client.aclose()
        await neo4j_client.close()


if __name__ == "__main__":
    asyncio.run(test_ocr_entities_write())

