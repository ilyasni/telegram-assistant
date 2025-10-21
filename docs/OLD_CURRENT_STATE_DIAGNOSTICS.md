# Репозиторий с первой версией проекта
https://github.com/ilyasni/t-bot-for-channels/tree/test-cleanup-fresh

# Диагностика текущего состояния системы

> **Версия:** 3.1  
> **Дата:** 12 октября 2025  
> **Проект:** n8n-server / Telegram Channel Parser + RAG System

## Содержание

1. [Проверка статуса сервисов](#1-проверка-статуса-сервисов)
2. [Диагностика базы данных](#2-диагностика-базы-данных)
3. [Проверка интеграций](#3-проверка-интеграций)
4. [Анализ логов](#4-анализ-логов)
5. [Мониторинг производительности](#5-мониторинг-производительности)

---

## 1. Проверка статуса сервисов

### 1.1 Docker контейнеры

```bash
# Проверить статус всех контейнеров
docker ps --filter "name=telethon" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Детальная информация
docker inspect telethon
docker inspect rag-service
docker inspect gpt2giga-proxy

# Проверить логи
docker logs telethon --tail 50
docker logs rag-service --tail 50
docker logs gpt2giga-proxy --tail 50
```

### 1.2 Health Checks

```bash
# Основной API
curl -s http://localhost:8010/health | jq

# RAG Service
curl -s http://localhost:8020/health | jq

# GigaChat Proxy
curl -s http://localhost:8090/health | jq

# Проверка зависимостей
curl -s http://localhost:8010/health/dependencies | jq
```

### 1.3 Сетевая диагностика

```bash
# Проверить сеть Docker
docker network inspect localai_default

# Проверить подключения между контейнерами
docker exec telethon ping -c 3 redis
docker exec telethon ping -c 3 qdrant
docker exec rag-service ping -c 3 gpt2giga-proxy
```

---

## 2. Диагностика базы данных

### 2.1 PostgreSQL (Supabase)

```bash
# Подключение к БД
docker exec -it supabase-db psql -U postgres -d postgres

# Проверить таблицы
\dt

# Статистика таблиц
SELECT 
    schemaname,
    tablename,
    n_tup_ins as inserts,
    n_tup_upd as updates,
    n_tup_del as deletes,
    n_live_tup as live_tuples
FROM pg_stat_user_tables 
ORDER BY n_live_tup DESC;

# Проверить индексы
SELECT 
    tablename,
    indexname,
    indexdef
FROM pg_indexes 
WHERE schemaname = 'public'
ORDER BY tablename;

# Активные подключения
SELECT 
    pid,
    usename,
    application_name,
    client_addr,
    state,
    query_start,
    query
FROM pg_stat_activity 
WHERE state = 'active';
```

### 2.2 Redis/Valkey

```bash
# Подключение к Redis
docker exec -it redis redis-cli

# Информация о сервере
INFO server

# Статистика памяти
INFO memory

# Список ключей
KEYS *

# Проверить QR сессии
KEYS "qr_session:*"

# Проверить админ сессии
KEYS "admin_session:*"

# Проверить кеш embeddings
KEYS "embedding:*"

# Проверить rate limiting
KEYS "rate:*"

# TTL ключей
TTL qr_session:abc123
TTL admin_session:xyz789
```

### 2.3 Qdrant

```bash
# Проверить коллекции
curl -s http://localhost:6333/collections | jq

# Информация о коллекции
curl -s http://localhost:6333/collections/user_123_posts | jq

# Статистика точек
curl -s http://localhost:6333/collections/user_123_posts | jq '.points_count'

# Проверить кластер
curl -s http://localhost:6333/cluster | jq
```

---

## 3. Проверка интеграций

### 3.1 GigaChat Proxy

```bash
# Проверить доступность
curl -s http://localhost:8090/health

# Тест embeddings
curl -X POST http://localhost:8090/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "EmbeddingsGigaR",
    "input": "Тестовый текст для проверки"
  }' | jq

# Проверить модели
curl -s http://localhost:8090/v1/models | jq
```

### 3.2 Searxng

```bash
# Проверить доступность
curl -s "https://searxng.produman.studio/search?q=test&format=json" | jq

# Тест поиска
curl -s "https://searxng.produman.studio/search?q=AI новости&format=json&engines=google" | jq '.results[0]'
```

### 3.3 Crawl4AI

```bash
# Проверить доступность
curl -s http://localhost:11235/health

# Тест извлечения контента
curl -X POST http://localhost:11235/crawl \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com",
    "word_count_threshold": 100
  }' | jq
```

### 3.4 n8n Webhooks

```bash
# Проверить webhook endpoints
curl -s https://n8n.produman.studio/webhook/telegram-new-post
curl -s https://n8n.produman.studio/webhook/telegram-post-tagged
curl -s https://n8n.produman.studio/webhook/telegram-post-indexed
curl -s https://n8n.produman.studio/webhook/telegram-digest-sent
```

---

## 4. Анализ логов

### 4.1 Основные логи

```bash
# Логи Telegram Bot
docker logs telethon 2>&1 | grep -E "(ERROR|WARNING|INFO)" | tail -20

# Логи RAG Service
docker logs rag-service 2>&1 | grep -E "(ERROR|WARNING|INFO)" | tail -20

# Логи парсера
docker logs telethon 2>&1 | grep "ParserService" | tail -10

# Логи тегирования
docker logs telethon 2>&1 | grep "TaggingService" | tail -10

# Логи QR авторизации
docker logs telethon 2>&1 | grep "QRAuthManager" | tail -10
```

### 4.2 Поиск ошибок

```bash
# Критические ошибки
docker logs telethon 2>&1 | grep "ERROR" | tail -20
docker logs rag-service 2>&1 | grep "ERROR" | tail -20

# Ошибки подключения к БД
docker logs telethon 2>&1 | grep -i "database\|connection\|sql" | tail -10

# Ошибки Redis
docker logs telethon 2>&1 | grep -i "redis" | tail -10

# Ошибки Qdrant
docker logs rag-service 2>&1 | grep -i "qdrant" | tail -10

# Ошибки GigaChat
docker logs rag-service 2>&1 | grep -i "gigachat\|gpt2giga" | tail -10
```

### 4.3 Анализ производительности

```bash
# Медленные запросы
docker logs telethon 2>&1 | grep -E "duration.*[5-9][0-9][0-9]ms" | tail -10

# Высокое использование памяти
docker logs telethon 2>&1 | grep -i "memory\|oom" | tail -10

# FloodWait ошибки
docker logs telethon 2>&1 | grep -i "floodwait" | tail -10
```

---

## 5. Мониторинг производительности

### 5.1 Системные ресурсы

```bash
# Использование CPU и памяти
docker stats telethon rag-service gpt2giga-proxy

# Дисковое пространство
df -h
du -sh telethon/sessions telethon/data telethon/logs

# Сетевые подключения
netstat -tulpn | grep -E "(8010|8020|8090|6379|6333)"
```

### 5.2 Метрики приложения

```bash
# Prometheus метрики (если настроены)
curl -s http://localhost:8010/metrics | grep -E "(rag_queries|posts_parsed|embeddings)"

# Статистика пользователей
curl -s http://localhost:8010/api/admin/stats/summary | jq

# Статистика каналов
curl -s http://localhost:8010/api/admin/stats/channels | jq
```

### 5.3 База данных - производительность

```sql
-- Медленные запросы
SELECT 
    query,
    calls,
    total_time,
    mean_time,
    rows
FROM pg_stat_statements 
ORDER BY mean_time DESC 
LIMIT 10;

-- Размер таблиц
SELECT 
    tablename,
    pg_size_pretty(pg_total_relation_size(tablename::regclass)) as size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(tablename::regclass) DESC;

-- Блокировки
SELECT 
    blocked_locks.pid AS blocked_pid,
    blocked_activity.usename AS blocked_user,
    blocking_locks.pid AS blocking_pid,
    blocking_activity.usename AS blocking_user,
    blocked_activity.query AS blocked_statement
FROM pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks ON blocking_locks.locktype = blocked_locks.locktype
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;
```

---

## 6. Диагностические скрипты

### 6.1 Полная диагностика системы

```bash
#!/bin/bash
# diagnostic.sh - Полная диагностика системы

echo "🔍 Диагностика Telegram Channel Parser Bot"
echo "=========================================="

echo "📊 Статус контейнеров:"
docker ps --filter "name=telethon" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

echo -e "\n🏥 Health Checks:"
echo "Main API: $(curl -s http://localhost:8010/health | jq -r '.status')"
echo "RAG Service: $(curl -s http://localhost:8020/health | jq -r '.status')"
echo "GigaChat Proxy: $(curl -s http://localhost:8090/health | jq -r '.status')"

echo -e "\n🗄️ База данных:"
docker exec supabase-db psql -U postgres -d postgres -c "SELECT COUNT(*) as users FROM users;"
docker exec supabase-db psql -U postgres -d postgres -c "SELECT COUNT(*) as channels FROM channels;"
docker exec supabase-db psql -U postgres -d postgres -c "SELECT COUNT(*) as posts FROM posts;"

echo -e "\n⚡ Redis:"
docker exec redis redis-cli INFO memory | grep used_memory_human

echo -e "\n🔍 Qdrant:"
curl -s http://localhost:6333/collections | jq '.result.collections | length'

echo -e "\n📋 Последние ошибки:"
docker logs telethon --tail 5 2>&1 | grep ERROR
docker logs rag-service --tail 5 2>&1 | grep ERROR

echo -e "\n✅ Диагностика завершена"
```

### 6.2 Проверка конкретного пользователя

```bash
#!/bin/bash
# user_diagnostic.sh - Диагностика конкретного пользователя

USER_ID=$1
if [ -z "$USER_ID" ]; then
    echo "Использование: $0 <user_id>"
    exit 1
fi

echo "🔍 Диагностика пользователя $USER_ID"
echo "=================================="

# Информация о пользователе
docker exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    telegram_id,
    username,
    role,
    subscription_type,
    is_authenticated,
    created_at
FROM users 
WHERE id = $USER_ID;
"

# Каналы пользователя
docker exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    channel_username,
    channel_title,
    is_active,
    posts_count,
    last_parsed_at
FROM channels c
JOIN user_channel uc ON c.id = uc.channel_id
WHERE uc.user_id = $USER_ID;
"

# Статистика постов
docker exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    COUNT(*) as total_posts,
    COUNT(CASE WHEN tags IS NOT NULL THEN 1 END) as tagged_posts,
    COUNT(CASE WHEN created_at > NOW() - INTERVAL '24 hours' THEN 1 END) as recent_posts
FROM posts 
WHERE user_id = $USER_ID;
"

# Статус индексации
docker exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    status,
    COUNT(*) as count
FROM indexing_status 
WHERE user_id = $USER_ID
GROUP BY status;
"

# RAG запросы
docker exec supabase-db psql -U postgres -d postgres -c "
SELECT 
    query,
    created_at
FROM rag_query_history 
WHERE user_id = $USER_ID
ORDER BY created_at DESC
LIMIT 5;
"
```

### 6.3 Мониторинг в реальном времени

```bash
#!/bin/bash
# monitor.sh - Мониторинг в реальном времени

echo "📊 Мониторинг системы в реальном времени"
echo "========================================"

while true; do
    clear
    echo "🕐 $(date)"
    echo "========================================"
    
    # Статус контейнеров
    echo "📦 Контейнеры:"
    docker ps --filter "name=telethon" --format "{{.Names}}: {{.Status}}"
    
    # Health checks
    echo -e "\n🏥 Health:"
    echo "API: $(curl -s http://localhost:8010/health | jq -r '.status' 2>/dev/null || echo 'DOWN')"
    echo "RAG: $(curl -s http://localhost:8020/health | jq -r '.status' 2>/dev/null || echo 'DOWN')"
    
    # Статистика БД
    echo -e "\n📊 Статистика:"
    USERS=$(docker exec supabase-db psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM users;" 2>/dev/null || echo "N/A")
    POSTS=$(docker exec supabase-db psql -U postgres -d postgres -t -c "SELECT COUNT(*) FROM posts;" 2>/dev/null || echo "N/A")
    echo "Users: $USERS, Posts: $POSTS"
    
    # Последние ошибки
    echo -e "\n❌ Последние ошибки:"
    docker logs telethon --tail 1 2>&1 | grep ERROR | tail -1
    docker logs rag-service --tail 1 2>&1 | grep ERROR | tail -1
    
    sleep 10
done
```

---

## 7. Troubleshooting

### 7.1 Частые проблемы

| Проблема | Симптомы | Решение |
|----------|----------|---------|
| **QR Login не работает** | "QR сессия истекла" | Проверить Redis, перезапустить telethon |
| **Парсинг падает** | "FloodWait" ошибки | Увеличить интервал парсинга |
| **RAG не отвечает** | Timeout на /rag/query | Проверить Qdrant, GigaChat proxy |
| **Admin Panel не открывается** | 403 Unauthorized | Проверить роль пользователя |
| **База данных недоступна** | Connection refused | Проверить Supabase, сеть Docker |

### 7.2 Команды восстановления

```bash
# Перезапуск сервисов
docker-compose restart telethon rag-service

# Очистка Redis
docker exec redis redis-cli FLUSHALL

# Переиндексация RAG
curl -X POST http://localhost:8020/rag/index/batch

# Проверка подключений
docker exec telethon python -c "from database import engine; print(engine.execute('SELECT 1').fetchone())"
```

---

> **Версия:** 3.1  
> **Дата:** 12 октября 2025  
> **Проект:** n8n-server / Telegram Channel Parser + RAG System
