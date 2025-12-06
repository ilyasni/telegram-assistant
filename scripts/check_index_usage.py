#!/usr/bin/env python3
"""
Проверка использования индексов Neo4j в запросах.
Context7 best practice: мониторинг эффективности индексов.
"""

import subprocess
import sys

def check_index_usage():
    """Проверка использования индексов через EXPLAIN."""
    
    container = "telegram-assistant-neo4j-1"
    username = "neo4j"
    password = "neo4j123"
    
    print("="*70)
    print("📊 Проверка использования индексов Neo4j")
    print("="*70)
    
    queries = [
        {
            "name": "Поиск поста по post_id",
            "query": "EXPLAIN MATCH (p:Post {post_id: $post_id}) RETURN p",
            "params": {"post_id": "test-post-id"},
            "expected_index": "post_id_index"
        },
        {
            "name": "Поиск альбома по album_id",
            "query": "EXPLAIN MATCH (a:Album {album_id: $album_id}) RETURN a",
            "params": {"album_id": "123"},
            "expected_index": "album_id_index"
        },
        {
            "name": "Поиск Entity по name и type",
            "query": "EXPLAIN MATCH (e:Entity {name: $name, type: $type}) RETURN e",
            "params": {"name": "Test", "type": "ORG"},
            "expected_index": "entity_name_type_index"
        },
        {
            "name": "Поиск канала по channel_id",
            "query": "EXPLAIN MATCH (c:Channel {channel_id: $channel_id}) RETURN c",
            "params": {"channel_id": "test-channel-id"},
            "expected_index": "channel_id_index"
        },
        {
            "name": "Фильтрация постов по tenant_id и channel_id",
            "query": "EXPLAIN MATCH (p:Post) WHERE p.tenant_id = $tenant_id AND p.channel_id = $channel_id RETURN p LIMIT 10",
            "params": {"tenant_id": "test-tenant", "channel_id": "test-channel"},
            "expected_index": "post_tenant_channel_index"
        }
    ]
    
    for query_info in queries:
        print(f"\n📊 Тест: {query_info['name']}")
        print(f"   Ожидаемый индекс: {query_info['expected_index']}")
        
        try:
            # Формируем параметры для cypher-shell
            # Для простоты используем захардкоженные значения
            query = query_info['query']
            for key, value in query_info['params'].items():
                if isinstance(value, str):
                    query = query.replace(f"${key}", f"'{value}'")
                else:
                    query = query.replace(f"${key}", str(value))
            
            cmd = [
                'docker', 'exec', container,
                'cypher-shell', '-u', username, '-p', password,
                query
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10
            )
            
            if result.returncode == 0:
                output = result.stdout.strip()
                # Проверяем, используется ли индекс
                output_lower = output.lower()
                if "indexseek" in output_lower or "nodeindexseek" in output_lower or "nodeuniqueindexseek" in output_lower:
                    print(f"   ✅ Индекс используется (IndexSeek найден)")
                    # Извлекаем имя индекса если есть
                    if query_info['expected_index'] in output:
                        print(f"      Используется индекс: {query_info['expected_index']}")
                elif "allnodesscan" in output_lower or "nodebyidseek" in output_lower:
                    print(f"   ⚠️  Полное сканирование - индекс не используется")
                else:
                    print(f"   ⏭️  План запроса получен (детали скрыты)")
                    # Для PROFILE показываем больше информации
                    if "PROFILE" in query_info['query']:
                        for line in output.split('\n')[:5]:
                            if line.strip() and not line.startswith('+'):
                                print(f"      {line.strip()}")
            else:
                print(f"   ❌ Ошибка: {result.stderr}")
                
        except subprocess.TimeoutExpired:
            print(f"   ❌ Таймаут выполнения запроса")
        except Exception as e:
            print(f"   ❌ Ошибка: {e}")
    
    print("\n" + "="*70)
    print("✅ Проверка завершена")
    print("="*70)
    print("""
Примечание:
- IndexSeek означает использование индекса (оптимально)
- AllNodesScan означает полное сканирование (неоптимально)
- Для точной проверки используйте PROFILE вместо EXPLAIN
""")


if __name__ == "__main__":
    try:
        check_index_usage()
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)

