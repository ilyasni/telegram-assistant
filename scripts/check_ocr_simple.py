#!/usr/bin/env python3
"""
Упрощённая проверка OCR в post_enrichment (использует psycopg2).
Context7 best practice: валидация данных через SQL запросы.

Использование:
    python scripts/check_ocr_simple.py --limit 10
"""

import os
import sys
import json
from pathlib import Path

# Добавляем пути
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    print("⚠️  Установите psycopg2: pip install psycopg2-binary")
    sys.exit(1)


def get_db_url():
    """Получение DATABASE_URL из env или docker-compose."""
    db_url = os.getenv('DATABASE_URL')
    if db_url:
        return db_url
    
    # Попытка получить из docker-compose
    db_host = os.getenv('POSTGRES_HOST', 'localhost')
    db_port = os.getenv('POSTGRES_PORT', '5432')
    db_name = os.getenv('POSTGRES_DB', 'telegram_assistant')
    db_user = os.getenv('POSTGRES_USER', 'postgres')
    db_password = os.getenv('POSTGRES_PASSWORD', 'postgres')
    
    return f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"


def check_ocr_statistics(db_url: str, limit: int = 20):
    """Проверка статистики OCR."""
    try:
        conn = psycopg2.connect(db_url)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        # Общая статистика
        cursor.execute("""
            SELECT 
                COUNT(*) as total_vision_records,
                COUNT(CASE WHEN data->'ocr' IS NOT NULL THEN 1 END) as has_ocr_new,
                COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL 
                           AND data->'ocr'->>'text' != '' THEN 1 END) as has_ocr_text_new,
                COUNT(CASE WHEN ocr_text IS NOT NULL AND ocr_text != '' THEN 1 END) as has_ocr_legacy,
                COUNT(CASE WHEN data->'ocr' IS NOT NULL AND ocr_text IS NOT NULL THEN 1 END) as has_ocr_both,
                COUNT(CASE WHEN (data->'ocr' IS NULL OR data->'ocr'->>'text' IS NULL OR data->'ocr'->>'text' = '') 
                           AND (ocr_text IS NULL OR ocr_text = '') THEN 1 END) as has_ocr_none
            FROM post_enrichment
            WHERE kind = 'vision'
        """)
        
        stats = cursor.fetchone()
        
        print("\n" + "="*60)
        print("СТАТИСТИКА OCR В POST_ENRICHMENT")
        print("="*60)
        print(f"Всего записей Vision: {stats['total_vision_records']}")
        print(f"  - С OCR в новом формате (data->'ocr'): {stats['has_ocr_new']}")
        print(f"  - С OCR текстом (data->'ocr'->>'text'): {stats['has_ocr_text_new']}")
        print(f"  - С OCR в legacy формате (ocr_text): {stats['has_ocr_legacy']}")
        print(f"  - С OCR в обоих форматах: {stats['has_ocr_both']}")
        print(f"  - Без OCR: {stats['has_ocr_none']}")
        
        if stats['total_vision_records'] > 0:
            coverage_new = round(stats['has_ocr_text_new'] / stats['total_vision_records'] * 100, 2)
            coverage_legacy = round(stats['has_ocr_legacy'] / stats['total_vision_records'] * 100, 2)
            print(f"\nПокрытие OCR:")
            print(f"  - Новый формат: {coverage_new}%")
            print(f"  - Legacy формат: {coverage_legacy}%")
        
        # Статистика по провайдерам
        cursor.execute("""
            SELECT 
                provider,
                COUNT(*) as total,
                COUNT(CASE WHEN data->'ocr' IS NOT NULL THEN 1 END) as with_ocr,
                COUNT(CASE WHEN data->'ocr'->>'engine' = 'gigachat' THEN 1 END) as engine_gigachat,
                COUNT(CASE WHEN data->'ocr'->>'engine' = 'tesseract' THEN 1 END) as engine_tesseract
            FROM post_enrichment
            WHERE kind = 'vision'
            GROUP BY provider
            ORDER BY total DESC
        """)
        
        providers = cursor.fetchall()
        if providers:
            print("\n" + "-"*60)
            print("СТАТИСТИКА ПО ПРОВАЙДЕРАМ:")
            print("-"*60)
            for p in providers:
                print(f"\nПровайдер: {p['provider']}")
                print(f"  Всего: {p['total']}")
                print(f"  С OCR: {p['with_ocr']}")
                print(f"    - Engine gigachat: {p['engine_gigachat']}")
                print(f"    - Engine tesseract: {p['engine_tesseract']}")
        
        # Последние записи с OCR
        cursor.execute("""
            SELECT 
                post_id,
                provider,
                status,
                data->'ocr'->>'text' as ocr_text_new,
                data->'ocr'->>'engine' as ocr_engine,
                data->'ocr'->>'confidence' as ocr_confidence,
                ocr_text as ocr_text_legacy,
                LENGTH(data->'ocr'->>'text') as ocr_length_new,
                LENGTH(ocr_text) as ocr_length_legacy,
                updated_at
            FROM post_enrichment
            WHERE kind = 'vision'
              AND (data->'ocr' IS NOT NULL OR ocr_text IS NOT NULL)
            ORDER BY updated_at DESC
            LIMIT %s
        """, (limit,))
        
        rows = cursor.fetchall()
        if rows:
            print("\n" + "-"*60)
            print(f"ПОСЛЕДНИЕ {len(rows)} ЗАПИСЕЙ С OCR:")
            print("-"*60)
            for i, row in enumerate(rows, 1):
                print(f"\n{i}. Post ID: {str(row['post_id'])[:8]}...")
                print(f"   Provider: {row['provider']}")
                print(f"   Status: {row['status']}")
                print(f"   OCR Engine: {row['ocr_engine'] or 'N/A'}")
                print(f"   OCR Confidence: {row['ocr_confidence'] or 'N/A'}")
                print(f"   OCR Length (new): {row['ocr_length_new'] or 0}")
                print(f"   OCR Length (legacy): {row['ocr_length_legacy'] or 0}")
                if row['ocr_text_new']:
                    preview = row['ocr_text_new'][:100].replace('\n', ' ')
                    print(f"   OCR Preview: {preview}...")
                print(f"   Updated: {row['updated_at']}")
        
        # Проблемные записи (без OCR)
        cursor.execute("""
            SELECT 
                post_id,
                provider,
                status,
                updated_at
            FROM post_enrichment
            WHERE kind = 'vision'
              AND (data->'ocr' IS NULL 
                   OR data->'ocr'->>'text' IS NULL 
                   OR data->'ocr'->>'text' = '')
              AND (ocr_text IS NULL OR ocr_text = '')
            ORDER BY updated_at DESC
            LIMIT 10
        """)
        
        problem_rows = cursor.fetchall()
        if problem_rows:
            print("\n" + "-"*60)
            print(f"ПРОБЛЕМНЫЕ ЗАПИСИ (БЕЗ OCR) - {len(problem_rows)}:")
            print("-"*60)
            for i, row in enumerate(problem_rows, 1):
                print(f"{i}. Post ID: {str(row['post_id'])[:8]}... | Provider: {row['provider']} | Updated: {row['updated_at']}")
        
        cursor.close()
        conn.close()
        
        print("\n" + "="*60)
        print("ПРОВЕРКА ЗАВЕРШЕНА")
        print("="*60 + "\n")
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    import argparse
    
    parser = argparse.ArgumentParser(description='Проверка OCR в post_enrichment')
    parser.add_argument('--limit', type=int, default=10, help='Лимит записей для просмотра')
    parser.add_argument('--db-url', help='Database URL (если не указан, берётся из env)')
    
    args = parser.parse_args()
    
    db_url = args.db_url or get_db_url()
    
    if not db_url:
        print("❌ DATABASE_URL не найден. Укажите через --db-url или установите переменную окружения")
        sys.exit(1)
    
    check_ocr_statistics(db_url, args.limit)

