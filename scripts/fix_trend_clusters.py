#!/usr/bin/env python3
"""
Скрипт для исправления существующих данных трендов:
1. Объединение дубликатов кластеров с одинаковым label
2. Пересчет burst_score для существующих кластеров

Context7: Best Practice - используем транзакции для атомарности операций
"""

import asyncio
import os
import sys
from typing import Dict, List, Optional
from datetime import datetime, timezone

import asyncpg
import structlog

logger = structlog.get_logger()

# Добавляем корневую директорию в путь для импортов
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres"
)


async def get_db_pool() -> asyncpg.Pool:
    """Создать пул подключений к БД."""
    return await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)


def _expected_baseline(freq_baseline: int, window_seconds: int) -> float:
    """Рассчитать ожидаемый baseline для окна."""
    long_window = 24 * 60 * 60  # 24 часа в секундах
    buckets = max(1, long_window // window_seconds)
    if freq_baseline <= 0:
        return 1.0
    return freq_baseline / buckets


def _compute_burst(observed: int, expected: float) -> float:
    """Context7: Исправленный расчет burst score с минимальным baseline = 0.1."""
    if expected <= 0:
        return float(observed)
    baseline = max(0.1, expected)  # Минимальный baseline = 0.1, а не 1.0
    return round(observed / baseline, 2)


async def recalculate_burst_scores(conn: asyncpg.Connection) -> int:
    """
    Пересчитать burst_score для всех существующих кластеров.
    
    Context7: Используем исправленную формулу расчета burst score.
    """
    updated = 0
    
    # Получаем все emerging кластеры
    clusters = await conn.fetch("""
        SELECT id, window_mentions, freq_baseline
        FROM trend_clusters
        WHERE status = 'emerging'
          AND is_generic = false
    """)
    
    logger.info("Recalculating burst scores", total_clusters=len(clusters))
    
    for cluster in clusters:
        cluster_id = cluster["id"]
        window_mentions = cluster["window_mentions"] or 0
        freq_baseline = cluster["freq_baseline"] or 0
        
        # Рассчитываем burst_score для окна 1 час (3600 секунд)
        window_baseline = _expected_baseline(freq_baseline, 3600)
        burst_score = _compute_burst(window_mentions, window_baseline)
        
        # Обновляем burst_score
        await conn.execute(
            """
            UPDATE trend_clusters
            SET burst_score = $1
            WHERE id = $2
            """,
            burst_score,
            cluster_id,
        )
        
        updated += 1
        
        if updated % 100 == 0:
            logger.info("Progress", updated=updated, total=len(clusters))
    
    logger.info("Burst scores recalculated", updated=updated)
    return updated


async def merge_duplicate_clusters(conn: asyncpg.Connection, dry_run: bool = False) -> int:
    """
    Объединить дубликаты кластеров с одинаковым label.
    
    Context7: Best Practice - объединяем посты, метрики и выбираем лучший кластер.
    """
    merged = 0
    
    # Находим дубликаты
    duplicates = await conn.fetch("""
        SELECT label, COUNT(*) as count, array_agg(id ORDER BY last_activity_at DESC) as cluster_ids
        FROM trend_clusters
        WHERE status = 'emerging'
          AND is_generic = false
          AND label IS NOT NULL
          AND label != 'Тренд'
        GROUP BY label
        HAVING COUNT(*) > 1
        ORDER BY count DESC
    """)
    
    logger.info("Found duplicate clusters", total_groups=len(duplicates))
    
    for dup in duplicates:
        label = dup["label"]
        cluster_ids = dup["cluster_ids"]
        count = dup["count"]
        
        if count <= 1:
            continue
        
        # Выбираем главный кластер (самый активный)
        main_cluster_id = cluster_ids[0]
        duplicate_ids = cluster_ids[1:]
        
        logger.info(
            "Merging duplicates",
            label=label,
            main_cluster=main_cluster_id,
            duplicates=duplicate_ids,
            dry_run=dry_run,
        )
        
        if dry_run:
            merged += len(duplicate_ids)
            continue
        
        # Объединяем в транзакции
        async with conn.transaction():
            # 1. Обновляем trend_cluster_posts: переносим посты в главный кластер
            await conn.execute(
                """
                UPDATE trend_cluster_posts
                SET cluster_id = $1
                WHERE cluster_id = ANY($2::uuid[])
                  AND NOT EXISTS (
                      SELECT 1 FROM trend_cluster_posts tcp2
                      WHERE tcp2.cluster_id = $1
                        AND tcp2.post_id = trend_cluster_posts.post_id
                  )
                """,
                main_cluster_id,
                duplicate_ids,
            )
            
            # 2. Обновляем метрики главного кластера: берем максимумы
            await conn.execute(
                """
                UPDATE trend_clusters
                SET 
                    window_mentions = (
                        SELECT MAX(window_mentions) 
                        FROM trend_clusters 
                        WHERE id = ANY($2::uuid[]) OR id = $1
                    ),
                    freq_baseline = (
                        SELECT MAX(freq_baseline) 
                        FROM trend_clusters 
                        WHERE id = ANY($2::uuid[]) OR id = $1
                    ),
                    sources_count = (
                        SELECT MAX(sources_count) 
                        FROM trend_clusters 
                        WHERE id = ANY($2::uuid[]) OR id = $1
                    ),
                    channels_count = (
                        SELECT MAX(channels_count) 
                        FROM trend_clusters 
                        WHERE id = ANY($2::uuid[]) OR id = $1
                    ),
                    source_diversity = (
                        SELECT MAX(source_diversity) 
                        FROM trend_clusters 
                        WHERE id = ANY($2::uuid[]) OR id = $1
                    ),
                    last_activity_at = NOW()
                WHERE id = $1
                """,
                main_cluster_id,
                duplicate_ids,
            )
            
            # 3. Пересчитываем burst_score для главного кластера
            main_cluster = await conn.fetchrow(
                "SELECT window_mentions, freq_baseline FROM trend_clusters WHERE id = $1",
                main_cluster_id,
            )
            if main_cluster:
                window_mentions = main_cluster["window_mentions"] or 0
                freq_baseline = main_cluster["freq_baseline"] or 0
                window_baseline = _expected_baseline(freq_baseline, 3600)
                burst_score = _compute_burst(window_mentions, window_baseline)
                
                await conn.execute(
                    "UPDATE trend_clusters SET burst_score = $1 WHERE id = $2",
                    burst_score,
                    main_cluster_id,
                )
            
            # 4. Помечаем дубликаты как archived
            await conn.execute(
                """
                UPDATE trend_clusters
                SET status = 'archived'
                WHERE id = ANY($1::uuid[])
                """,
                duplicate_ids,
            )
        
        merged += len(duplicate_ids)
        
        if merged % 10 == 0:
            logger.info("Progress", merged=merged)
    
    logger.info("Duplicate clusters merged", total_merged=merged)
    return merged


async def main():
    """Главная функция."""
    import argparse
    
    parser = argparse.ArgumentParser(description="Fix trend clusters: merge duplicates and recalculate burst scores")
    parser.add_argument("--dry-run", action="store_true", help="Don't apply changes, just show what would be done")
    parser.add_argument("--merge-only", action="store_true", help="Only merge duplicates, don't recalculate burst")
    parser.add_argument("--recalculate-only", action="store_true", help="Only recalculate burst scores, don't merge")
    
    args = parser.parse_args()
    
    pool = await get_db_pool()
    
    try:
        async with pool.acquire() as conn:
            if not args.recalculate_only:
                logger.info("Starting duplicate merge", dry_run=args.dry_run)
                merged = await merge_duplicate_clusters(conn, dry_run=args.dry_run)
                logger.info("Merge completed", merged=merged, dry_run=args.dry_run)
            
            if not args.merge_only:
                logger.info("Starting burst score recalculation")
                updated = await recalculate_burst_scores(conn)
                logger.info("Recalculation completed", updated=updated)
        
        logger.info("All operations completed successfully")
        
    except Exception as exc:
        logger.error("Error during fix operations", error=str(exc), exc_info=True)
        raise
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())

