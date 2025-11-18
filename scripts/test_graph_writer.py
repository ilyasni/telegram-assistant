#!/usr/bin/env python3
"""
Тестовый скрипт для Graph Writer Service (Context7 P2).

Проверяет:
1. Подключение к Neo4j
2. Подключение к Redis
3. Создание графовых связей (forwards/replies/author)
4. Чтение событий из Redis Streams
"""
import asyncio
import os
import sys
import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Добавляем корневую директорию проекта в путь
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "api"))

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

# Импорт из проекта
from worker.integrations.neo4j_client import Neo4jClient
from worker.services.graph_writer import GraphWriter

# Конфигурация
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@supabase-db:5432/postgres")
NEO4J_URI = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "changeme")

STREAM_POSTS_PARSED = "stream:posts:parsed"


async def test_neo4j_connection():
    """Тест подключения к Neo4j."""
    print("\n🔍 Тест 1: Подключение к Neo4j")
    
    try:
        neo4j_client = Neo4jClient(uri=NEO4J_URI, username=NEO4J_USER, password=NEO4J_PASSWORD)
        await neo4j_client.connect()
        
        # Проверка health check
        is_healthy = await neo4j_client.health_check()
        if is_healthy:
            print("✅ Neo4j подключение успешно")
            
            # Получение статистики
            stats = await neo4j_client.get_stats()
            print(f"   Connected: {stats.get('connected')}")
            print(f"   Posts: {stats.get('posts_count', 0)}")
            print(f"   Tags: {stats.get('tags_count', 0)}")
            
            await neo4j_client.close()
            return neo4j_client
        else:
            print("❌ Neo4j health check failed")
            return None
            
    except Exception as e:
        print(f"❌ Ошибка подключения к Neo4j: {e}")
        return None


async def test_redis_connection():
    """Тест подключения к Redis."""
    print("\n🔍 Тест 2: Подключение к Redis")
    
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=False)
        await redis_client.ping()
        print("✅ Redis подключение успешно")
        return redis_client
    except Exception as e:
        print(f"❌ Ошибка подключения к Redis: {e}")
        return None


async def test_create_graph_relationships(neo4j_client: Neo4jClient):
    """Тест создания графовых связей (forwards/replies/author)."""
    print("\n🔍 Тест 3: Создание графовых связей")
    
    try:
        # Сначала создаём тестовый Post узел
        test_post_id = f"test_post_{uuid.uuid4().hex[:8]}"
        test_channel_id = "test_channel_123"
        test_user_id = "test_user_123"
        test_tenant_id = "test_tenant_123"
        
        print(f"   Создание тестового Post узла: {test_post_id}")
        
        # Создаём Post узел
        post_created = await neo4j_client.create_post_node(
            post_id=test_post_id,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
            channel_id=test_channel_id,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            content="Test post for graph relationships",
            indexed_at=datetime.now(timezone.utc).isoformat()
        )
        
        if not post_created:
            print("❌ Не удалось создать Post узел")
            return False
        
        print("✅ Post узел создан")
        
        # Тест 3.1: Создание forward связи
        print("\n   Тест 3.1: Создание forward связи")
        forward_created = await neo4j_client.create_forward_relationship(
            post_id=test_post_id,
            forward_from_peer_id={'channel_id': 123456789},
            forward_from_chat_id=123456789,
            forward_from_message_id=100,
            forward_date=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            forward_from_name="Test Channel"
        )
        
        if forward_created:
            print("✅ Forward связь создана")
        else:
            print("⚠️ Forward связь не создана (возможно, нет данных)")
        
        # Тест 3.2: Создание reply связи
        print("\n   Тест 3.2: Создание reply связи")
        # Сначала создаём исходный пост для reply
        original_post_id = f"test_original_post_{uuid.uuid4().hex[:8]}"
        original_created = await neo4j_client.create_post_node(
            post_id=original_post_id,
            user_id=test_user_id,
            tenant_id=test_tenant_id,
            channel_id=test_channel_id,
            expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            content="Original post for reply test",
            indexed_at=datetime.now(timezone.utc).isoformat()
        )
        
        if original_created:
            # Обновляем Post узел с telegram_message_id для поиска
            async with neo4j_client._driver.session() as session:
                await session.run(
                    """
                    MATCH (p:Post {post_id: $post_id})
                    SET p.telegram_message_id = $message_id,
                        p.channel_id = $channel_id
                    RETURN p.post_id
                    """,
                    post_id=original_post_id,
                    message_id=200,
                    channel_id=test_channel_id
                )
            
            reply_created = await neo4j_client.create_reply_relationship(
                post_id=test_post_id,
                reply_to_message_id=200,
                reply_to_chat_id=int(test_channel_id.split('_')[-1]) if '_' in test_channel_id else 123,
                thread_id=None
            )
            
            if reply_created:
                print("✅ Reply связь создана")
            else:
                print("⚠️ Reply связь не создана (возможно, исходный пост не найден)")
        
        # Тест 3.3: Создание author связи
        print("\n   Тест 3.3: Создание author связи")
        author_created = await neo4j_client.create_author_relationship(
            post_id=test_post_id,
            author_peer_id={'user_id': 987654321},
            author_name="Test Author",
            author_type="user"
        )
        
        if author_created:
            print("✅ Author связь создана")
        else:
            print("⚠️ Author связь не создана")
        
        # Проверка созданных связей в Neo4j
        print("\n   Проверка созданных связей:")
        async with neo4j_client._driver.session() as session:
            # Проверка forward связей
            forward_result = await session.run(
                """
                MATCH (p:Post {post_id: $post_id})-[r:FORWARDED_FROM]->(fs:ForwardSource)
                RETURN fs.source_id, fs.source_type, r.forward_date
                """,
                post_id=test_post_id
            )
            forward_record = await forward_result.single()
            if forward_record:
                print(f"   ✅ Forward: {forward_record['fs.source_type']} {forward_record['fs.source_id']}")
            
            # Проверка reply связей
            reply_result = await session.run(
                """
                MATCH (p:Post {post_id: $post_id})-[r:REPLIES_TO]->(orig:Post)
                RETURN orig.post_id, r.thread_id
                """,
                post_id=test_post_id
            )
            reply_record = await reply_result.single()
            if reply_record:
                print(f"   ✅ Reply: -> {reply_record['orig.post_id']}")
            
            # Проверка author связей
            author_result = await session.run(
                """
                MATCH (a:Author)-[r:AUTHOR_OF]->(p:Post {post_id: $post_id})
                RETURN a.author_id, a.author_type, a.name
                """,
                post_id=test_post_id
            )
            author_record = await author_result.single()
            if author_record:
                print(f"   ✅ Author: {author_record['a.author_type']} {author_record['a.author_id']} ({author_record['a.name']})")
        
        # Очистка тестовых данных
        print("\n   Очистка тестовых данных...")
        await neo4j_client.delete_post_node(test_post_id)
        if original_created:
            await neo4j_client.delete_post_node(original_post_id)
        print("✅ Тестовые данные удалены")
        
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при создании графовых связей: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_redis_stream_publish(redis_client: redis.Redis):
    """Тест публикации события в Redis Streams."""
    print("\n🔍 Тест 4: Публикация события в Redis Streams")
    
    try:
        test_event = {
            'schema_version': 'v1',
            'trace_id': str(uuid.uuid4()),
            'occurred_at': datetime.now(timezone.utc).isoformat(),
            'idempotency_key': f"test:channel:123:{uuid.uuid4().hex[:8]}",
            'user_id': 'test_user_123',
            'channel_id': 'test_channel_123',
            'post_id': f"test_post_{uuid.uuid4().hex[:8]}",
            'tenant_id': 'test_tenant_123',
            'text': 'Test post for graph writer',
            'urls': json.dumps(['https://example.com']),
            'posted_at': datetime.now(timezone.utc).isoformat(),
            'telegram_message_id': '12345',
            'tg_message_id': '12345',
            'tg_channel_id': '-1001234567890',
            'has_media': False,
            'is_edited': False,
            'views_count': '0',
            'forwards_count': '0',
            'reactions_count': '0',
            # Context7 P2: Данные о forwards
            'forward_from_peer_id': json.dumps({'channel_id': 987654321}),
            'forward_from_chat_id': '987654321',
            'forward_from_message_id': '200',
            'forward_date': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            'forward_from_name': 'Test Forward Source',
            # Context7 P2: Данные о replies
            'reply_to_message_id': '100',
            'reply_to_chat_id': '123456789',
            'thread_id': None
        }
        
        # Публикация в Redis Streams
        message_id = await redis_client.xadd(
            STREAM_POSTS_PARSED,
            {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) if v is not None else '' 
             for k, v in test_event.items()},
            maxlen=10000
        )
        
        print(f"✅ Событие опубликовано в Redis Streams")
        print(f"   Message ID: {message_id}")
        print(f"   Post ID: {test_event['post_id']}")
        
        # Проверка наличия события в stream
        messages = await redis_client.xread({STREAM_POSTS_PARSED: "0"}, count=1)
        if messages:
            print(f"✅ Событие найдено в stream")
        
        return test_event['post_id']
        
    except Exception as e:
        print(f"❌ Ошибка при публикации события: {e}")
        import traceback
        traceback.print_exc()
        return None


async def test_graph_writer_processing(neo4j_client: Neo4jClient, redis_client: redis.Redis, test_post_id: str):
    """Тест обработки события через GraphWriter."""
    print("\n🔍 Тест 5: Обработка события через GraphWriter")
    
    try:
        # Создаём тестовый Post узел в Neo4j (если ещё не существует)
        await neo4j_client.create_post_node(
            post_id=test_post_id,
            user_id='test_user_123',
            tenant_id='test_tenant_123',
            channel_id='test_channel_123',
            expires_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            content="Test post for graph writer processing",
            indexed_at=datetime.now(timezone.utc).isoformat()
        )
        
        # Создаём GraphWriter (без DB сессии для простоты)
        graph_writer = GraphWriter(
            neo4j_client=neo4j_client,
            redis_client=redis_client,
            consumer_group="test_graph_writer",
            batch_size=10
        )
        
        # Создаём тестовое событие-подобную структуру
        test_event_data = {
            'post_id': test_post_id,
            'channel_id': 'test_channel_123',
            'forward_from_peer_id': {'channel_id': 987654321},
            'forward_from_chat_id': 987654321,
            'forward_from_message_id': 200,
            'forward_date': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            'forward_from_name': 'Test Forward Source',
            'reply_to_message_id': 100,
            'reply_to_chat_id': 123456789,
            'thread_id': None,
            'post_author': 'Test Author'
        }
        
        print(f"   Обработка события для post_id: {test_post_id}")
        
        # Обработка события
        success = await graph_writer._process_post_parsed_event(test_event_data)
        
        if success:
            print("✅ Событие обработано успешно")
            
            # Проверка созданных связей
            async with neo4j_client._driver.session() as session:
                # Forward связь
                forward_result = await session.run(
                    """
                    MATCH (p:Post {post_id: $post_id})-[r:FORWARDED_FROM]->(fs:ForwardSource)
                    RETURN fs.source_id, fs.source_type
                    """,
                    post_id=test_post_id
                )
                forward_record = await forward_result.single()
                if forward_record:
                    print(f"   ✅ Forward связь найдена: {forward_record['fs.source_type']} {forward_record['fs.source_id']}")
                
                # Author связь
                author_result = await session.run(
                    """
                    MATCH (a:Author)-[r:AUTHOR_OF]->(p:Post {post_id: $post_id})
                    RETURN a.author_id, a.author_type
                    """,
                    post_id=test_post_id
                )
                author_record = await author_result.single()
                if author_record:
                    print(f"   ✅ Author связь найдена: {author_record['a.author_type']} {author_record['a.author_id']}")
            
            # Очистка
            await neo4j_client.delete_post_node(test_post_id)
            print("✅ Тестовый пост удалён")
            
            return True
        else:
            print("❌ Ошибка обработки события")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при обработке события: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """Главная функция тестирования."""
    print("=" * 60)
    print("Тестирование Graph Writer Service (Context7 P2)")
    print("=" * 60)
    
    results = {
        'neo4j_connection': False,
        'redis_connection': False,
        'graph_relationships': False,
        'redis_stream_publish': False,
        'graph_writer_processing': False
    }
    
    neo4j_client = None
    redis_client = None
    
    try:
        # Тест 1: Neo4j подключение
        neo4j_client = await test_neo4j_connection()
        results['neo4j_connection'] = neo4j_client is not None
        
        if not neo4j_client:
            print("\n❌ Neo4j недоступен, остальные тесты пропущены")
            return
        
        # Тест 2: Redis подключение
        redis_client = await test_redis_connection()
        results['redis_connection'] = redis_client is not None
        
        if not redis_client:
            print("\n❌ Redis недоступен, остальные тесты пропущены")
            return
        
        # Тест 3: Создание графовых связей
        results['graph_relationships'] = await test_create_graph_relationships(neo4j_client)
        
        # Тест 4: Публикация в Redis Streams
        test_post_id = await test_redis_stream_publish(redis_client)
        results['redis_stream_publish'] = test_post_id is not None
        
        # Тест 5: Обработка через GraphWriter
        if test_post_id:
            results['graph_writer_processing'] = await test_graph_writer_processing(
                neo4j_client, redis_client, test_post_id
            )
        
        # Итоговая сводка
        print("\n" + "=" * 60)
        print("Итоговая сводка тестирования")
        print("=" * 60)
        
        for test_name, result in results.items():
            status = "✅ PASS" if result else "❌ FAIL"
            print(f"{status} {test_name}")
        
        all_passed = all(results.values())
        if all_passed:
            print("\n✅ Все тесты пройдены успешно!")
        else:
            print("\n⚠️ Некоторые тесты не пройдены")
            failed_tests = [name for name, result in results.items() if not result]
            print(f"   Не пройдены: {', '.join(failed_tests)}")
        
    finally:
        # Очистка подключений
        if neo4j_client:
            await neo4j_client.close()
        if redis_client:
            await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())

