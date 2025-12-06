#!/usr/bin/env python3
"""
Диагностика извлечения OCR entities.
Context7: Проверка работы OCR Enhancement Service и извлечения entities.
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api', 'worker'))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
from services.ocr_enhancement_service import OCREnhancementService
import redis.asyncio as redis
import json

async def diagnose_ocr_entities():
    """Диагностика извлечения OCR entities."""
    
    print("="*70)
    print("🔍 Диагностика извлечения OCR Entities")
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
        # 1. Найти пост с OCR текстом
        print("\n📊 Шаг 1: Поиск поста с OCR текстом...")
        async with async_session() as session:
            result = await session.execute(text("""
                SELECT 
                    post_id,
                    data->'ocr'->>'text' as ocr_text,
                    data->'ocr'->'entities' as entities_existing,
                    jsonb_array_length(COALESCE(data->'ocr'->'entities', '[]'::jsonb)) as entities_count,
                    data->'ocr'->>'engine' as ocr_engine
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
            ocr_engine = row[4]
            
            print(f"✅ Найден пост: {post_id}")
            print(f"   OCR engine: {ocr_engine}")
            print(f"   OCR текст (первые 150 символов): {ocr_text[:150]}...")
            print(f"   Длина OCR текста: {len(ocr_text)} символов")
            print(f"   Существующие entities в БД: {entities_count}")
            if entities_existing:
                print(f"   Entities данные: {json.dumps(entities_existing, ensure_ascii=False)[:200]}...")
        
        # 2. Проверить конфигурацию OCR Enhancement Service
        print("\n📊 Шаг 2: Проверка конфигурации OCR Enhancement Service...")
        ocr_enhancement = OCREnhancementService(
            redis_client=redis_client,
            enabled=True,
            entity_extraction_enabled=True,
            llm_fallback_enabled=True
        )
        
        print(f"   enabled: {ocr_enhancement.enabled}")
        print(f"   entity_extraction_enabled: {ocr_enhancement.entity_extraction_enabled}")
        print(f"   llm_fallback_enabled: {ocr_enhancement.llm_fallback_enabled}")
        print(f"   LLM инициализирован: {ocr_enhancement.llm is not None}")
        
        if not ocr_enhancement.llm:
            print("   ⚠️  LLM не инициализирован - entity extraction не будет работать")
            return
        
        # 3. Попытка извлечения entities
        print("\n📊 Шаг 3: Попытка извлечения entities...")
        try:
            # Берем первые 500 символов для теста
            test_text = ocr_text[:500]
            print(f"   Тестовый текст (первые 500 символов): {test_text[:100]}...")
            
            entities = await ocr_enhancement.extract_entities(test_text)
            
            print(f"   ✅ Извлечено entities: {len(entities)}")
            if entities:
                print("\n   Извлеченные entities:")
                for i, entity in enumerate(entities[:10], 1):  # Первые 10
                    print(f"   {i}. '{entity.get('text', 'N/A')}' ({entity.get('type', 'N/A')}) [confidence: {entity.get('confidence', 0)}]")
            else:
                print("   ⚠️  Entities не извлечены")
                print("   Возможные причины:")
                print("      - LLM не нашел сущности в тексте")
                print("      - Ошибка парсинга JSON ответа от LLM")
                print("      - LLM недоступен")
                
        except Exception as e:
            print(f"   ❌ Ошибка при извлечении entities: {e}")
            import traceback
            traceback.print_exc()
        
        # 4. Проверить кэш
        print("\n📊 Шаг 4: Проверка кэша entities...")
        try:
            cache_key = await ocr_enhancement._get_cache_key(test_text[:500], "entities", "ru", "default")
            cached = await ocr_enhancement._get_from_cache(cache_key)
            if cached:
                print(f"   ✅ Найдено в кэше: {len(cached)} entities")
            else:
                print("   ⏭️  Нет в кэше")
        except Exception as e:
            print(f"   ⚠️  Ошибка проверки кэша: {e}")
        
        # 5. Рекомендации
        print("\n" + "="*70)
        print("📋 Рекомендации:")
        print("="*70)
        
        if not entities or len(entities) == 0:
            print("""
1. Проверить доступность LLM (GigaChat):
   - Проверить переменные окружения GIGACHAT_*
   - Проверить логи на ошибки подключения

2. Проверить формат ответа LLM:
   - LLM должен возвращать валидный JSON
   - Формат: [{"text": "...", "type": "ORG|PERSON|LOC|PRODUCT", "confidence": 0.0-1.0}]

3. Проверить промпт для entity extraction:
   - Убедиться, что промпт корректно сформирован
   - Проверить логи на ошибки парсинга JSON

4. Включить детальное логирование:
   - Проверить логи worker на "Entity extraction failed"
   - Проверить логи на "Failed to parse entity extraction JSON"
""")
        else:
            print(f"""
✅ Entities извлекаются успешно ({len(entities)} найдено)

Проблема в том, что entities не записываются в БД при сохранении vision enrichment.
Проверить:
1. Вызывается ли enhance_ocr_data() при сохранении vision данных
2. Добавляются ли entities в vision_data['ocr']['entities']
3. Вызывается ли create_ocr_entities() в indexing_task
""")
        
        print("="*70)
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await engine.dispose()
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(diagnose_ocr_entities())

