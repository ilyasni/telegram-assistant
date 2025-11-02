# Решение проблемы "too many clients already" в PostgreSQL

## Проблема

При работе с Supabase Studio появляется ошибка:
```
Error: sorry, too many clients already
```

Это означает, что PostgreSQL достиг лимита максимальных соединений (по умолчанию 100).

## Причины

1. **Отсутствие connection pooling** - все сервисы подключаются напрямую
2. **Утечки соединений** - idle соединения не закрываются
3. **Высокий pool_size в приложениях** - SQLAlchemy/asyncpg создают большие пулы
4. **PostgREST использует множество соединений** - для каждого запроса

## Решение

### 1. Увеличение max_connections (временно)

Изменено в `docker-compose.yml`:
```yaml
supabase-db:
  environment:
    POSTGRES_MAX_CONNECTIONS: 500
```

Применение через `ALTER SYSTEM`:
```sql
ALTER SYSTEM SET max_connections = 500;
```

Требует перезапуска PostgreSQL.

### 2. Ограничение пула соединений PostgREST

Добавлено в `docker-compose.yml`:
```yaml
rest:
  environment:
    PGRST_DB_POOL: 10  # Ограничение пула соединений
```

### 3. Настройка пулов в приложениях

**Worker (worker/database.py):**
- SQLAlchemy: `pool_size=10, max_overflow=20`
- AsyncPG: `min_size=5, max_size=20`

**Telethon Ingest (telethon-ingest/main.py):**
- SQLAlchemy: `pool_size=5`

**Post Persistence Worker:**
- AsyncPG: `min_size=2, max_size=10`

### 4. Мониторинг соединений

Используйте скрипт для мониторинга:
```bash
./scripts/monitor_db_connections.sh
```

Показывает:
- Общее количество соединений
- Соединения по приложениям
- Долгие idle соединения

### 5. Закрытие idle соединений

Закрыть долгие idle соединения:
```bash
./scripts/kill_idle_connections.sh [минут]
```

По умолчанию закрывает соединения idle > 10 минут.

## Долгосрочное решение: PgBouncer

Для production рекомендуется настроить PgBouncer (connection pooler):

```yaml
# TODO: Добавить pgbouncer в docker-compose.yml
pgbouncer:
  image: edoburu/pgbouncer
  environment:
    DATABASES_HOST: supabase-db
    DATABASES_PORT: 5432
    DATABASES_USER: postgres
    DATABASES_PASSWORD: ${POSTGRES_PASSWORD}
    DATABASES_DBNAME: ${POSTGRES_DB}
    PGBOUNCER_POOL_MODE: transaction
    PGBOUNCER_MAX_CLIENT_CONN: 1000
    PGBOUNCER_DEFAULT_POOL_SIZE: 25
```

И изменить DATABASE_URL на использование порта 6543 (pgbouncer).

## Проверка решения

1. **Проверить max_connections:**
```sql
SHOW max_connections;
```

2. **Проверить текущие соединения:**
```sql
SELECT count(*) FROM pg_stat_activity;
```

3. **Проверить использование:**
```sql
SELECT 
    (SELECT count(*) FROM pg_stat_activity)::float / 
    (SELECT setting::float FROM pg_settings WHERE name = 'max_connections') * 100 
    as usage_percent;
```

## Best Practices (Context7)

1. **Используйте connection pooling** везде, где возможно
2. **Ограничивайте pool_size** в приложениях разумными значениями
3. **Закрывайте idle соединения** регулярно
4. **Мониторьте использование соединений** постоянно
5. **Настройте pgbouncer** для production окружения

## См. также

- `scripts/monitor_db_connections.sh` - мониторинг соединений
- `scripts/kill_idle_connections.sh` - закрытие idle соединений
- `scripts/fix_postgres_collation.sh` - исправление collation

