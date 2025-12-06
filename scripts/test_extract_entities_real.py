#!/usr/bin/env python3
"""
Тест извлечения entities из реального OCR текста.
Context7: Проверка работы extract_entities() на реальных данных.
"""

import asyncio
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api', 'worker'))

from services.ocr_enhancement_service import OCREnhancementService
import redis.asyncio as redis

# Реальный OCR текст из БД
REAL_OCR_TEXT = """Де
Bевер
мобилизует
бельгийскую
поддержку
ЕC
против
редложения
O
заморозке
активов
ремьер
министр
ельгии
заявил
Oћ
о
страны
нельзя
требовать
HEBOBMOЖHOIO
Крис
ауэрс
I
Еврактив
oo.
оо?
иколяа
Skohgmurefioto.
череэ
Gerty irmages
ремер
министр
ельгии
арт
де
Bевер
оыл
встречен
бурными
аплодисментами
бельгийских
депутатов
когда
н
отверг
предположения
Tom.
Oћ
н
меHее
проевропейски
или
проукраински
настроен
изза
выражения
обеспокоенности
поводу
плана
Европейской
комйссий
испOлызOват
замороженные
российские
аKTИBы
для
обеспечения
репарационного
кредита
Киeвy."""

# Enhanced текст (после spell correction)
ENHANCED_TEXT = """Де Север мобилизует бельгийскую поддержку Ее против предложения O заморозке активов премьер министр ельгии завел Of о страны нельзя требовать HEBOBMOЖHOIO Крис курс I Еврактив oo. оо? школа Skohgmurefioto. через Gerry images номер министр ельгии арт де Север был встрече бурными аплодисментами бельгийских депутат когда i ответ предположения Tom. Of i менее проевропейски или проукраински настроение иззи выражение обеспокоенности поводу плана Европейский комиссия испOлызOват замороженные российские аKTИBы для обеспечение репарационного кредит Киeвy."""

async def test_extract_entities():
    """Тест извлечения entities из реального OCR текста."""
    
    print("="*70)
    print("🧪 Тест извлечения entities из реального OCR текста")
    print("="*70)
    
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
    redis_client = redis.from_url(redis_url, decode_responses=True)
    
    # Инициализация OCR Enhancement Service
    ocr_enhancement = OCREnhancementService(
        redis_client=redis_client,
        enabled=True,
        entity_extraction_enabled=True,
        llm_fallback_enabled=True
    )
    
    print(f"\n📊 Конфигурация:")
    print(f"   enabled: {ocr_enhancement.enabled}")
    print(f"   entity_extraction_enabled: {ocr_enhancement.entity_extraction_enabled}")
    print(f"   LLM инициализирован: {ocr_enhancement.llm is not None}")
    
    if not ocr_enhancement.llm:
        print("   ❌ LLM не инициализирован - entity extraction невозможен")
        await redis_client.aclose()
        return
    
    try:
        # Тест 1: Оригинальный OCR текст
        print("\n" + "="*70)
        print("📊 Тест 1: Извлечение из оригинального OCR текста")
        print("="*70)
        print(f"   Длина текста: {len(REAL_OCR_TEXT)} символов")
        print(f"   Первые 200 символов: {REAL_OCR_TEXT[:200]}...")
        
        entities_original = await ocr_enhancement.extract_entities(REAL_OCR_TEXT)
        print(f"\n   ✅ Извлечено entities: {len(entities_original)}")
        if entities_original:
            for i, entity in enumerate(entities_original[:10], 1):
                print(f"   {i}. '{entity.get('text')}' ({entity.get('type')}) [confidence: {entity.get('confidence')}]")
        else:
            print("   ⚠️  Entities не извлечены")
        
        # Тест 2: Enhanced текст (после spell correction)
        print("\n" + "="*70)
        print("📊 Тест 2: Извлечение из enhanced текста")
        print("="*70)
        print(f"   Длина текста: {len(ENHANCED_TEXT)} символов")
        print(f"   Первые 200 символов: {ENHANCED_TEXT[:200]}...")
        
        entities_enhanced = await ocr_enhancement.extract_entities(ENHANCED_TEXT)
        print(f"\n   ✅ Извлечено entities: {len(entities_enhanced)}")
        if entities_enhanced:
            for i, entity in enumerate(entities_enhanced[:10], 1):
                print(f"   {i}. '{entity.get('text')}' ({entity.get('type')}) [confidence: {entity.get('confidence')}]")
        else:
            print("   ⚠️  Entities не извлечены")
        
        # Тест 3: Нормализованный текст (убираем переносы строк)
        print("\n" + "="*70)
        print("📊 Тест 3: Извлечение из нормализованного текста")
        print("="*70)
        
        normalized_text = ENHANCED_TEXT.replace('\n', ' ').replace('  ', ' ').strip()
        print(f"   Длина текста: {len(normalized_text)} символов")
        print(f"   Первые 200 символов: {normalized_text[:200]}...")
        
        entities_normalized = await ocr_enhancement.extract_entities(normalized_text)
        print(f"\n   ✅ Извлечено entities: {len(entities_normalized)}")
        if entities_normalized:
            for i, entity in enumerate(entities_normalized[:10], 1):
                print(f"   {i}. '{entity.get('text')}' ({entity.get('type')}) [confidence: {entity.get('confidence')}]")
        else:
            print("   ⚠️  Entities не извлечены")
        
        # Анализ
        print("\n" + "="*70)
        print("📋 Анализ результатов")
        print("="*70)
        
        all_entities = {
            'original': entities_original,
            'enhanced': entities_enhanced,
            'normalized': entities_normalized
        }
        
        for test_name, entities in all_entities.items():
            print(f"\n   {test_name}: {len(entities)} entities")
            if entities:
                types = {}
                for entity in entities:
                    entity_type = entity.get('type', 'UNKNOWN')
                    types[entity_type] = types.get(entity_type, 0) + 1
                print(f"      Типы: {types}")
        
        # Ожидаемые entities в тексте
        print("\n📋 Ожидаемые entities в тексте:")
        print("   - 'Европейская комиссия' / 'комиссия' (ORG)")
        print("   - 'Киев' / 'Киeвy' (LOC)")
        print("   - 'де Бевер' / 'Bевер' (PERSON)")
        print("   - 'Бельгия' / 'бельгийскую' (LOC)")
        print("   - 'Россия' / 'российские' (LOC)")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await redis_client.aclose()
    
    print("\n" + "="*70)
    print("✅ Тестирование завершено")
    print("="*70)


if __name__ == "__main__":
    asyncio.run(test_extract_entities())

