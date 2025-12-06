#!/usr/bin/env python3
"""
Тест извлечения entities для конкретного поста.
Context7: Проверка работы нового промпта на реальном посте.
"""

import asyncio
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api', 'worker'))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from services.ocr_enhancement_service import OCREnhancementService
import redis.asyncio as redis

async def test_post_entities(post_id: str):
    """Тест извлечения entities для конкретного поста."""
    
    print("="*70)
    print(f"🧪 Тест извлечения entities для поста: {post_id}")
    print("="*70)
    
    # Инициализация
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:54322/postgres")
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    try:
        # 1. Получить OCR данные поста
        print("\n📊 Шаг 1: Получение OCR данных поста...")
        async with async_session() as session:
            result = await session.execute(text("""
                SELECT 
                    post_id,
                    data->'ocr'->>'text' as ocr_text,
                    data->'ocr'->'entities' as entities_existing,
                    jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) as entities_count,
                    data->'ocr'->>'engine' as ocr_engine,
                    data->'ocr'->>'text_enhanced' as text_enhanced
                FROM post_enrichment
                WHERE post_id = :post_id AND kind = 'vision'
            """), {"post_id": post_id})
            row = result.fetchone()
            
            if not row:
                print(f"❌ Пост {post_id} не найден в post_enrichment")
                return
            
            ocr_text = row[1]
            entities_existing = row[2]
            entities_count = row[3]
            ocr_engine = row[4]
            text_enhanced = row[5]
            
            print(f"✅ Пост найден")
            print(f"   OCR engine: {ocr_engine}")
            print(f"   OCR текст (первые 200 символов): {ocr_text[:200] if ocr_text else 'N/A'}...")
            print(f"   Длина OCR текста: {len(ocr_text) if ocr_text else 0} символов")
            print(f"   Enhanced текст: {'Есть' if text_enhanced else 'Нет'}")
            print(f"   Существующие entities в БД: {entities_count}")
        
        if not ocr_text:
            print("❌ OCR текст отсутствует")
            return
        
        # 2. Инициализация OCR Enhancement Service
        print("\n📊 Шаг 2: Инициализация OCR Enhancement Service...")
        ocr_enhancement = OCREnhancementService(
            redis_client=redis_client,
            enabled=True,
            entity_extraction_enabled=True,
            llm_fallback_enabled=True
        )
        
        print(f"   enabled: {ocr_enhancement.enabled}")
        print(f"   entity_extraction_enabled: {ocr_enhancement.entity_extraction_enabled}")
        print(f"   LLM инициализирован: {ocr_enhancement.llm is not None}")
        
        if not ocr_enhancement.llm:
            print("   ❌ LLM не инициализирован - entity extraction невозможен")
            return
        
        # 3. Тест извлечения entities
        print("\n📊 Шаг 3: Извлечение entities с новым промптом...")
        try:
            # Используем enhanced текст если есть, иначе оригинальный
            test_text = text_enhanced or ocr_text
            print(f"   Используемый текст: {'Enhanced' if text_enhanced else 'Оригинальный'}")
            print(f"   Длина текста: {len(test_text)} символов")
            print(f"   Первые 300 символов: {test_text[:300]}...")
            
            entities = await ocr_enhancement.extract_entities(test_text)
            
            print(f"\n   ✅ Извлечено entities: {len(entities)}")
            if entities:
                print("\n   Извлеченные entities:")
                for i, entity in enumerate(entities[:15], 1):  # Показываем первые 15
                    print(f"   {i}. '{entity.get('text', 'N/A')}' ({entity.get('type', 'N/A')}) [confidence: {entity.get('confidence', 0)}]")
            else:
                print("   ⚠️  Entities не извлечены")
                
        except Exception as e:
            print(f"   ❌ Ошибка при извлечении entities: {e}")
            import traceback
            traceback.print_exc()
        
        # 4. Сравнение с существующими entities
        print("\n📊 Шаг 4: Сравнение результатов...")
        print(f"   Entities в БД: {entities_count}")
        print(f"   Entities извлечено: {len(entities) if 'entities' in locals() else 0}")
        
        if entities_count == 0 and len(entities) > 0:
            print(f"   ✅ Новый промпт нашел entities (в БД был 0)")
        elif entities_count == 0 and len(entities) == 0:
            print(f"   ⚠️  Entities не найдены ни в БД, ни новым промптом")
        else:
            print(f"   📊 Сравнение требует детального анализа")
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await engine.dispose()
        await redis_client.aclose()
    
    print("\n" + "="*70)
    print("✅ Тестирование завершено")
    print("="*70)


if __name__ == "__main__":
    post_id = sys.argv[1] if len(sys.argv) > 1 else "509d15bc-9404-473e-a906-8f57eca48a05"
    asyncio.run(test_post_entities(post_id))

