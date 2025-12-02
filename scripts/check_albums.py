#!/usr/bin/env python3
"""
Диагностический скрипт для проверки сохранения альбомов.

Проверяет:
1. Количество постов с grouped_id в БД
2. Количество альбомов в media_groups
3. Логи ошибок сохранения альбомов
4. Метрики Prometheus
5. Условия, которые могут блокировать сохранение
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# Добавляем путь к проекту
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import structlog

logger = structlog.get_logger()


async def check_albums_in_db(db_url: str) -> Dict:
    """Проверка альбомов в БД."""
    engine = create_async_engine(db_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    results = {}
    
    async with async_session() as session:
        # 1. Проверяем посты с grouped_id
        posts_with_grouped_id = await session.execute(text("""
            SELECT 
                COUNT(*) as total_posts,
                COUNT(DISTINCT grouped_id) as unique_albums,
                COUNT(DISTINCT channel_id) as channels_with_albums
            FROM posts
            WHERE grouped_id IS NOT NULL
        """))
        row = posts_with_grouped_id.fetchone()
        results['posts_with_grouped_id'] = {
            'total_posts': row.total_posts if row else 0,
            'unique_albums': row.unique_albums if row else 0,
            'channels_with_albums': row.channels_with_albums if row else 0
        }
        
        # 2. Проверяем альбомы в media_groups
        albums_in_db = await session.execute(text("""
            SELECT 
                COUNT(*) as total_albums,
                COUNT(DISTINCT channel_id) as channels_with_saved_albums,
                COUNT(DISTINCT user_id) as users_with_albums,
                SUM(items_count) as total_items
            FROM media_groups
        """))
        row = albums_in_db.fetchone()
        results['albums_in_media_groups'] = {
            'total_albums': row.total_albums if row else 0,
            'channels_with_saved_albums': row.channels_with_saved_albums if row else 0,
            'users_with_albums': row.users_with_albums if row else 0,
            'total_items': row.total_items if row else 0
        }
        
        # 3. Проверяем посты с grouped_id, но без альбома в media_groups
        missing_albums = await session.execute(text("""
            SELECT 
                p.channel_id,
                p.grouped_id,
                COUNT(*) as posts_count,
                MIN(p.posted_at) as first_post_at,
                MAX(p.posted_at) as last_post_at
            FROM posts p
            WHERE p.grouped_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM media_groups mg
                  WHERE mg.channel_id = p.channel_id
                    AND mg.grouped_id = p.grouped_id
              )
            GROUP BY p.channel_id, p.grouped_id
            ORDER BY posts_count DESC
            LIMIT 20
        """))
        results['missing_albums'] = [
            {
                'channel_id': str(row.channel_id),
                'grouped_id': row.grouped_id,
                'posts_count': row.posts_count,
                'first_post_at': row.first_post_at.isoformat() if row.first_post_at else None,
                'last_post_at': row.last_post_at.isoformat() if row.last_post_at else None
            }
            for row in missing_albums.fetchall()
        ]
        
        # 4. Проверяем альбомы за последние 24 часа
        last_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        recent_albums = await session.execute(text("""
            SELECT 
                COUNT(*) as albums_count,
                COUNT(DISTINCT channel_id) as channels_count
            FROM media_groups
            WHERE created_at >= :cutoff
        """), {"cutoff": last_24h})
        row = recent_albums.fetchone()
        results['recent_albums'] = {
            'albums_last_24h': row.albums_count if row else 0,
            'channels_last_24h': row.channels_count if row else 0
        }
        
        # 5. Проверяем посты с grouped_id за последние 24 часа
        recent_posts_with_grouped = await session.execute(text("""
            SELECT 
                COUNT(*) as posts_count,
                COUNT(DISTINCT grouped_id) as albums_count,
                COUNT(DISTINCT channel_id) as channels_count
            FROM posts
            WHERE grouped_id IS NOT NULL
              AND posted_at >= :cutoff
        """), {"cutoff": last_24h})
        row = recent_posts_with_grouped.fetchone()
        results['recent_posts_with_grouped'] = {
            'posts_last_24h': row.posts_count if row else 0,
            'albums_last_24h': row.albums_count if row else 0,
            'channels_last_24h': row.channels_count if row else 0
        }
        
        # 6. Проверяем user_channel подписки для каналов с постами grouped_id
        channels_with_missing_albums = await session.execute(text("""
            SELECT DISTINCT p.channel_id
            FROM posts p
            WHERE p.grouped_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM media_groups mg
                  WHERE mg.channel_id = p.channel_id
                    AND mg.grouped_id = p.grouped_id
              )
            LIMIT 10
        """))
        channel_ids = [str(row.channel_id) for row in channels_with_missing_albums.fetchall()]
        
        if channel_ids:
            user_subscriptions = await session.execute(text(f"""
                SELECT 
                    uc.channel_id,
                    COUNT(DISTINCT uc.user_id) as subscribed_users,
                    COUNT(DISTINCT CASE WHEN uc.is_active THEN uc.user_id END) as active_subscriptions
                FROM user_channel uc
                WHERE uc.channel_id = ANY(CAST(:channel_ids AS uuid[]))
                GROUP BY uc.channel_id
            """), {"channel_ids": channel_ids})
            results['user_subscriptions'] = [
                {
                    'channel_id': str(row.channel_id),
                    'subscribed_users': row.subscribed_users,
                    'active_subscriptions': row.active_subscriptions
                }
                for row in user_subscriptions.fetchall()
            ]
        else:
            results['user_subscriptions'] = []
    
    await engine.dispose()
    return results


def print_results(results: Dict):
    """Вывод результатов проверки."""
    print("\n" + "="*80)
    print("ДИАГНОСТИКА СОХРАНЕНИЯ АЛЬБОМОВ")
    print("="*80 + "\n")
    
    # 1. Посты с grouped_id
    posts_data = results.get('posts_with_grouped_id', {})
    print(f"📊 Посты с grouped_id:")
    print(f"   Всего постов: {posts_data.get('total_posts', 0)}")
    print(f"   Уникальных альбомов: {posts_data.get('unique_albums', 0)}")
    print(f"   Каналов с альбомами: {posts_data.get('channels_with_albums', 0)}")
    
    # 2. Альбомы в media_groups
    albums_data = results.get('albums_in_media_groups', {})
    print(f"\n💾 Альбомы в media_groups:")
    print(f"   Всего альбомов: {albums_data.get('total_albums', 0)}")
    print(f"   Каналов с сохранёнными альбомами: {albums_data.get('channels_with_saved_albums', 0)}")
    print(f"   Пользователей с альбомами: {albums_data.get('users_with_albums', 0)}")
    print(f"   Всего элементов: {albums_data.get('total_items', 0)}")
    
    # 3. Пропущенные альбомы
    missing = results.get('missing_albums', [])
    print(f"\n⚠️  Пропущенные альбомы (первые 20):")
    if missing:
        print(f"   Найдено пропущенных альбомов: {len(missing)}")
        for i, album in enumerate(missing[:5], 1):
            print(f"   {i}. Channel: {album['channel_id'][:8]}..., grouped_id: {album['grouped_id']}, "
                  f"постов: {album['posts_count']}")
    else:
        print("   ✅ Все альбомы сохранены!")
    
    # 4. Альбомы за последние 24 часа
    recent_albums = results.get('recent_albums', {})
    recent_posts = results.get('recent_posts_with_grouped', {})
    print(f"\n⏰ За последние 24 часа:")
    print(f"   Постов с grouped_id: {recent_posts.get('posts_last_24h', 0)}")
    print(f"   Уникальных альбомов (по grouped_id): {recent_posts.get('albums_last_24h', 0)}")
    print(f"   Сохранено альбомов в media_groups: {recent_albums.get('albums_last_24h', 0)}")
    
    # Проверяем разницу
    expected = recent_posts.get('albums_last_24h', 0)
    saved = recent_albums.get('albums_last_24h', 0)
    if expected > 0:
        coverage = (saved / expected * 100) if expected > 0 else 0
        print(f"   Покрытие: {coverage:.1f}% ({saved}/{expected})")
        if coverage < 50:
            print(f"   ⚠️  ВНИМАНИЕ: Низкое покрытие сохранения альбомов!")
    
    # 5. Подписки пользователей
    subscriptions = results.get('user_subscriptions', [])
    if subscriptions:
        print(f"\n👥 Подписки пользователей на каналы с пропущенными альбомами:")
        for sub in subscriptions[:5]:
            print(f"   Channel: {sub['channel_id'][:8]}..., "
                  f"подписок: {sub['subscribed_users']}, "
                  f"активных: {sub['active_subscriptions']}")
    
    print("\n" + "="*80)
    print("РЕКОМЕНДАЦИИ:")
    print("="*80)
    
    if missing:
        print("1. ⚠️  Обнаружены пропущенные альбомы")
        print("   Проверьте логи на наличие ошибок:")
        print("   - 'User UUID not found'")
        print("   - 'User not subscribed to channel'")
        print("   - 'Failed to save media group'")
        print("   - 'CRITICAL: Cannot save albums'")
    
    if recent_posts.get('albums_last_24h', 0) > recent_albums.get('albums_last_24h', 0):
        print("2. ⚠️  За последние 24 часа не все альбомы сохранены")
        print("   Проверьте метрики Prometheus: album_save_failures_total")
        print("   Проверьте логи telethon-ingest на ошибки сохранения")
    
    if not subscriptions and missing:
        print("3. ⚠️  Возможная причина: пользователи не подписаны на каналы")
        print("   Альбомы сохраняются только для подписанных пользователей")
    
    print("\nДля детальной диагностики проверьте:")
    print("  - Логи: docker compose logs --since=1h telethon-ingest | grep -E '(album|Media group)'")
    print("  - Метрики: curl http://localhost:8002/metrics | grep album_save_failures")
    print("="*80 + "\n")


async def main():
    """Главная функция."""
    import os
    from config import settings
    
    db_url = getattr(settings, 'database_url', os.getenv('DATABASE_URL'))
    if not db_url:
        print("❌ DATABASE_URL не настроен")
        return 1
    
    # Конвертируем в async URL если нужно
    if db_url.startswith('postgresql://'):
        db_url = db_url.replace('postgresql://', 'postgresql+asyncpg://')
    elif db_url.startswith('postgresql+psycopg2://'):
        db_url = db_url.replace('postgresql+psycopg2://', 'postgresql+asyncpg://')
    
    try:
        results = await check_albums_in_db(db_url)
        print_results(results)
        return 0
    except Exception as e:
        logger.error("Ошибка при проверке альбомов", error=str(e), exc_info=True)
        print(f"❌ Ошибка: {e}")
        return 1


if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

