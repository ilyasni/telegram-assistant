"""
Simple helper to replay recent posts into posts.indexed for trend backfill.

Usage:
    python worker/scripts/replay_trend_posts.py --hours 6 --limit 200
"""

import argparse
import asyncio
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import List

import asyncpg

# Ensure root of worker package is importable
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from event_bus import EventPublisher, RedisStreamsClient


async def fetch_recent_post_ids(dsn: str, hours: int, limit: int) -> List[str]:
    """Fetch recent post IDs from PostgreSQL."""
    conn = await asyncpg.connect(dsn)
    try:
        interval = timedelta(hours=hours)
        rows = await conn.fetch(
            """
            SELECT id::text
            FROM posts
            WHERE posted_at >= NOW() - $1::interval
            ORDER BY posted_at DESC
            LIMIT $2
            """,
            interval,
            limit,
        )
        return [row["id"] for row in rows]
    finally:
        await conn.close()


async def replay_posts(post_ids: List[str], redis_url: str):
    client = RedisStreamsClient(redis_url)
    await client.connect()
    publisher = EventPublisher(client)
    try:
        for post_id in post_ids:
            payload = {
                "post_id": post_id,
                "tenant_id": "default",
                "vector_id": post_id,
                "indexed_at": "trend-replay",
            }
            await publisher.publish_event("posts.indexed", payload)
            print(f"Replayed post {post_id}")
    finally:
        await client.disconnect()


async def main():
    parser = argparse.ArgumentParser(description="Replay recent posts to posts.indexed")
    parser.add_argument("--hours", type=int, default=6, help="Look-back window in hours")
    parser.add_argument("--limit", type=int, default=200, help="Max posts to replay")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only fetch and print IDs without publishing events",
    )
    args = parser.parse_args()

    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@supabase-db:5432/postgres",
    )
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")

    post_ids = await fetch_recent_post_ids(database_url, args.hours, args.limit)
    if not post_ids:
        print("No posts found for replay window.")
        return

    print(f"Found {len(post_ids)} posts to replay for last {args.hours}h")
    if args.dry_run:
        for pid in post_ids[:10]:
            print(f" - {pid}")
        print("Dry run finished.")
        return

    await replay_posts(post_ids, redis_url)
    print("Replay completed.")


if __name__ == "__main__":
    asyncio.run(main())

