# Мониторинг Vision и Crawl4ai

**Дата**: 2025-11-17  
**Статус**: В процессе мониторинга

## Результаты проверки

### 1. Vision: MediaProcessor для channel messages

**Проблема**: MediaProcessor не вызывается для channel messages.

**Симптомы**:
- В логах нет "Media processed" для channel messages (только для group messages)
- Последние посты с медиа имеют `media_urls`, но `media_objects_count = 0`
- В stream `posts:vision` есть события, но они от 16 ноября (вчера), последнее от 21:04

**Диагностика**:
- MediaProcessor инициализируется в `run_scheduler_loop()` и передается в ChannelParser
- Код вызова MediaProcessor присутствует в `channel_parser.py` (строки 1555-1624)
- Но в логах нет сообщений "Media processed" для channel messages

**Возможные причины**:
1. MediaProcessor не вызывается из-за отсутствия TelegramClient
2. `message.media` проверка не проходит
3. Логирование не работает (уровень логирования слишком высокий)

**Следующие шаги**:
1. Проверить логи на наличие "TelegramClient not available" или "TelegramClientManager not available"
2. Проверить, что `message.media` не None для channel messages
3. Убедиться, что логирование работает на уровне INFO

### 2. Crawl4ai: Перезапуск `crawl_trigger`

**Проблема**: `crawl_trigger` перезапускается каждые 30 секунд.

**Симптомы**:
- Supervisor логирует: `[WARNING] supervisor: Task crawl_trigger completed unexpectedly, will be restarted`
- В stream `posts:crawl` есть события, но они старые
- В stream `posts:tagged` есть новые события (последнее от 12:57:52)
- `crawl_trigger` не обрабатывает новые события из `posts:tagged`

**Диагностика**:
- В логах нет "CrawlTriggerTask.start() called" или "CrawlTriggerTask entering main loop"
- Это означает, что либо логи не попадают в вывод, либо задача завершается до того, как успевает залогировать

**Возможные причины**:
1. Задача завершается при инициализации (до входа в главный цикл)
2. Исключение в `_initialize()` не логируется
3. Supervisor перезапускает задачу до того, как она успевает залогировать

**Следующие шаги**:
1. Проверить логи на наличие ошибок при инициализации
2. Перезапустить worker и проверить логи сразу после запуска
3. Убедиться, что логирование работает на уровне INFO

## Команды для мониторинга

### Vision

```bash
# Проверка логов MediaProcessor
docker compose logs -f telethon-ingest | grep -iE "(Media.*processed|Saved media|Media not|TelegramClient.*not)"

# Проверка постов с медиа
docker compose exec -T worker python3 << 'EOF'
import os
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

async def check():
    db_url = os.getenv("DATABASE_URL", "").replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(db_url)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        result = await session.execute(text("""
            SELECT 
                p.id,
                p.created_at,
                jsonb_array_length(COALESCE(p.media_urls, '[]'::jsonb)) as media_urls_count,
                (SELECT COUNT(*) FROM post_media_map pm WHERE pm.post_id = p.id) as media_objects_count
            FROM posts p
            WHERE p.media_urls IS NOT NULL
                AND jsonb_array_length(COALESCE(p.media_urls, '[]'::jsonb)) > 0
                AND p.created_at > NOW() - INTERVAL '2 hours'
            ORDER BY p.created_at DESC
            LIMIT 10
        """))
        for row in result:
            print(f"ID: {row[0]}, Created: {row[1]}, URLs: {row[2]}, Objects: {row[3]}")

asyncio.run(check())
EOF

# Проверка событий posts:vision
docker compose exec -T redis redis-cli XREVRANGE stream:posts:vision + - COUNT 5
```

### Crawl4ai

```bash
# Проверка логов crawl_trigger
docker compose logs -f worker | grep -iE "(crawl_trigger|CrawlTriggerTask)"

# Проверка событий posts:tagged
docker compose exec -T redis redis-cli XREVRANGE stream:posts:tagged + - COUNT 5

# Проверка событий posts:crawl
docker compose exec -T redis redis-cli XREVRANGE stream:posts:crawl + - COUNT 5
```

## Метрики для мониторинга

### Vision

- `media_processing_total{stage="parse", outcome="ok"}` - успешно обработанные медиа
- `media_processing_total{stage="parse", outcome="err"}` - ошибки обработки медиа
- `media_processing_duration_seconds{stage="parse"}` - время обработки медиа

### Crawl4ai

- `crawl_triggers_total{reason="triggered"}` - успешно сработавшие триггеры
- `crawl_trigger_queue_depth_current` - глубина очереди `posts:tagged`
- `crawl_trigger_processing_latency_seconds` - время обработки событий

## Следующие шаги

1. ✅ Добавлено детальное логирование для MediaProcessor
2. ✅ Добавлено логирование для save_media_to_cas()
3. ✅ Улучшена обработка ошибок в crawl_trigger
4. ✅ Добавлено логирование завершения crawl_trigger
5. ⏳ Мониторинг логов для диагностики проблем
6. ⏳ Проверка работы MediaProcessor для новых постов
7. ⏳ Проверка стабильности crawl_trigger

