#!/usr/bin/env python3
"""
Создание индексов Neo4j для улучшения производительности.
Context7 best practice: индексы для частых операций поиска и JOIN.

Использование:
    python3 scripts/create_neo4j_indexes.py
    # или через Docker:
    docker exec telegram-assistant-neo4j-1 cypher-shell -u neo4j -p neo4j123 < <(cat scripts/neo4j_indexes.cypher)
"""

import subprocess
import sys

# Индексы для создания
INDEXES = [
    {
        "name": "post_id_index",
        "query": "CREATE INDEX post_id_index IF NOT EXISTS FOR (p:Post) ON (p.post_id);",
        "description": "Индекс для поиска постов по post_id"
    },
    {
        "name": "album_id_index",
        "query": "CREATE INDEX album_id_index IF NOT EXISTS FOR (a:Album) ON (a.album_id);",
        "description": "Индекс для поиска альбомов по album_id"
    },
    {
        "name": "entity_name_type_index",
        "query": "CREATE INDEX entity_name_type_index IF NOT EXISTS FOR (e:Entity) ON (e.name, e.type);",
        "description": "Составной индекс для поиска сущностей по name и type"
    },
    {
        "name": "channel_id_index",
        "query": "CREATE INDEX channel_id_index IF NOT EXISTS FOR (c:Channel) ON (c.channel_id);",
        "description": "Индекс для поиска каналов по channel_id"
    },
    {
        "name": "user_id_index",
        "query": "CREATE INDEX user_id_index IF NOT EXISTS FOR (u:User) ON (u.user_id);",
        "description": "Индекс для поиска пользователей по user_id"
    },
    {
        "name": "post_tenant_channel_index",
        "query": "CREATE INDEX post_tenant_channel_index IF NOT EXISTS FOR (p:Post) ON (p.tenant_id, p.channel_id);",
        "description": "Составной индекс для фильтрации по tenant_id и channel_id"
    },
    {
        "name": "album_tenant_index",
        "query": "CREATE INDEX album_tenant_index IF NOT EXISTS FOR (a:Album) ON (a.tenant_id);",
        "description": "Индекс для фильтрации альбомов по tenant_id"
    }
]


def create_indexes_via_cypher():
    """Создание индексов Neo4j через cypher-shell."""
    container = "telegram-assistant-neo4j-1"
    username = "neo4j"
    password = "neo4j123"
    
    print("🚀 Создание индексов Neo4j...")
    print("="*60)
    
    created_count = 0
    failed_count = 0
    
    for index_info in INDEXES:
        try:
            print(f"\n📊 Создание индекса: {index_info['name']}")
            print(f"   Описание: {index_info['description']}")
            
            # Подготавливаем команду для cypher-shell
            query = index_info['query'].strip()
            # Удаляем точку с запятой для cypher-shell
            if query.endswith(';'):
                query = query[:-1]
            
            cmd = [
                'docker', 'exec', container,
                'cypher-shell', '-u', username, '-p', password,
                query
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                print(f"   ✅ Индекс создан/уже существует")
                created_count += 1
            else:
                error_str = result.stderr.lower()
                if "already exists" in error_str or "equivalent" in error_str:
                    print(f"   ⏭️  Индекс уже существует")
                    created_count += 1
                else:
                    print(f"   ❌ Ошибка: {result.stderr}")
                    failed_count += 1
                    
        except subprocess.TimeoutExpired:
            print(f"   ❌ Таймаут при создании индекса")
            failed_count += 1
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
            failed_count += 1
    
    print(f"\n{'='*60}")
    print(f"📈 Итоги создания индексов:")
    print(f"   ✅ Создано/существует: {created_count}")
    print(f"   ❌ Ошибок: {failed_count}")
    print(f"{'='*60}")
    
    # Проверяем существующие индексы
    print(f"\n🔍 Проверка существующих индексов:")
    try:
        cmd = [
            'docker', 'exec', container,
            'cypher-shell', '-u', username, '-p', password,
            'SHOW INDEXES;'
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            print(f"   Всего индексов: {len([l for l in lines if l.strip() and not l.startswith('id,')])}")
            for line in lines[1:6]:  # Первые 5 индексов (после заголовка)
                if line.strip():
                    print(f"   - {line.split(',')[1] if ',' in line else line}")
    except Exception as e:
        print(f"   ⚠️  Не удалось получить список индексов: {e}")


if __name__ == "__main__":
    try:
        create_indexes_via_cypher()
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)

