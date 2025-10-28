#!/usr/bin/env python3
"""
Исправление проблем пайплайна.

Проблемы:
1. Пустые теги (частично решено - 50/119 имеют теги)
2. Crawl данные не сохраняются
3. Несоответствие размерности Qdrant
4. Neo4j null поля

Context7 best practices:
- Транзакции БД
- Graceful error handling
- Логирование с контекстом
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional

import asyncpg
import structlog

# Настройка логирования
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# Конфигурация
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")


class PipelineFixer:
    """Исправление проблем пайплайна."""
    
    def __init__(self):
        self.db_pool: Optional[asyncpg.Pool] = None
        self.results = {
            'tags_analysis': {},
            'crawl_analysis': {},
            'qdrant_fix': {},
            'neo4j_fix': {}
        }
    
    async def initialize(self):
        """Инициализация подключений."""
        logger.info("Initializing database connection...")
        
        try:
            self.db_pool = await asyncpg.create_pool(
                DATABASE_URL.replace("postgresql://", "postgresql://", 1),
                min_size=2,
                max_size=10,
                command_timeout=30
            )
            logger.info("Database pool created")
        except Exception as e:
            logger.error("Failed to create DB pool", error=str(e))
            raise
    
    async def cleanup(self):
        """Закрытие подключений."""
        if self.db_pool:
            await self.db_pool.close()
    
    async def analyze_tags(self):
        """Анализ проблемы с тегами."""
        logger.info("Analyzing tags issue...")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Статистика тегов
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE tags IS NULL OR array_length(tags, 1) IS NULL OR array_length(tags, 1) = 0) as empty,
                        COUNT(*) FILTER (WHERE tags IS NOT NULL AND array_length(tags, 1) > 0) as with_tags,
                        AVG(array_length(tags, 1)) FILTER (WHERE tags IS NOT NULL AND array_length(tags, 1) > 0) as avg_tags_count
                    FROM post_enrichment
                    WHERE kind = 'tags'
                """)
                
                # Примеры постов с пустыми тегами
                empty_samples = await conn.fetch("""
                    SELECT 
                        pe.post_id,
                        pe.metadata->>'reason' as reason,
                        pe.metadata->>'provider' as provider,
                        p.content
                    FROM post_enrichment pe
                    LEFT JOIN posts p ON pe.post_id = p.id
                    WHERE pe.kind = 'tags'
                    AND (pe.tags IS NULL OR array_length(pe.tags, 1) IS NULL OR array_length(pe.tags, 1) = 0)
                    ORDER BY pe.enriched_at DESC
                    LIMIT 10
                """)
                
                # Примеры постов с тегами
                with_tags_samples = await conn.fetch("""
                    SELECT 
                        pe.post_id,
                        array_length(pe.tags, 1) as tags_count,
                        pe.tags,
                        p.content
                    FROM post_enrichment pe
                    LEFT JOIN posts p ON pe.post_id = p.id
                    WHERE pe.kind = 'tags'
                    AND pe.tags IS NOT NULL
                    AND array_length(pe.tags, 1) > 0
                    ORDER BY pe.enriched_at DESC
                    LIMIT 10
                """)
                
                self.results['tags_analysis'] = {
                    'total_tags_records': stats['total'],
                    'empty_tags': stats['empty'],
                    'with_tags': stats['with_tags'],
                    'avg_tags_count': float(stats['avg_tags_count']) if stats['avg_tags_count'] else None,
                    'empty_samples': [
                        {
                            'post_id': str(row['post_id']),
                            'reason': row['reason'],
                            'provider': row['provider'],
                            'content_preview': (row['content'] or '')[:100]
                        }
                        for row in empty_samples
                    ],
                    'with_tags_samples': [
                        {
                            'post_id': str(row['post_id']),
                            'tags_count': row['tags_count'],
                            'tags': row['tags'][:5] if row['tags'] else [],
                            'content_preview': (row['content'] or '')[:100]
                        }
                        for row in with_tags_samples
                    ]
                }
                
                logger.info("Tags analysis completed",
                           total=stats['total'],
                           empty=stats['empty'],
                           with_tags=stats['with_tags'])
                
        except Exception as e:
            logger.error("Tags analysis failed", error=str(e))
            self.results['tags_analysis'] = {'error': str(e)}
    
    async def analyze_crawl(self):
        """Анализ проблемы с crawl данными."""
        logger.info("Analyzing crawl data issue...")
        
        try:
            async with self.db_pool.acquire() as conn:
                # Статистика crawl
                stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(DISTINCT post_id) FILTER (WHERE kind = 'crawl') as posts_with_crawl,
                        COUNT(*) FILTER (WHERE kind = 'crawl') as crawl_records,
                        COUNT(*) FILTER (WHERE kind = 'crawl' AND crawl_md IS NOT NULL) as with_crawl_md,
                        COUNT(*) FILTER (WHERE kind = 'ocr') as ocr_records,
                        COUNT(*) FILTER (WHERE kind = 'vision') as vision_records
                    FROM post_enrichment
                """)
                
                # Посты с тегами, но без crawl
                tagged_no_crawl = await conn.fetch("""
                    SELECT 
                        p.id as post_id,
                        pe_tags.tags,
                        p.content,
                        p.posted_at
                    FROM posts p
                    INNER JOIN post_enrichment pe_tags ON p.id = pe_tags.post_id AND pe_tags.kind = 'tags'
                    LEFT JOIN post_enrichment pe_crawl ON p.id = pe_crawl.post_id AND pe_crawl.kind = 'crawl'
                    WHERE pe_crawl.post_id IS NULL
                    AND pe_tags.tags IS NOT NULL
                    AND array_length(pe_tags.tags, 1) > 0
                    ORDER BY p.posted_at DESC
                    LIMIT 10
                """)
                
                self.results['crawl_analysis'] = {
                    'posts_with_crawl': stats['posts_with_crawl'],
                    'crawl_records': stats['crawl_records'],
                    'with_crawl_md': stats['with_crawl_md'],
                    'ocr_records': stats['ocr_records'],
                    'vision_records': stats['vision_records'],
                    'tagged_no_crawl_samples': [
                        {
                            'post_id': str(row['post_id']),
                            'tags': row['tags'][:5] if row['tags'] else [],
                            'content_preview': (row['content'] or '')[:100]
                        }
                        for row in tagged_no_crawl
                    ]
                }
                
                logger.info("Crawl analysis completed",
                           posts_with_crawl=stats['posts_with_crawl'],
                           crawl_records=stats['crawl_records'])
                
        except Exception as e:
            logger.error("Crawl analysis failed", error=str(e))
            self.results['crawl_analysis'] = {'error': str(e)}
    
    async def fix_neo4j_structure(self):
        """Анализ структуры Neo4j для исправления."""
        logger.info("Analyzing Neo4j structure...")
        
        # Это информационный анализ - реальные исправления нужно делать через Neo4j запросы
        self.results['neo4j_fix'] = {
            'note': 'Neo4j structure analysis requires direct Neo4j connection',
            'recommendation': 'Check Neo4j client create_post_node method',
            'expected_fields': ['post_id', 'posted_at', 'expires_at', 'user_id', 'tenant_id', 'channel_id']
        }
        
        logger.info("Neo4j analysis completed")
    
    async def run_all_analyses(self):
        """Запуск всех анализов."""
        logger.info("Starting pipeline fixes analysis...")
        
        await self.initialize()
        
        try:
            await self.analyze_tags()
            await self.analyze_crawl()
            await self.fix_neo4j_structure()
        finally:
            await self.cleanup()
        
        return self.results


async def main():
    """Главная функция."""
    fixer = PipelineFixer()
    
    try:
        results = await fixer.run_all_analyses()
        
        # Вывод результатов
        print("\n" + "="*80)
        print("PIPELINE ISSUES ANALYSIS")
        print("="*80)
        print(json.dumps(results, indent=2, default=str))
        print("="*80)
        
        # Рекомендации
        print("\n## Рекомендации:\n")
        
        if results.get('tags_analysis', {}).get('empty', 0) > 0:
            empty_pct = (results['tags_analysis']['empty'] / results['tags_analysis']['total']) * 100
            print(f"⚠️  Теги: {results['tags_analysis']['empty']}/{results['tags_analysis']['total']} ({empty_pct:.1f}%) постов без тегов")
            if results['tags_analysis'].get('empty_samples'):
                sample_reasons = [s.get('reason') for s in results['tags_analysis']['empty_samples'] if s.get('reason')]
                if sample_reasons:
                    print(f"   Причины: {', '.join(set(sample_reasons))}")
            print("   Проверить логи: docker compose logs worker | grep -E '(tagging|gigachat)'")
        
        if results.get('crawl_analysis', {}).get('posts_with_crawl', 0) == 0:
            print("❌ Crawl: Нет постов с crawl данными")
            print("   Проверить: docker compose logs crawl4ai --tail=100")
            print("   Проверить stream: docker compose exec redis redis-cli XINFO GROUPS stream:posts:crawl")
        
        if results.get('crawl_analysis', {}).get('tagged_no_crawl_samples'):
            print(f"⚠️  Crawl: {len(results['crawl_analysis']['tagged_no_crawl_samples'])} постов с тегами, но без crawl")
            print("   Возможно, не срабатывают триггеры для публикации в stream:posts:crawl")
        
        print("\n## Следующие шаги:")
        print("1. Проверить работу GigaChat для тегирования")
        print("2. Проверить публикацию в stream:posts:crawl (crawl_trigger_task)")
        print("3. Проверить обработку crawl4ai_service")
        print("4. Исправить размерность Qdrant (согласовать конфиг и коллекцию)")
        print("5. Исправить структуру Neo4j узлов (добавить недостающие поля)")
        
        sys.exit(0)
        
    except Exception as e:
        logger.error("Pipeline fix analysis failed", error=str(e))
        print(f"❌ Analysis failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

