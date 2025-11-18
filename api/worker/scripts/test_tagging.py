#!/usr/bin/env python3
"""
Context7: [C7-ID: test-tagging-001] - Скрипт для ручного тестирования тегирования через GigaChat
Документация: https://github.com/ai-forever/gpt2giga
"""
import asyncio
import os
import sys
import psycopg2
import structlog
from datetime import datetime, timezone

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from ai_providers.gigachain_adapter import GigaChainAdapter, ProviderConfig, TaggingConfig

logger = structlog.get_logger("test_tagging")

async def test_tagging():
    """Тестирует генерацию тегов через GigaChat."""
    db_conn = None
    success_count = 0
    failed_count = 0
    
    try:
        # 1. Подключиться к PostgreSQL
        db_conn = psycopg2.connect(settings.database_url)
        cursor = db_conn.cursor()
        
        logger.info("Connected to database")
        
        # 2. Получить несколько постов для тестирования
        cursor.execute("""
            SELECT id, content
            FROM posts 
            WHERE content IS NOT NULL 
            AND content != ''
            ORDER BY created_at DESC
            LIMIT 5
        """)
        
        posts = cursor.fetchall()
        logger.info(f"Found {len(posts)} posts for testing")
        
        if not posts:
            logger.warning("No posts found for testing")
            return False
        
        # 3. Инициализировать GigaChainAdapter
        # Конфигурация для GigaChat через gpt2giga-proxy
        gigachat_config = ProviderConfig(
            name="gigachat",
            api_key=os.getenv('OPENAI_API_KEY', 'dummy'),
            base_url=os.getenv('OPENAI_API_BASE', 'http://gpt2giga-proxy:8090/v1'),
            model="GigaChat",
            max_tokens=100,
            temperature=0.1,
            timeout=30,
            max_retries=3,
            max_concurrent_requests=1
        )
        
        # Fallback на OpenRouter
        openrouter_config = ProviderConfig(
            name="openrouter",
            api_key=os.getenv('OPENROUTER_API_KEY', ''),
            base_url="https://openrouter.ai/api/v1",
            model="meta-llama/llama-3.1-8b-instruct:free",
            max_tokens=100,
            temperature=0.1,
            timeout=30,
            max_retries=3,
            max_concurrent_requests=1
        )
        
        adapter = GigaChainAdapter(
            primary_config=gigachat_config,
            fallback_config=openrouter_config,
            tagging_config=TaggingConfig()
        )
        
        # 4. Тестировать тегирование для каждого поста
        for post_id, content in posts:
            try:
                logger.info("Testing tagging", post_id=post_id)
                
                # Подготовить текст для тегирования
                text = content
                
                # Генерировать теги
                results = await adapter.generate_tags_batch([text])
                
                if results and len(results) > 0:
                    result = results[0]
                    tags = result.tags
                    language = result.language
                    
                    logger.info(
                        "Tagging successful",
                        post_id=post_id,
                        tags=tags,
                        language=language,
                        tags_count=len(tags)
                    )
                    
                    # Сохранить результат в БД
                    # Context7: Используем унифицированную схему с kind/provider/data
                    import json
                    tags_json = json.dumps({'tags': tags or []})
                    cursor.execute("""
                               INSERT INTO post_enrichment (
                                   post_id, 
                                   provider,
                                   kind,
                                   data
                               ) VALUES (%s, %s, %s, %s::jsonb)
                               ON CONFLICT (post_id, kind) 
                               DO UPDATE SET 
                                   data = jsonb_set(
                                       COALESCE(post_enrichment.data, '{}'::jsonb),
                                       '{tags}',
                                       %s::jsonb
                                   ),
                                   provider = EXCLUDED.provider,
                                   updated_at = NOW()
                           """, (
                               post_id,
                               'gigachat',
                               'tags',
                               tags_json,
                               tags_json
                           ))
                    
                    db_conn.commit()
                    success_count += 1
                    
                else:
                    logger.warning("No tags generated", post_id=post_id)
                    failed_count += 1
                    
            except Exception as e:
                logger.error("Failed to tag post", post_id=post_id, error=str(e))
                failed_count += 1
        
        logger.info("Tagging test completed", success=success_count, failed=failed_count)
        return success_count > 0
        
    except Exception as e:
        logger.error("Unexpected error during tagging test", error=str(e))
        return False
    finally:
        if db_conn:
            db_conn.close()

if __name__ == "__main__":
    if asyncio.run(test_tagging()):
        logger.info("Tagging test completed successfully")
    else:
        logger.error("Tagging test failed")
