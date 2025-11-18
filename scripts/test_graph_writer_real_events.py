#!/usr/bin/env python3
"""
Тестовый скрипт для проверки обработки реальных событий через GraphWriter.

Использует существующие события из Redis Streams для проверки создания графовых связей.
"""
import asyncio
import os
import sys
import json
from pathlib import Path

# Добавляем пути для импортов
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "api"))

import redis.asyncio as redis
from worker.integrations.neo4j_client import Neo4jClient
from worker.services.graph_writer import GraphWriter

# Конфигурация
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

STREAM_POSTS_PARSED = "stream:posts:parsed"


async def test_process_real_events():
    """Тест обработки реальных событий из Redis Streams."""
    print("=" * 60)
    print("Тестирование обработки реальных событий через GraphWriter")
    print("=" * 60)
    
    neo4j_client = None
    redis_client = None
    
    try:
        # Подключения
        print("\n📡 Подключение к Neo4j...")
        neo4j_client = Neo4jClient(uri=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASSWORD)
        await neo4j_client.connect()
        print("✅ Neo4j подключен")
        
        print("\n📡 Подключение к Redis...")
        redis_client = redis.from_url(REDIS_URL, decode_responses=False)
        await redis_client.ping()
        print("✅ Redis подключен")
        
        # Проверка наличия событий
        print("\n🔍 Проверка наличия событий в Redis Streams...")
        stream_length = await redis_client.xlen(STREAM_POSTS_PARSED)
        print(f"   Событий в stream:posts:parsed: {stream_length}")
        
        if stream_length == 0:
            print("⚠️ Нет событий для обработки")
            return
        
        # Читаем несколько последних событий
        print("\n🔍 Чтение последних событий из stream...")
        messages = await redis_client.xread({STREAM_POSTS_PARSED: "0"}, count=5)
        
        if not messages:
            print("⚠️ Не удалось прочитать события")
            return
        
        # Создаём GraphWriter
        print("\n📦 Создание GraphWriter...")
        graph_writer = GraphWriter(
            neo4j_client=neo4j_client,
            redis_client=redis_client,
            consumer_group="test_graph_writer",
            batch_size=10
        )
        print("✅ GraphWriter создан")
        
        # Обработка событий
        print("\n🔍 Обработка событий...")
        processed_count = 0
        success_count = 0
        failed_count = 0
        
        for stream, stream_messages in messages:
            if stream.decode() if isinstance(stream, bytes) else stream == STREAM_POSTS_PARSED:
                for message_id, fields in stream_messages:
                    try:
                        # Парсинг события
                        event_data = {}
                        for key, value in fields.items():
                            key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                            try:
                                value_str = value.decode('utf-8') if isinstance(value, bytes) else str(value)
                                try:
                                    event_data[key_str] = json.loads(value_str)
                                except (json.JSONDecodeError, TypeError):
                                    event_data[key_str] = value_str
                            except:
                                event_data[key_str] = str(value)
                        
                        post_id = event_data.get('post_id')
                        if not post_id:
                            print(f"   ⚠️ Пропущено: нет post_id")
                            continue
                        
                        processed_count += 1
                        print(f"\n   Обработка события {processed_count}: post_id={post_id}")
                        
                        # Обработка события
                        success = await graph_writer._process_post_parsed_event(event_data)
                        
                        if success:
                            success_count += 1
                            print(f"   ✅ Событие обработано успешно")
                            
                            # Проверка созданных связей в Neo4j
                            async with neo4j_client._driver.session() as session:
                                # Forward связи
                                forward_result = await session.run(
                                    "MATCH (p:Post {post_id: $post_id})-[r:FORWARDED_FROM]->(fs:ForwardSource) RETURN count(fs) as count",
                                    post_id=post_id
                                )
                                forward_record = await forward_result.single()
                                forward_count = forward_record['count'] if forward_record else 0
                                
                                # Reply связи
                                reply_result = await session.run(
                                    "MATCH (p:Post {post_id: $post_id})-[r:REPLIES_TO]->(orig:Post) RETURN count(orig) as count",
                                    post_id=post_id
                                )
                                reply_record = await reply_result.single()
                                reply_count = reply_record['count'] if reply_record else 0
                                
                                # Author связи
                                author_result = await session.run(
                                    "MATCH (a:Author)-[r:AUTHOR_OF]->(p:Post {post_id: $post_id}) RETURN count(a) as count",
                                    post_id=post_id
                                )
                                author_record = await author_result.single()
                                author_count = author_record['count'] if author_record else 0
                                
                                if forward_count > 0 or reply_count > 0 or author_count > 0:
                                    print(f"      Forward: {forward_count}, Reply: {reply_count}, Author: {author_count}")
                        else:
                            failed_count += 1
                            print(f"   ❌ Ошибка обработки события")
                        
                    except Exception as e:
                        failed_count += 1
                        print(f"   ❌ Ошибка: {e}")
                        import traceback
                        traceback.print_exc()
        
        # Итоговая статистика
        print("\n" + "=" * 60)
        print("Итоговая статистика")
        print("=" * 60)
        print(f"Обработано событий: {processed_count}")
        print(f"Успешно: {success_count}")
        print(f"Ошибок: {failed_count}")
        
        # Проверка общей статистики графа
        print("\n🔍 Проверка статистики графа...")
        async with neo4j_client._driver.session() as session:
            stats_result = await session.run("""
                MATCH (p:Post)
                OPTIONAL MATCH (p)-[:FORWARDED_FROM]->(fs:ForwardSource)
                OPTIONAL MATCH (p)-[:REPLIES_TO]->(orig:Post)
                OPTIONAL MATCH (a:Author)-[:AUTHOR_OF]->(p)
                RETURN 
                    count(DISTINCT p) as posts,
                    count(DISTINCT fs) as forward_sources,
                    count(DISTINCT orig) as reply_targets,
                    count(DISTINCT a) as authors
            """)
            stats_record = await stats_result.single()
            if stats_record:
                print(f"   Posts: {stats_record['posts']}")
                print(f"   Forward Sources: {stats_record['forward_sources']}")
                print(f"   Reply Targets: {stats_record['reply_targets']}")
                print(f"   Authors: {stats_record['authors']}")
        
        print("\n" + "=" * 60)
        if success_count > 0:
            print("✅ Тест завершён успешно!")
        else:
            print("⚠️ Тест завершён без успешных обработок")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if neo4j_client:
            await neo4j_client.close()
        if redis_client:
            await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(test_process_real_events())

