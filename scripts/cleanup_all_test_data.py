#!/usr/bin/env python3
"""
Скрипт для полной очистки всех тестовых данных и застрявших очередей.

Использует Context7 best practices для:
- PostgreSQL массовых удалений с FK constraints
- Redis Streams очистки (XAUTOCLAIM, XTRIM, XDEL)
- Qdrant удаления коллекций
- Neo4j массовых операций удаления
- Prometheus TSDB очистки метрик

Сохраняет:
- users, identities, tenants
- telegram_sessions (активные сессии)
- channels (каналы пользователей)
- user_channel (подписки на каналы)
- Grafana дашборды и конфигурацию (только метрики в Prometheus удаляются)

ВАЖНО: Grafana использует Prometheus как источник данных.
После очистки Prometheus, исторические метрики в Grafana исчезнут,
но дашборды, конфигурация и datasources останутся нетронутыми.
"""

import asyncio
import os
import sys
import argparse
from typing import Dict, List, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
import structlog
import redis.asyncio as redis
from qdrant_client import QdrantClient as QdrantSDK
from qdrant_client.http.exceptions import UnexpectedResponse
from neo4j import AsyncGraphDatabase
try:
    import httpx
except ImportError:
    httpx = None  # Опциональная зависимость для Prometheus

# Добавляем путь к проекту
sys.path.append('/opt/telegram-assistant')

logger = structlog.get_logger()

# ============================================================================
# КОНФИГУРАЦИЯ
# ============================================================================

# Redis Streams из worker/event_bus.py
REDIS_STREAMS = [
    'stream:posts:parsed',
    'stream:posts:tagged',
    'stream:posts:enriched',
    'stream:posts:indexed',
    'stream:posts:crawl',
    'stream:posts:deleted',
    'stream:posts:vision:uploaded',
    'stream:posts:vision:analyzed',
    'stream:albums:parsed',
    'stream:album:assembled',
    # DLQ стримы
    'stream:posts:parsed:dlq',
    'stream:posts:tagged:dlq',
    'stream:posts:enriched:dlq',
    'stream:posts:indexed:dlq',
    'stream:posts:crawl:dlq',
    'stream:posts:deleted:dlq',
    'stream:posts:vision:analyzed:dlq',
    'stream:albums:parsed:dlq',
    'stream:album:assembled:dlq',
]

# Таблицы для очистки в правильном порядке (с учетом FK dependencies)
POSTGRES_TABLES = [
    # Зависимые таблицы от posts (удаляем сначала)
    "post_media_map",
    "post_reactions",
    "post_forwards",
    "post_replies",
    "post_media",
    "post_enrichment",
    "indexing_status",
    # Альбомы (зависит от posts и channels/users)
    "media_group_items",
    "media_groups",
    # Основная таблица постов (в конце)
    "posts",
]

# Тестовые каналы для очистки (Context7: идентификация по паттернам)
TEST_CHANNEL_PATTERNS = [
    ("title", "Test E2E Channel"),
    ("username", "test_e2e_channel"),
]

# ============================================================================
# POSTGRESQL ОЧИСТКА
# ============================================================================

async def cleanup_postgres(
    db_url: str,
    dry_run: bool = False
) -> Dict[str, Dict[str, int]]:
    """
    Context7 best practice: Использование транзакций для атомарности.
    Удаление в правильном порядке с учетом FK constraints.
    """
    logger.info("Начинаем очистку PostgreSQL", dry_run=dry_run)
    
    engine = create_async_engine(db_url)
    stats = {"before": {}, "after": {}}
    
    try:
        async with AsyncSession(engine) as session:
            # Проверяем количество записей перед удалением
            logger.info("Собираем статистику ДО очистки...")
            for table in POSTGRES_TABLES:
                try:
                    result = await session.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    )
                    count = result.scalar()
                    stats["before"][table] = count
                    logger.info(f"Таблица {table}: {count} записей")
                except Exception as e:
                    logger.warning(f"Не удалось получить статистику для {table}", error=str(e))
                    stats["before"][table] = 0
            
            if dry_run:
                logger.info("DRY-RUN: Пропускаем удаление данных")
                stats["after"] = stats["before"].copy()
                return stats
            
            # Context7: Использование транзакции для атомарности
            logger.info("Начинаем транзакционное удаление...")
            
            for table in POSTGRES_TABLES:
                try:
                    logger.info(f"Очищаем таблицу {table}...")
                    
                    # Context7: DELETE в транзакции для безопасности
                    await session.execute(text(f"DELETE FROM {table}"))
                    
                    # Проверяем результат
                    result = await session.execute(
                        text(f"SELECT COUNT(*) FROM {table}")
                    )
                    remaining = result.scalar()
                    stats["after"][table] = remaining
                    
                    deleted = stats["before"][table] - remaining
                    logger.info(
                        f"Таблица {table}: удалено {deleted}, осталось {remaining}"
                    )
                    
                except Exception as e:
                    logger.error(f"Ошибка при очистке таблицы {table}", error=str(e))
                    await session.rollback()
                    raise
            
            # Очищаем поле grouped_id в posts (должно быть NULL после удаления media_groups)
            try:
                await session.execute(
                    text("UPDATE posts SET grouped_id = NULL WHERE grouped_id IS NOT NULL")
                )
                logger.info("Очищено поле posts.grouped_id")
            except Exception as e:
                logger.warning(f"Не удалось очистить posts.grouped_id", error=str(e))
            
            # Context7: Очистка тестовых каналов (после удаления зависимых данных)
            try:
                logger.info("Очищаем тестовые каналы...")
                
                # Сначала удаляем связанные данные (user_channel, posts уже удалены выше)
                # Но нужно удалить user_channel для тестовых каналов
                test_channels_result = await session.execute(text("""
                    SELECT id FROM channels 
                    WHERE title = 'Test E2E Channel' OR username = 'test_e2e_channel'
                """))
                test_channel_ids = [row[0] for row in test_channels_result.fetchall()]
                
                if test_channel_ids:
                    logger.info(f"Найдено {len(test_channel_ids)} тестовых каналов")
                    
                    # Удаляем user_channel для тестовых каналов
                    await session.execute(text("""
                        DELETE FROM user_channel 
                        WHERE channel_id = ANY(:channel_ids)
                    """), {"channel_ids": test_channel_ids})
                    
                    # Удаляем тестовые каналы
                    deleted_channels = await session.execute(text("""
                        DELETE FROM channels 
                        WHERE title = 'Test E2E Channel' OR username = 'test_e2e_channel'
                        RETURNING id
                    """))
                    deleted_count = len(deleted_channels.fetchall())
                    logger.info(f"Удалено {deleted_count} тестовых каналов")
                else:
                    logger.info("Тестовые каналы не найдены")
                    
            except Exception as e:
                logger.warning(f"Не удалось очистить тестовые каналы", error=str(e))
            
            # Коммитим транзакцию
            await session.commit()
            
            logger.info("PostgreSQL очистка завершена успешно")
            
    except Exception as e:
        logger.error("Ошибка при очистке PostgreSQL", error=str(e))
        raise
    finally:
        await engine.dispose()
    
    return stats

# ============================================================================
# REDIS STREAMS ОЧИСТКА
# ============================================================================

async def cleanup_redis_streams(
    redis_url: str,
    dry_run: bool = False
) -> Dict[str, Dict[str, int]]:
    """
    Context7 best practice: Очистка Redis Streams через XTRIM, XAUTOCLAIM, XACK.
    """
    logger.info("Начинаем очистку Redis Streams", dry_run=dry_run)
    
    redis_client = None
    stats = {}
    
    try:
        redis_client = await redis.from_url(redis_url, decode_responses=True)
        
        # Проверяем подключение
        await redis_client.ping()
        logger.info("Подключение к Redis установлено")
        
        for stream_name in REDIS_STREAMS:
            try:
                # Проверяем существование стрима
                stream_length = await redis_client.xlen(stream_name)
                
                if stream_length == 0:
                    logger.info(f"Стрим {stream_name}: пуст, пропускаем")
                    stats[stream_name] = {"before": 0, "after": 0}
                    continue
                
                stats[stream_name] = {"before": stream_length, "after": 0}
                logger.info(f"Стрим {stream_name}: {stream_length} сообщений")
                
                if dry_run:
                    logger.info(f"DRY-RUN: Пропускаем очистку {stream_name}")
                    stats[stream_name]["after"] = stream_length
                    continue
                
                # Context7: Очистка PEL через XAUTOCLAIM для всех consumer groups
                # Сначала получаем список групп для этого стрима
                try:
                    groups_info = await redis_client.xinfo_groups(stream_name)
                    
                    for group_info in groups_info:
                        group_name = group_info['name']
                        pending_count = group_info.get('pending', 0)
                        
                        if pending_count > 0:
                            logger.info(
                                f"Группа {group_name} в {stream_name}: "
                                f"{pending_count} pending сообщений"
                            )
                            
                            # Context7: XAUTOCLAIM для очистки старых pending сообщений
                            # Используем минимальный idle time (0) для очистки всех
                            claimed = await redis_client.xautoclaim(
                                stream_name,
                                group_name,
                                "cleanup_worker",
                                min_idle_time=0,
                                start_id="0-0",
                                count=100
                            )
                            
                            # ACK всех claimed сообщений
                            if claimed and len(claimed) > 1:
                                message_ids = claimed[1]
                                if message_ids:
                                    await redis_client.xack(stream_name, group_name, *message_ids)
                                    logger.info(
                                        f"Очищено {len(message_ids)} pending сообщений "
                                        f"из группы {group_name}"
                                    )
                
                except Exception as e:
                    logger.warning(
                        f"Не удалось очистить PEL для {stream_name}",
                        error=str(e)
                    )
                
                # Context7: XTRIM для очистки стрима (удаляем все сообщения)
                # Используем MAXLEN 0 для полной очистки
                await redis_client.xtrim(stream_name, maxlen=0, approximate=False)
                
                # Проверяем результат
                final_length = await redis_client.xlen(stream_name)
                stats[stream_name]["after"] = final_length
                
                logger.info(
                    f"Стрим {stream_name}: очищено "
                    f"{stream_length - final_length} сообщений"
                )
                
            except Exception as e:
                # Если стрим не существует, это нормально
                if "no such key" in str(e).lower():
                    logger.debug(f"Стрим {stream_name} не существует, пропускаем")
                    stats[stream_name] = {"before": 0, "after": 0}
                else:
                    logger.error(f"Ошибка при очистке стрима {stream_name}", error=str(e))
                    # Продолжаем работу с другими стримами
        
        logger.info("Redis Streams очистка завершена")
        
    except Exception as e:
        logger.error("Ошибка при очистке Redis Streams", error=str(e))
        raise
    finally:
        if redis_client:
            await redis_client.close()
    
    return stats

# ============================================================================
# QDRANT ОЧИСТКА
# ============================================================================

async def cleanup_qdrant(
    qdrant_url: str,
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Context7 best practice: Полное удаление всех коллекций через API.
    """
    logger.info("Начинаем очистку Qdrant", dry_run=dry_run)
    
    stats = {}
    
    try:
        # Qdrant SDK синхронный, но мы можем обернуть в asyncio
        client = QdrantSDK(url=qdrant_url)
        
        # Получаем список всех коллекций
        collections = client.get_collections()
        
        collection_names = [col.name for col in collections.collections]
        logger.info(f"Найдено коллекций: {len(collection_names)}")
        
        for collection_name in collection_names:
            try:
                collection_info = client.get_collection(collection_name)
                points_count = collection_info.points_count
                
                stats[collection_name] = points_count
                logger.info(
                    f"Коллекция {collection_name}: {points_count} точек"
                )
                
                if dry_run:
                    logger.info(f"DRY-RUN: Пропускаем удаление {collection_name}")
                    continue
                
                # Context7: Удаление коллекции через DELETE /collections/{name}
                client.delete_collection(collection_name)
                logger.info(f"Коллекция {collection_name} удалена")
                
            except UnexpectedResponse:
                logger.warning(f"Коллекция {collection_name} уже не существует")
            except Exception as e:
                logger.error(
                    f"Ошибка при удалении коллекции {collection_name}",
                    error=str(e)
                )
        
        logger.info("Qdrant очистка завершена")
        
    except Exception as e:
        logger.error("Ошибка при очистке Qdrant", error=str(e))
        raise
    
    return stats

# ============================================================================
# NEO4J ОЧИСТКА
# ============================================================================

async def cleanup_prometheus(
    prometheus_url: str = "http://prometheus:9090",
    dry_run: bool = False
) -> Dict[str, str]:
    """
    Context7 best practice: Очистка Prometheus TSDB через Admin API.
    Удаляет все метрики из хранилища.
    
    Примечание: Grafana использует Prometheus как источник данных.
    После очистки Prometheus, Grafana не будет показывать исторические метрики,
    но дашборды и конфигурация останутся.
    """
    logger.info("Начинаем очистку Prometheus TSDB", dry_run=dry_run)
    
    stats = {}
    
    try:
        if httpx is None:
            logger.warning("httpx не установлен, очистка Prometheus недоступна")
            stats["error"] = "httpx не установлен"
            stats["status"] = "skipped"
            return stats
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Проверяем доступность Prometheus
            try:
                response = await client.get(f"{prometheus_url}/api/v1/status/config")
                response.raise_for_status()
                logger.info("Подключение к Prometheus установлено")
            except Exception as e:
                logger.warning(f"Не удалось подключиться к Prometheus: {e}")
                stats["error"] = str(e)
                return stats
            
            if dry_run:
                logger.info("DRY-RUN: Пропускаем очистку Prometheus TSDB")
                stats["status"] = "dry_run"
                return stats
            
            # Context7: Очистка через Admin API
            # Используем /api/v1/admin/tsdb/clean_tombstones для удаления tombstone markers
            # Для полной очистки нужно использовать /api/v1/admin/tsdb/delete_series
            
            # Удаляем все серии метрик
            # Context7: Prometheus Admin API требует правильный формат параметров
            logger.info("Удаление всех серий метрик...")
            
            # Context7: Prometheus Admin API delete_series требует правильный формат
            # Используем GET запрос с параметрами match[] (POST тоже поддерживается, но GET более надежен)
            try:
                # Вариант 1: GET с параметрами match[]
                delete_response = await client.get(
                    f"{prometheus_url}/api/v1/admin/tsdb/delete_series",
                    params={"match[]": '{__name__=~".+"}'},  # Удаляем все метрики
                    timeout=60.0
                )
                
                if delete_response.status_code != 200:
                    # Вариант 2: POST запрос
                    logger.info("Пробуем POST запрос...")
                    delete_response = await client.post(
                        f"{prometheus_url}/api/v1/admin/tsdb/delete_series",
                        params={"match[]": '{__name__=~".+"}'},
                        timeout=60.0
                    )
            except Exception as e:
                logger.warning(f"Ошибка при удалении метрик через Admin API: {e}")
                # Context7: Альтернативный подход - очистка через удаление volume
                # Но это требует доступа к Docker, поэтому оставляем как есть
                stats["error"] = f"Admin API недоступен: {str(e)}"
                stats["status"] = "partial"
                logger.warning(
                    "Prometheus Admin API недоступен. "
                    "Метрики останутся до истечения retention (200h). "
                    "Для полной очистки можно удалить volume prometheus_data"
                )
                return stats
            
            if delete_response.status_code == 200:
                logger.info("Серии метрик удалены, выполняется cleanup tombstones...")
                stats["delete_series"] = "success"
                
                # Очищаем tombstone markers
                cleanup_response = await client.post(
                    f"{prometheus_url}/api/v1/admin/tsdb/clean_tombstones",
                    timeout=60.0
                )
                
                if cleanup_response.status_code == 200:
                    logger.info("Prometheus TSDB очищен")
                    stats["clean_tombstones"] = "success"
                    stats["status"] = "completed"
                else:
                    logger.warning(
                        f"Ошибка при очистке tombstones: {cleanup_response.status_code}"
                    )
                    stats["clean_tombstones"] = f"error_{cleanup_response.status_code}"
            else:
                logger.warning(
                    f"Ошибка при удалении серий: {delete_response.status_code}"
                )
                stats["delete_series"] = f"error_{delete_response.status_code}"
                stats["status"] = "partial"
                
    except Exception as e:
        logger.error("Ошибка при очистке Prometheus", error=str(e))
        stats["error"] = str(e)
        stats["status"] = "failed"
    
    return stats

async def cleanup_neo4j(
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    dry_run: bool = False
) -> Dict[str, int]:
    """
    Context7 best practice: Полная очистка всех узлов и отношений через MATCH (n) DETACH DELETE n.
    """
    logger.info("Начинаем очистку Neo4j", dry_run=dry_run)
    
    stats = {}
    driver = None
    
    try:
        driver = AsyncGraphDatabase.driver(
            neo4j_uri,
            auth=(neo4j_user, neo4j_password)
        )
        
        # Проверяем подключение
        await driver.verify_connectivity()
        logger.info("Подключение к Neo4j установлено")
        
        async with driver.session() as session:
            # Считаем узлы и отношения перед удалением
            result = await session.run("MATCH (n) RETURN count(n) as node_count")
            record = await result.single()
            node_count = record["node_count"] if record else 0
            
            result = await session.run("MATCH ()-[r]->() RETURN count(r) as rel_count")
            record = await result.single()
            rel_count = record["rel_count"] if record else 0
            
            stats["nodes"] = node_count
            stats["relationships"] = rel_count
            
            logger.info(
                f"Найдено узлов: {node_count}, отношений: {rel_count}"
            )
            
            if dry_run:
                logger.info("DRY-RUN: Пропускаем удаление данных")
                return stats
            
            # Context7: MATCH (n) DETACH DELETE n - удаляет все узлы и их отношения
            result = await session.run("MATCH (n) DETACH DELETE n RETURN count(n) as deleted")
            record = await result.single()
            deleted_count = record["deleted"] if record else 0
            
            logger.info(f"Удалено узлов и отношений: {deleted_count}")
            
            # Проверяем результат
            result = await session.run("MATCH (n) RETURN count(n) as node_count")
            record = await result.single()
            remaining_nodes = record["node_count"] if record else 0
            
            result = await session.run("MATCH ()-[r]->() RETURN count(r) as rel_count")
            record = await result.single()
            remaining_rels = record["rel_count"] if record else 0
            
            stats["nodes_after"] = remaining_nodes
            stats["relationships_after"] = remaining_rels
            
            logger.info(
                f"Осталось узлов: {remaining_nodes}, отношений: {remaining_rels}"
            )
        
        logger.info("Neo4j очистка завершена")
        
    except Exception as e:
        logger.error("Ошибка при очистке Neo4j", error=str(e))
        raise
    finally:
        if driver:
            await driver.close()
    
    return stats

# ============================================================================
# ГЛАВНАЯ ФУНКЦИЯ
# ============================================================================

async def main():
    """Основная функция."""
    parser = argparse.ArgumentParser(
        description="Очистка всех тестовых данных и застрявших очередей"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Режим проверки без реального удаления данных"
    )
    parser.add_argument(
        "--skip-postgres",
        action="store_true",
        help="Пропустить очистку PostgreSQL"
    )
    parser.add_argument(
        "--skip-redis",
        action="store_true",
        help="Пропустить очистку Redis Streams"
    )
    parser.add_argument(
        "--skip-qdrant",
        action="store_true",
        help="Пропустить очистку Qdrant"
    )
    parser.add_argument(
        "--skip-neo4j",
        action="store_true",
        help="Пропустить очистку Neo4j"
    )
    parser.add_argument(
        "--skip-prometheus",
        action="store_true",
        help="Пропустить очистку Prometheus метрик"
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Автоматически подтвердить удаление (без интерактивного подтверждения)"
    )
    
    args = parser.parse_args()
    
    # Получаем настройки из переменных окружения
    # Конвертируем синхронный URL в асинхронный если нужно
    db_url_env = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres"
    )
    # Если URL синхронный (postgresql://), заменяем на asyncpg
    if db_url_env.startswith("postgresql://") and "+asyncpg" not in db_url_env:
        db_url = db_url_env.replace("postgresql://", "postgresql+asyncpg://", 1)
    else:
        db_url = db_url_env
    redis_url = os.getenv("REDIS_URL", "redis://redis:6379")
    qdrant_url = os.getenv("QDRANT_URL", "http://qdrant:6333")
    neo4j_uri = os.getenv("NEO4J_URI") or os.getenv("NEO4J_URL", "neo4j://neo4j:7687")
    neo4j_user = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password = os.getenv("NEO4J_PASSWORD", "neo4j123")
    prometheus_url = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")
    
    print("=" * 80)
    print("ОЧИСТКА ТЕСТОВЫХ ДАННЫХ И ЗАСТРЯВШИХ ОЧЕРЕДЕЙ")
    print("=" * 80)
    print(f"Режим: {'DRY-RUN (проверка без удаления)' if args.dry_run else 'РЕАЛЬНОЕ УДАЛЕНИЕ'}")
    print(f"PostgreSQL: {'Пропущено' if args.skip_postgres else 'Включено'}")
    print(f"Redis: {'Пропущено' if args.skip_redis else 'Включено'}")
    print(f"Qdrant: {'Пропущено' if args.skip_qdrant else 'Включено'}")
    print(f"Neo4j: {'Пропущено' if args.skip_neo4j else 'Включено'}")
    print(f"Prometheus: {'Пропущено' if args.skip_prometheus else 'Включено'}")
    print(f"\n⚠️  ВАЖНО: Grafana использует Prometheus как источник данных.")
    print(f"    После очистки Prometheus, исторические метрики в Grafana исчезнут,")
    print(f"    но дашборды и конфигурация останутся.")
    print("=" * 80)
    
    if not args.dry_run and not args.yes:
        try:
            response = input("\n⚠️  ВНИМАНИЕ: Будет выполнено реальное удаление данных! Продолжить? (yes/no): ")
            if response.lower() != "yes":
                print("Отменено пользователем")
                sys.exit(0)
        except EOFError:
            # Нет интерактивного ввода (например, в docker exec -T)
            print("\n⚠️  Нет интерактивного ввода. Используйте --yes для автоматического подтверждения.")
            sys.exit(1)
    
    all_stats = {}
    
    try:
        # Очистка PostgreSQL
        if not args.skip_postgres:
            print("\n📊 Очистка PostgreSQL...")
            all_stats["postgresql"] = await cleanup_postgres(db_url, args.dry_run)
        else:
            print("\n⏭️  Пропуск очистки PostgreSQL")
        
        # Очистка Redis Streams
        if not args.skip_redis:
            print("\n📊 Очистка Redis Streams...")
            all_stats["redis"] = await cleanup_redis_streams(redis_url, args.dry_run)
        else:
            print("\n⏭️  Пропуск очистки Redis Streams")
        
        # Очистка Qdrant
        if not args.skip_qdrant:
            print("\n📊 Очистка Qdrant...")
            all_stats["qdrant"] = await cleanup_qdrant(qdrant_url, args.dry_run)
        else:
            print("\n⏭️  Пропуск очистки Qdrant")
        
        # Очистка Neo4j
        if not args.skip_neo4j:
            print("\n📊 Очистка Neo4j...")
            all_stats["neo4j"] = await cleanup_neo4j(
                neo4j_uri, neo4j_user, neo4j_password, args.dry_run
            )
        else:
            print("\n⏭️  Пропуск очистки Neo4j")
        
        # Очистка Prometheus
        if not args.skip_prometheus:
            print("\n📊 Очистка Prometheus метрик...")
            all_stats["prometheus"] = await cleanup_prometheus(
                prometheus_url, args.dry_run
            )
        else:
            print("\n⏭️  Пропуск очистки Prometheus")
        
        # Итоговая статистика
        print("\n" + "=" * 80)
        print("ИТОГОВАЯ СТАТИСТИКА")
        print("=" * 80)
        
        if "postgresql" in all_stats:
            print("\nPostgreSQL:")
            for table, before_count in all_stats["postgresql"]["before"].items():
                after_count = all_stats["postgresql"]["after"].get(table, 0)
                deleted = before_count - after_count
                print(f"  {table}: {before_count} → {after_count} (удалено: {deleted})")
        
        if "redis" in all_stats:
            print("\nRedis Streams:")
            for stream, stream_stats in all_stats["redis"].items():
                before = stream_stats.get("before", 0)
                after = stream_stats.get("after", 0)
                deleted = before - after
                if before > 0 or deleted > 0:
                    print(f"  {stream}: {before} → {after} (удалено: {deleted})")
        
        if "qdrant" in all_stats:
            print("\nQdrant:")
            total_points = sum(all_stats["qdrant"].values())
            print(f"  Всего точек в коллекциях: {total_points}")
            print(f"  Коллекций: {len(all_stats['qdrant'])}")
        
        if "neo4j" in all_stats:
            print("\nNeo4j:")
            nodes = all_stats["neo4j"].get("nodes", 0)
            rels = all_stats["neo4j"].get("relationships", 0)
            nodes_after = all_stats["neo4j"].get("nodes_after", 0)
            rels_after = all_stats["neo4j"].get("relationships_after", 0)
            print(f"  Узлы: {nodes} → {nodes_after} (удалено: {nodes - nodes_after})")
            print(f"  Отношения: {rels} → {rels_after} (удалено: {rels - rels_after})")
        
        if "prometheus" in all_stats:
            print("\nPrometheus:")
            status = all_stats["prometheus"].get("status", "unknown")
            if status == "completed":
                print("  ✅ Все метрики удалены из TSDB")
                print("  ⚠️  Grafana больше не будет показывать исторические метрики")
            elif status == "dry_run":
                print("  🔍 DRY-RUN: Пропущено")
            else:
                print(f"  ⚠️  Статус: {status}")
                if "error" in all_stats["prometheus"]:
                    print(f"  Ошибка: {all_stats['prometheus']['error']}")
        
        print("\n" + "=" * 80)
        
        if args.dry_run:
            print("✅ DRY-RUN завершён успешно. Для реального удаления запустите без --dry-run")
        else:
            print("✅ Очистка завершена успешно!")
        
    except Exception as e:
        logger.error("Критическая ошибка", error=str(e))
        print(f"\n❌ Критическая ошибка: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())

