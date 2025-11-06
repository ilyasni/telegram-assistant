#!/usr/bin/env python3
"""
Диагностический скрипт для проверки сохранения OCR текста.

Context7: Проверяет:
1. Когда было последнее сохранение OCR
2. Статистику по OCR в БД
3. Проблемы с парсингом/валидацией OCR
"""

import asyncio
import asyncpg
import os
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

# Подключение к БД
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/telegram_assistant"
)


async def check_ocr_statistics(pool: asyncpg.Pool) -> Dict[str, Any]:
    """Проверка статистики OCR в БД."""
    async with pool.acquire() as conn:
        # Общая статистика
        total_vision = await conn.fetchval("""
            SELECT COUNT(*) 
            FROM post_enrichment 
            WHERE kind = 'vision'
        """)
        
        # С OCR текстом (новый формат)
        with_ocr_new = await conn.fetchval("""
            SELECT COUNT(*) 
            FROM post_enrichment 
            WHERE kind = 'vision' 
              AND data->'ocr'->>'text' IS NOT NULL 
              AND LENGTH(data->'ocr'->>'text') > 0
        """)
        
        # С OCR в legacy формате
        with_ocr_legacy = await conn.fetchval("""
            SELECT COUNT(*) 
            FROM post_enrichment 
            WHERE kind = 'vision' 
              AND vision_ocr_text IS NOT NULL 
              AND LENGTH(vision_ocr_text) > 0
        """)
        
        # Последнее сохранение с OCR
        last_ocr = await conn.fetchrow("""
            SELECT 
                post_id,
                updated_at,
                provider,
                data->'ocr'->>'text' as ocr_text,
                LENGTH(data->'ocr'->>'text') as ocr_length,
                data->>'model' as model
            FROM post_enrichment 
            WHERE kind = 'vision' 
              AND data->'ocr'->>'text' IS NOT NULL 
              AND LENGTH(data->'ocr'->>'text') > 0
            ORDER BY updated_at DESC 
            LIMIT 1
        """)
        
        # Последнее сохранение вообще (vision)
        last_vision = await conn.fetchrow("""
            SELECT 
                post_id,
                updated_at,
                provider,
                data->'ocr'->>'text' as ocr_text,
                data->>'model' as model
            FROM post_enrichment 
            WHERE kind = 'vision'
            ORDER BY updated_at DESC 
            LIMIT 1
        """)
        
        # Статистика по провайдерам
        provider_stats = await conn.fetch("""
            SELECT 
                provider,
                COUNT(*) as total,
                COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL AND LENGTH(data->'ocr'->>'text') > 0 THEN 1 END) as with_ocr,
                MAX(updated_at) as last_update
            FROM post_enrichment 
            WHERE kind = 'vision'
            GROUP BY provider
            ORDER BY total DESC
        """)
        
        # Посты без OCR за последние 24 часа
        recent_without_ocr = await conn.fetchval("""
            SELECT COUNT(*) 
            FROM post_enrichment 
            WHERE kind = 'vision' 
              AND updated_at > NOW() - INTERVAL '24 hours'
              AND (data->'ocr'->>'text' IS NULL OR LENGTH(data->'ocr'->>'text') = 0)
        """)
        
        # Посты с OCR за последние 24 часа
        recent_with_ocr = await conn.fetchval("""
            SELECT COUNT(*) 
            FROM post_enrichment 
            WHERE kind = 'vision' 
              AND updated_at > NOW() - INTERVAL '24 hours'
              AND data->'ocr'->>'text' IS NOT NULL 
              AND LENGTH(data->'ocr'->>'text') > 0
        """)
        
        return {
            "total_vision": total_vision,
            "with_ocr_new_format": with_ocr_new,
            "with_ocr_legacy": with_ocr_legacy,
            "ocr_percentage": round((with_ocr_new / total_vision * 100) if total_vision > 0 else 0, 2),
            "last_ocr": dict(last_ocr) if last_ocr else None,
            "last_vision": dict(last_vision) if last_vision else None,
            "provider_stats": [dict(row) for row in provider_stats],
            "recent_24h_without_ocr": recent_without_ocr,
            "recent_24h_with_ocr": recent_with_ocr,
        }


async def check_ocr_parsing_issues(pool: asyncpg.Pool) -> Dict[str, Any]:
    """Проверка проблем с парсингом OCR."""
    async with pool.acquire() as conn:
        # Посты где OCR = null но должен быть (по description или classification)
        potential_ocr_missing = await conn.fetch("""
            SELECT 
                post_id,
                updated_at,
                provider,
                data->>'classification' as classification,
                data->>'description' as description,
                data->'ocr' as ocr_raw,
                LENGTH(data->>'description') as desc_length
            FROM post_enrichment 
            WHERE kind = 'vision' 
              AND updated_at > NOW() - INTERVAL '7 days'
              AND (data->'ocr' IS NULL OR data->'ocr' = 'null'::jsonb)
              AND (
                  data->>'classification' IN ('document', 'screenshot', 'infographic')
                  OR LENGTH(data->>'description') > 100
              )
            ORDER BY updated_at DESC 
            LIMIT 10
        """)
        
        # Посты с пустым OCR объектом
        empty_ocr = await conn.fetch("""
            SELECT 
                post_id,
                updated_at,
                provider,
                data->'ocr' as ocr_raw
            FROM post_enrichment 
            WHERE kind = 'vision' 
              AND updated_at > NOW() - INTERVAL '7 days'
              AND data->'ocr' IS NOT NULL
              AND data->'ocr' != 'null'::jsonb
              AND (data->'ocr'->>'text' IS NULL OR LENGTH(data->'ocr'->>'text') = 0)
            ORDER BY updated_at DESC 
            LIMIT 10
        """)
        
        return {
            "potential_ocr_missing": [dict(row) for row in potential_ocr_missing],
            "empty_ocr_objects": [dict(row) for row in empty_ocr],
        }


async def check_validation_issues(pool: asyncpg.Pool) -> Dict[str, Any]:
    """Проверка проблем с валидацией."""
    async with pool.acquire() as conn:
        # Проверка структуры OCR данных
        ocr_structure = await conn.fetch("""
            SELECT 
                post_id,
                updated_at,
                provider,
                jsonb_typeof(data->'ocr') as ocr_type,
                data->'ocr' as ocr_raw,
                data->'ocr'->>'text' as ocr_text,
                data->'ocr'->>'engine' as ocr_engine
            FROM post_enrichment 
            WHERE kind = 'vision' 
              AND updated_at > NOW() - INTERVAL '7 days'
              AND data->'ocr' IS NOT NULL
              AND data->'ocr' != 'null'::jsonb
            ORDER BY updated_at DESC 
            LIMIT 20
        """)
        
        return {
            "ocr_structure_samples": [dict(row) for row in ocr_structure],
        }


async def main():
    """Основная функция."""
    print("=" * 80)
    print("Диагностика сохранения OCR текста")
    print("=" * 80)
    print()
    
    pool = await asyncpg.create_pool(DATABASE_URL)
    
    try:
        # 1. Статистика OCR
        print("📊 Статистика OCR в БД:")
        print("-" * 80)
        stats = await check_ocr_statistics(pool)
        
        print(f"Всего Vision записей: {stats['total_vision']}")
        print(f"С OCR текстом (новый формат): {stats['with_ocr_new_format']} ({stats['ocr_percentage']}%)")
        print(f"С OCR текстом (legacy формат): {stats['with_ocr_legacy']}")
        print()
        
        if stats['last_ocr']:
            last_ocr_time = stats['last_ocr']['updated_at']
            time_ago = datetime.now(timezone.utc) - last_ocr_time
            print(f"⏰ Последнее сохранение с OCR:")
            print(f"   Дата: {last_ocr_time}")
            print(f"   Время назад: {time_ago}")
            print(f"   Post ID: {stats['last_ocr']['post_id']}")
            print(f"   Provider: {stats['last_ocr']['provider']}")
            print(f"   Model: {stats['last_ocr']['model']}")
            print(f"   OCR длина: {stats['last_ocr']['ocr_length']} символов")
            print(f"   OCR превью: {stats['last_ocr']['ocr_text'][:100]}...")
        else:
            print("⚠️  Нет записей с OCR текстом!")
        print()
        
        if stats['last_vision']:
            last_vision_time = stats['last_vision']['updated_at']
            time_ago = datetime.now(timezone.utc) - last_vision_time
            print(f"⏰ Последнее сохранение Vision (вообще):")
            print(f"   Дата: {last_vision_time}")
            print(f"   Время назад: {time_ago}")
            print(f"   Post ID: {stats['last_vision']['post_id']}")
            print(f"   Provider: {stats['last_vision']['provider']}")
            print(f"   Model: {stats['last_vision']['model']}")
            print(f"   Есть OCR: {'Да' if stats['last_vision']['ocr_text'] else 'Нет'}")
        print()
        
        print("📈 Статистика по провайдерам:")
        for provider_stat in stats['provider_stats']:
            ocr_pct = round((provider_stat['with_ocr'] / provider_stat['total'] * 100) if provider_stat['total'] > 0 else 0, 2)
            print(f"   {provider_stat['provider']}:")
            print(f"      Всего: {provider_stat['total']}")
            print(f"      С OCR: {provider_stat['with_ocr']} ({ocr_pct}%)")
            print(f"      Последнее обновление: {provider_stat['last_update']}")
        print()
        
        print("📅 За последние 24 часа:")
        print(f"   С OCR: {stats['recent_24h_with_ocr']}")
        print(f"   Без OCR: {stats['recent_24h_without_ocr']}")
        print()
        
        # 2. Проблемы с парсингом
        print("🔍 Проверка проблем с парсингом OCR:")
        print("-" * 80)
        parsing_issues = await check_ocr_parsing_issues(pool)
        
        if parsing_issues['potential_ocr_missing']:
            print(f"⚠️  Найдено {len(parsing_issues['potential_ocr_missing'])} постов где OCR может отсутствовать:")
            for item in parsing_issues['potential_ocr_missing'][:5]:
                print(f"   Post ID: {item['post_id']}")
                print(f"   Classification: {item['classification']}")
                print(f"   Description length: {item['desc_length']}")
                print(f"   Updated: {item['updated_at']}")
                print()
        else:
            print("✅ Проблем с парсингом не обнаружено")
        print()
        
        if parsing_issues['empty_ocr_objects']:
            print(f"⚠️  Найдено {len(parsing_issues['empty_ocr_objects'])} постов с пустым OCR объектом:")
            for item in parsing_issues['empty_ocr_objects'][:5]:
                print(f"   Post ID: {item['post_id']}")
                print(f"   OCR raw: {item['ocr_raw']}")
                print(f"   Updated: {item['updated_at']}")
                print()
        print()
        
        # 3. Структура OCR данных
        print("🔬 Проверка структуры OCR данных:")
        print("-" * 80)
        validation_issues = await check_validation_issues(pool)
        
        if validation_issues['ocr_structure_samples']:
            print(f"Примеры структуры OCR ({len(validation_issues['ocr_structure_samples'])} записей):")
            for item in validation_issues['ocr_structure_samples'][:5]:
                print(f"   Post ID: {item['post_id']}")
                print(f"   OCR type: {item['ocr_type']}")
                print(f"   Has text: {bool(item['ocr_text'])}")
                print(f"   Engine: {item['ocr_engine']}")
                print()
        
        # Рекомендации
        print("=" * 80)
        print("💡 Рекомендации:")
        print("=" * 80)
        
        if stats['recent_24h_with_ocr'] == 0 and stats['recent_24h_without_ocr'] > 0:
            print("⚠️  ПРОБЛЕМА: За последние 24 часа нет новых постов с OCR!")
            print("   Возможные причины:")
            print("   1. GigaChat не возвращает OCR в ответе")
            print("   2. Парсинг ответа не извлекает OCR")
            print("   3. Валидация блокирует сохранение OCR")
            print("   4. OCR текст пустой и фильтруется")
            print()
            print("   Действия:")
            print("   1. Проверить логи worker на ошибки парсинга")
            print("   2. Проверить ответы GigaChat API (логирование включено)")
            print("   3. Проверить валидацию OCR (может быть слишком строгая)")
        
        if stats['ocr_percentage'] < 20:
            print(f"⚠️  Низкий процент OCR ({stats['ocr_percentage']}%)")
            print("   Возможно, большинство изображений не содержат текст")
        
        if parsing_issues['potential_ocr_missing']:
            print(f"⚠️  Найдены посты где OCR может отсутствовать ({len(parsing_issues['potential_ocr_missing'])})")
            print("   Проверьте логику извлечения OCR из ответов GigaChat")
        
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

