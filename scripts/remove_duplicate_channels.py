#!/usr/bin/env python3
"""
Скрипт для удаления дублей каналов.
Оставляет канал с наибольшим количеством подписок и постов.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from urllib.parse import urlparse, urlunparse

async def remove_duplicates():
    """Удаление дублей каналов."""
    
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    
    parsed = urlparse(db_url)
    if parsed.scheme == "postgresql":
        new_scheme = "postgresql+asyncpg"
    else:
        new_scheme = parsed.scheme
    
    db_url_async = urlunparse((new_scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    
    engine = create_async_engine(
        db_url_async,
        pool_pre_ping=True,
        pool_size=5,
        pool_timeout=30,
        connect_args={
            "command_timeout": 60,
            "server_settings": {
                "application_name": "remove_duplicate_channels"
            }
        }
    )
    
    async_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    
    async with async_session_factory() as session:
        print("=" * 80)
        print("УДАЛЕНИЕ ДУБЛЕЙ КАНАЛОВ")
        print("=" * 80)
        print()
        
        # Находим дубли
        duplicates_result = await session.execute(text("""
            SELECT 
                LOWER(LTRIM(username, '@')) as normalized_username,
                array_agg(id::text ORDER BY id) as channel_ids
            FROM channels
            WHERE username IS NOT NULL
            GROUP BY LOWER(LTRIM(username, '@'))
            HAVING COUNT(*) > 1
            ORDER BY normalized_username
        """))
        
        duplicates = duplicates_result.fetchall()
        
        if not duplicates:
            print("Дублей каналов не найдено")
            return
        
        print(f"Найдено {len(duplicates)} групп дублей")
        print()
        
        total_deleted = 0
        
        for row in duplicates:
            channel_ids = row.channel_ids
            normalized_username = row.normalized_username
            
            print(f"Обработка @{normalized_username}: {len(channel_ids)} каналов")
            
            # Для каждого канала получаем статистику
            channel_stats = []
            for channel_id in channel_ids:
                stats_result = await session.execute(text("""
                    SELECT 
                        c.id,
                        c.username,
                        c.title,
                        COUNT(DISTINCT uc.user_id) FILTER (WHERE uc.is_active = true) as subscriptions,
                        COUNT(p.id) as posts
                    FROM channels c
                    LEFT JOIN user_channel uc ON c.id = uc.channel_id
                    LEFT JOIN posts p ON c.id = p.channel_id
                    WHERE c.id = :channel_id
                    GROUP BY c.id, c.username, c.title
                """), {"channel_id": channel_id})
                
                stats = stats_result.fetchone()
                if stats:
                    channel_stats.append({
                        'id': str(stats.id),
                        'username': stats.username,
                        'title': stats.title,
                        'subscriptions': stats.subscriptions or 0,
                        'posts': stats.posts or 0
                    })
            
            # Сортируем: сначала по подпискам, потом по постам
            channel_stats.sort(key=lambda x: (x['subscriptions'], x['posts']), reverse=True)
            
            # Оставляем первый (лучший), остальные удаляем
            keep_channel = channel_stats[0]
            delete_channels = channel_stats[1:]
            
            print(f"   Оставляем: {keep_channel['id'][:8]}... (@{keep_channel['username']}) - "
                  f"подписок: {keep_channel['subscriptions']}, постов: {keep_channel['posts']}")
            
            for delete_channel in delete_channels:
                print(f"   Удаляем: {delete_channel['id'][:8]}... (@{delete_channel['username']}) - "
                      f"подписок: {delete_channel['subscriptions']}, постов: {delete_channel['posts']}")
                
                # Переносим подписки на оставляемый канал
                if delete_channel['subscriptions'] > 0:
                    await session.execute(text("""
                        UPDATE user_channel
                        SET channel_id = :new_channel_id
                        WHERE channel_id = :old_channel_id
                        AND NOT EXISTS (
                            SELECT 1 FROM user_channel
                            WHERE user_id = user_channel.user_id
                            AND channel_id = :new_channel_id
                        )
                    """), {
                        "new_channel_id": keep_channel['id'],
                        "old_channel_id": delete_channel['id']
                    })
                    
                    # Деактивируем оставшиеся подписки (если есть конфликты)
                    await session.execute(text("""
                        UPDATE user_channel
                        SET is_active = false
                        WHERE channel_id = :old_channel_id
                    """), {"old_channel_id": delete_channel['id']})
                
                # Удаляем канал (каскадно удалятся связанные записи)
                await session.execute(text("""
                    DELETE FROM channels WHERE id = :channel_id
                """), {"channel_id": delete_channel['id']})
                
                total_deleted += 1
            
            await session.commit()
            print()
        
        print("=" * 80)
        print(f"Удалено {total_deleted} дублей каналов")
        print("=" * 80)

if __name__ == "__main__":
    asyncio.run(remove_duplicates())

