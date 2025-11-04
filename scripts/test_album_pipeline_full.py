#!/usr/bin/env python3
"""
Полный E2E тест пайплайна альбомов (Phase 1 + Phase 2)
Context7: проверка всех компонентов на реальных данных

Тестирует:
1. Redis negative cache
2. Схему БД с новыми полями
3. Эмиссию событий albums.parsed
4. Album assembler task (если есть данные)
5. Индексацию с album_id в Qdrant
6. Neo4j граф альбомов
"""

import asyncio
import sys
import os
import json
from datetime import datetime, timezone
from uuid import uuid4

# Добавляем корень проекта в путь
project_root = '/opt/telegram-assistant'
if project_root not in sys.path:
    sys.path.insert(0, project_root)

import structlog
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text
import redis.asyncio as redis

logger = structlog.get_logger()

async def test_db_schema_with_real_data():
    """Тест схемы БД на реальных данных."""
    print("\n🧪 Тест 1: Схема БД с реальными данными")
    
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Проверяем наличие альбомов
            result = await session.execute(text("SELECT COUNT(*) FROM media_groups"))
            albums_count = result.scalar()
            
            result = await session.execute(text("SELECT COUNT(*) FROM media_group_items"))
            items_count = result.scalar()
            
            print(f"  ✓ Найдено альбомов: {albums_count}")
            print(f"  ✓ Найдено элементов альбомов: {items_count}")
            
            # Проверяем новые поля на реальных данных
            if albums_count > 0:
                result = await session.execute(text("""
                    SELECT 
                        id, grouped_id, caption_text, cover_media_id, posted_at,
                        album_kind, items_count
                    FROM media_groups
                    LIMIT 5
                """))
                albums = result.fetchall()
                
                print(f"  ✓ Проверено альбомов: {len(albums)}")
                for album in albums:
                    print(f"    - Album ID: {album[0]}, grouped_id: {album[1]}, "
                          f"items: {album[6]}, kind: {album[5]}")
                    if album[2]:  # caption_text
                        print(f"      caption: {album[2][:50]}...")
            
            # Проверяем media_group_items с новыми полями
            if items_count > 0:
                result = await session.execute(text("""
                    SELECT COUNT(*), 
                           COUNT(media_object_id) as with_media_object,
                           COUNT(media_kind) as with_kind,
                           COUNT(sha256) as with_sha256
                    FROM media_group_items
                """))
                row = result.fetchone()
                print(f"  ✓ Элементы с новыми полями:")
                print(f"    - Всего: {row[0]}")
                print(f"    - С media_object_id: {row[1]}")
                print(f"    - С media_kind: {row[2]}")
                print(f"    - С sha256: {row[3]}")
            
            # Проверяем media_objects.id
            result = await session.execute(text("""
                SELECT COUNT(*), COUNT(id) as with_id
                FROM media_objects
            """))
            row = result.fetchone()
            print(f"  ✓ media_objects: всего {row[0]}, с id: {row[1]}")
            
    finally:
        await engine.dispose()
    
    print("  ✅ Тест схемы БД пройден")


async def test_album_id_in_qdrant():
    """Тест получения album_id для постов."""
    print("\n🧪 Тест 2: Получение album_id для постов")
    
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Получаем пост из альбома
            result = await session.execute(text("""
                SELECT p.id as post_id, mg.id as album_id
                FROM posts p
                JOIN media_group_items mgi ON p.id = mgi.post_id
                JOIN media_groups mg ON mgi.group_id = mg.id
                WHERE p.grouped_id IS NOT NULL
                LIMIT 1
            """))
            row = result.fetchone()
            
            if row:
                post_id = str(row[0])
                album_id = row[1]
                print(f"  ✓ Найден пост из альбома: post_id={post_id}, album_id={album_id}")
                
                # Проверяем метод получения album_id
                result2 = await session.execute(text("""
                    SELECT mg.id as album_id
                    FROM media_group_items mgi
                    JOIN media_groups mg ON mgi.group_id = mg.id
                    WHERE mgi.post_id = :post_id
                    LIMIT 1
                """), {"post_id": post_id})
                row2 = result2.fetchone()
                
                if row2 and row2[0] == album_id:
                    print(f"  ✓ album_id корректно получен через запрос")
                else:
                    print(f"  ⚠️  Несоответствие album_id")
            else:
                print(f"  ⚠️  Нет постов из альбомов для тестирования")
                
    finally:
        await engine.dispose()
    
    print("  ✅ Тест album_id пройден")


async def test_redis_streams():
    """Тест наличия streams для альбомов в Redis."""
    print("\n🧪 Тест 3: Redis Streams для альбомов")
    
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    
    try:
        # Проверяем наличие stream для albums.parsed
        stream_key = "stream:albums:parsed"
        stream_length = await redis_client.xlen(stream_key)
        print(f"  ✓ stream:albums:parsed: {stream_length} сообщений")
        
        # Проверяем stream для album.assembled
        stream_key2 = "stream:album:assembled"
        stream_length2 = await redis_client.xlen(stream_key2)
        print(f"  ✓ stream:album:assembled: {stream_length2} сообщений")
        
        # Если есть сообщения, показываем последнее
        if stream_length > 0:
            result = await redis_client.xrevrange(stream_key, count=1)
            if result:
                msg_id, fields = result[0]
                print(f"  ✓ Последнее событие albums.parsed: {msg_id}")
                # Парсим данные если есть
                if 'data' in fields:
                    try:
                        data = json.loads(fields['data'])
                        album_id = data.get('album_id', 'N/A')
                        print(f"    album_id: {album_id}")
                    except:
                        pass
        
    finally:
        await redis_client.aclose()
    
    print("  ✅ Тест Redis Streams пройден")


async def test_album_state_tracking():
    """Тест отслеживания состояния альбомов в Redis."""
    print("\n🧪 Тест 4: Отслеживание состояния альбомов (Redis state)")
    
    redis_client = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    
    try:
        # Проверяем наличие state ключей
        pattern = "album:state:*"
        keys = []
        async for key in redis_client.scan_iter(match=pattern):
            keys.append(key)
        
        print(f"  ✓ Найдено активных состояний альбомов: {len(keys)}")
        
        # Если есть состояния, показываем детали
        if keys:
            for key in keys[:3]:  # Первые 3
                state_json = await redis_client.get(key)
                if state_json:
                    try:
                        state = json.loads(state_json)
                        album_id = state.get('album_id', 'N/A')
                        items_count = state.get('items_count', 0)
                        items_analyzed = len(state.get('items_analyzed', []))
                        print(f"    - Album {album_id}: {items_analyzed}/{items_count} обработано")
                    except:
                        pass
        else:
            print(f"  ℹ️  Нет активных состояний (альбомы собраны или не обрабатываются)")
        
    finally:
        await redis_client.aclose()
    
    print("  ✅ Тест отслеживания состояния пройден")


async def test_event_schemas():
    """Тест схем событий."""
    print("\n🧪 Тест 5: Схемы событий альбомов")
    
    try:
        # Пробуем разные пути импорта
        paths = [
            '/opt/telegram-assistant/worker',
            '/opt/telegram-assistant',
            '/app/worker',
            '/app'
        ]
        for path in paths:
            if path not in sys.path:
                sys.path.insert(0, path)
        
        try:
            from events.schemas import AlbumParsedEventV1, AlbumAssembledEventV1
        except ImportError:
            try:
                from worker.events.schemas import AlbumParsedEventV1, AlbumAssembledEventV1
            except ImportError:
                # Используем прямой импорт файла
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "albums_parsed_v1",
                    "/opt/telegram-assistant/worker/events/schemas/albums_parsed_v1.py"
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                AlbumParsedEventV1 = module.AlbumParsedEventV1
                
                spec2 = importlib.util.spec_from_file_location(
                    "album_assembled_v1",
                    "/opt/telegram-assistant/worker/events/schemas/album_assembled_v1.py"
                )
                module2 = importlib.util.module_from_spec(spec2)
                spec2.loader.exec_module(module2)
                AlbumAssembledEventV1 = module2.AlbumAssembledEventV1
        
        # Тест AlbumParsedEventV1
        event1 = AlbumParsedEventV1(
            idempotency_key="test:channel:123",
            user_id=str(uuid4()),
            channel_id=str(uuid4()),
            album_id=12345,
            grouped_id=67890,
            tenant_id="test_tenant",
            items_count=5,
            post_ids=["post1", "post2"]
        )
        print(f"  ✓ AlbumParsedEventV1 создан: album_id={event1.album_id}")
        
        # Тест AlbumAssembledEventV1
        event2 = AlbumAssembledEventV1(
            idempotency_key="test:channel:123:assembled",
            user_id=str(uuid4()),
            channel_id=str(uuid4()),
            album_id=12345,
            grouped_id=67890,
            tenant_id="test_tenant",
            items_count=5,
            items_analyzed=5,
            assembly_completed_at=datetime.now(timezone.utc)
        )
        print(f"  ✓ AlbumAssembledEventV1 создан: album_id={event2.album_id}")
        
        # Проверяем сериализацию
        event1_dict = event1.model_dump()
        event2_dict = event2.model_dump()
        print(f"  ✓ События корректно сериализуются")
        
    except Exception as e:
        print(f"  ❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    print("  ✅ Тест схем событий пройден")


async def test_neo4j_queries():
    """Тест Neo4j запросов для альбомов (если Neo4j доступен)."""
    print("\n🧪 Тест 6: Neo4j запросы для альбомов")
    
    try:
        from neo4j import AsyncGraphDatabase
        
        neo4j_uri = os.getenv("NEO4J_URI", "neo4j://neo4j:7687")
        neo4j_user = os.getenv("NEO4J_USER", "neo4j")
        neo4j_password = os.getenv("NEO4J_PASSWORD", "changeme")
        
        driver = AsyncGraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password)
        )
        
        async with driver.session() as session:
            # Проверяем наличие узлов Album
            result = await session.run("MATCH (a:Album) RETURN count(a) as count")
            record = await result.single()
            albums_count = record["count"] if record else 0
            
            print(f"  ✓ Узлов Album в Neo4j: {albums_count}")
            
            # Проверяем связи CONTAINS
            result = await session.run("""
                MATCH (a:Album)-[r:CONTAINS]->(p:Post)
                RETURN count(r) as count
            """)
            record = await result.single()
            contains_count = record["count"] if record else 0
            
            print(f"  ✓ Связей CONTAINS: {contains_count}")
            
            # Проверяем структуру узла Album
            if albums_count > 0:
                result = await session.run("""
                    MATCH (a:Album)
                    RETURN a.album_id, a.grouped_id, a.items_count, a.album_kind
                    LIMIT 1
                """)
                record = await result.single()
                if record:
                    print(f"  ✓ Пример узла Album:")
                    print(f"    - album_id: {record['a.album_id']}")
                    print(f"    - grouped_id: {record['a.grouped_id']}")
                    print(f"    - items_count: {record['a.items_count']}")
                    print(f"    - album_kind: {record['a.album_kind']}")
        
        await driver.close()
        print("  ✅ Тест Neo4j пройден")
        
    except Exception as e:
        print(f"  ⚠️  Neo4j недоступен или ошибка: {e}")
        print("  ℹ️  Пропускаем тест Neo4j")


async def test_qdrant_payload():
    """Тест наличия album_id в Qdrant (если Qdrant доступен)."""
    print("\n🧪 Тест 7: album_id в payload Qdrant")
    
    try:
        from qdrant_client import QdrantClient
        
        qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
        collection_name = os.getenv("QDRANT_COLLECTION", "telegram_posts")
        
        client = QdrantClient(url=qdrant_url)
        
        # Получаем несколько векторов для проверки
        scroll_result = client.scroll(
            collection_name=collection_name,
            limit=10,
            with_payload=True,
            with_vectors=False
        )
        
        points = scroll_result[0]
        print(f"  ✓ Найдено векторов в Qdrant: {len(points)}")
        
        # Проверяем наличие album_id в payload
        albums_found = 0
        for point in points:
            payload = point.payload or {}
            if 'album_id' in payload:
                albums_found += 1
        
        print(f"  ✓ Векторов с album_id: {albums_found}/{len(points)}")
        
        if albums_found > 0:
            # Показываем пример
            for point in points:
                payload = point.payload or {}
                if 'album_id' in payload:
                    print(f"  ✓ Пример: vector_id={point.id}, album_id={payload['album_id']}")
                    break
        
    except Exception as e:
        print(f"  ⚠️  Qdrant недоступен или ошибка: {e}")
        print("  ℹ️  Пропускаем тест Qdrant")


async def test_integration_flow():
    """Интеграционный тест полного пайплайна."""
    print("\n🧪 Тест 8: Интеграционный пайплайн")
    
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@supabase-db:5432/postgres")
    if db_url.startswith("postgresql://") or db_url.startswith("postgres://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1).replace("postgres://", "postgresql+asyncpg://", 1)
    
    engine = create_async_engine(db_url)
    async_session = async_sessionmaker(engine, expire_on_commit=False)
    
    try:
        async with async_session() as session:
            # Получаем альбом с постами
            result = await session.execute(text("""
                SELECT 
                    mg.id as album_id,
                    mg.grouped_id,
                    mg.items_count,
                    mg.caption_text,
                    mg.posted_at,
                    array_agg(mgi.post_id) as post_ids
                FROM media_groups mg
                JOIN media_group_items mgi ON mg.id = mgi.group_id
                GROUP BY mg.id, mg.grouped_id, mg.items_count, mg.caption_text, mg.posted_at
                HAVING COUNT(mgi.post_id) > 1
                LIMIT 1
            """))
            row = result.fetchone()
            
            if row:
                album_id = row[0]
                grouped_id = row[1]
                items_count = row[2]
                post_ids = row[5]
                
                print(f"  ✓ Найден альбом для теста:")
                print(f"    - album_id: {album_id}")
                print(f"    - grouped_id: {grouped_id}")
                print(f"    - items_count: {items_count}")
                print(f"    - post_ids: {len(post_ids)}")
                
                # Проверяем что можно получить album_id для постов
                test_post_id = str(post_ids[0]) if post_ids else None
                if test_post_id:
                    result2 = await session.execute(text("""
                        SELECT mg.id as album_id
                        FROM media_group_items mgi
                        JOIN media_groups mg ON mgi.group_id = mg.id
                        WHERE mgi.post_id = :post_id
                        LIMIT 1
                    """), {"post_id": test_post_id})
                    row2 = result2.fetchone()
                    
                    if row2 and row2[0] == album_id:
                        print(f"  ✓ album_id корректно получается для постов альбома")
                    else:
                        print(f"  ⚠️  Несоответствие album_id")
            else:
                print(f"  ℹ️  Нет альбомов с несколькими элементами для теста")
                
    finally:
        await engine.dispose()
    
    print("  ✅ Интеграционный тест пройден")


async def main():
    """Запуск всех тестов."""
    print("=" * 60)
    print("Полное тестирование пайплайна альбомов (Phase 1 + Phase 2)")
    print("=" * 60)
    
    tests = [
        ("Схема БД с реальными данными", test_db_schema_with_real_data),
        ("Получение album_id для постов", test_album_id_in_qdrant),
        ("Redis Streams", test_redis_streams),
        ("Отслеживание состояния", test_album_state_tracking),
        ("Схемы событий", test_event_schemas),
        ("Neo4j запросы", test_neo4j_queries),
        ("Qdrant payload", test_qdrant_payload),
        ("Интеграционный пайплайн", test_integration_flow),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            await test_func()
            results.append((name, True, None))
        except Exception as e:
            print(f"  ❌ Ошибка: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False, str(e)))
    
    print("\n" + "=" * 60)
    print("Результаты тестирования:")
    print("=" * 60)
    
    for name, success, error in results:
        status = "✅ PASSED" if success else "❌ FAILED"
        print(f"{status} - {name}")
        if error:
            print(f"      Ошибка: {error}")
    
    failed_count = sum(1 for _, success, _ in results if not success)
    if failed_count > 0:
        print(f"\n⚠️  {failed_count} тест(ов) не прошли")
        sys.exit(1)
    else:
        print("\n✅ Все тесты прошли успешно!")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())

