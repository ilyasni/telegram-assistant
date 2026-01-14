# Отчет о проверке пайплайна постов и альбомов

**Дата**: 2026-01-12  
**Цель**: Проверка всего пайплайна на предмет проблем, влияющих на производительность

---

## 📊 Сводка

**Статус**: ⚠️ **Обнаружены проблемы**

**Критичные проблемы**:
1. ❌ **DLQ накопление**: 863 сообщений в Dead Letter Queues
2. ⚠️ **GigaChat Rate Limiting**: 72 ошибки HTTP 429 за час
3. ⚠️ **Telethon FloodWait**: 790 ошибок, огромные задержки (27000+ секунд)
4. ⚠️ **Crawl4AI backlog**: 165 сообщений в очереди
5. ⚠️ **Failed indexing**: 1313 постов с failed статусом

---

## 1. Redis Streams - Основные потоки

### Длина потоков (история, не проблема):
- `stream:posts:parsed`: 10001 сообщений (trim настроен)
- `stream:posts:tagged`: 10002 сообщений (trim настроен)
- `stream:posts:vision:analyzed`: 8253 сообщений
- `stream:posts:enriched`: 4 сообщения ✅
- `stream:posts:indexed`: 10002 сообщений (trim настроен)
- `stream:albums:parsed`: 1980 сообщений
- `stream:album:assembled`: 583 сообщений

### Pending сообщения:
- ✅ Все consumer groups имеют `pending = 0` (кроме одного случая)
- ⚠️ `stream:posts:vision:analyzed` → группа `album_assemblers`: **2 pending сообщения**

### Lag:
- ✅ Все группы имеют `lag = 0` (все обработано)
- ⚠️ `stream:posts:indexed` → группа `indexing_monitoring`: lag 10002 (но consumers=0, это нормально)

---

## 2. Dead Letter Queues (DLQ) - КРИТИЧНО

### ❌ Накопление сообщений в DLQ:

| DLQ Stream | Количество | Причина |
|------------|------------|---------|
| `stream:posts:parsed:dlq` | **7** | Ошибки парсинга |
| `stream:posts:tagged:dlq` | **130** | Ошибки транзакций БД (`InFailedSQLTransactionError`) |
| `stream:posts:indexed:dlq` | **726** | `ChannelIsolationError` (старые ошибки от 2025-11-09) |

**Всего**: **863 сообщения** в DLQ

### Анализ причин:

**stream:posts:tagged:dlq (130 сообщений)**:
- Причина: `asyncpg.exceptions.InFailedSQLTransactionError`
- Детали: "current transaction is aborted, commands ignored until end of transaction block"
- Дата: 2025-11-17 (старые ошибки)

**stream:posts:indexed:dlq (726 сообщений)**:
- Причина: `ChannelIsolationError`
- Детали: "Channel is not linked to tenant"
- Дата: 2025-11-09 (очень старые ошибки)

**Вывод**: Большинство ошибок в DLQ - старые (ноябрь 2025). Нужна очистка или анализ.

---

## 3. GigaChat API Rate Limiting - ВАЖНО

### ⚠️ HTTP 429: Too Many Requests

**Статистика**:
- За последний час: **72 ошибки HTTP 429**
- Метод: `/api/v1/embeddings` (генерация embeddings)
- Также: `/api/v1/chat/completions` (для trends)

**Влияние**:
- Задержки в индексации постов
- Fallback на нулевые embeddings
- Посты не индексируются в Qdrant

**Примеры ошибок**:
```
error=HTTP 429: {"detail":{"url":"https://gigachat.devices.sberbank.ru/api/v1/embeddings","error":{"status":429,"message":"Too Many Requests"}}}
embedding_fallback_zeros - используются нулевые embeddings
```

**Рекомендация**: Проверить rate limits GigaChat, возможно нужно увеличить задержки между запросами.

---

## 4. Telethon FloodWait - ВАЖНО

### ⚠️ Огромные задержки FloodWait

**Статистика**:
- За последний час: **790 ошибок**
- FloodWait: **27000+ секунд** (7.5+ часов!)

**Примеры**:
```
"A wait of 27142 seconds is required (caused by ResolveUsernameRequest)"
"A wait of 28041 seconds is required (caused by ResolveUsernameRequest)"
```

**Влияние**:
- Парсинг каналов блокируется
- Каналы не обновляются
- Новые посты не появляются

**Причина**: Превышены лимиты Telegram API для ResolveUsernameRequest.

**Рекомендация**: 
- Использовать tg_channel_id вместо username где возможно
- Увеличить интервалы между запросами
- Проверить, не слишком ли агрессивный парсинг

---

## 5. Crawl4AI Backlog

### ⚠️ Накопление в очереди crawling

- **Длина очереди**: 165 сообщений
- **Статус**: Crawl4AI работает, но не успевает обрабатывать
- **Предупреждения**: "High crawl queue backlog" в логах

**Влияние**: Обогащение постов через Crawl4AI происходит с задержкой.

---

## 6. Failed Indexing

### ⚠️ Посты с failed статусом

- **Количество**: 1313 постов в `indexing_status` с `embedding_status='failed'` или `graph_status='failed'`
- **Причина**: Связано с GigaChat rate limiting (HTTP 429)

**Влияние**: Эти посты не индексируются в Qdrant и Neo4j.

---

## 7. Статус сервисов

- ✅ **Worker**: Up 3 weeks (healthy)
- ✅ **Telethon-ingest**: Up (healthy) - но FloodWait проблемы
- ✅ **Crawl4AI**: Up 5 weeks (healthy) - но backlog
- ⚠️ **Qdrant**: Up, но curl недоступен (возможно проблема сети/портов)
- ✅ **Neo4j**: Up 5 weeks (healthy)
- ⚠️ **gpt2giga-proxy**: Проверить (ошибки health check)

---

## 8. База данных

- ✅ **Последний пост**: 2026-01-12 12:46:20 (активная работа)
- ✅ **Всего постов**: 18301
- ⚠️ **Альбомы**: Таблица не найдена или пуста

---

## 🎯 Выводы и рекомендации

### Критичные проблемы (влияют на производительность):

1. **GigaChat Rate Limiting (HTTP 429)**
   - **Приоритет**: Высокий
   - **Влияние**: Блокирует индексацию постов
   - **Решение**: Увеличить задержки между запросами, оптимизировать batch размеры

2. **Telethon FloodWait (27000+ секунд)**
   - **Приоритет**: Высокий
   - **Влияние**: Блокирует парсинг каналов
   - **Решение**: Использовать tg_channel_id, увеличить интервалы парсинга

3. **DLQ накопление (863 сообщения)**
   - **Приоритет**: Средний
   - **Влияние**: Старые ошибки, можно очистить
   - **Решение**: Анализ и очистка старых сообщений

4. **Crawl4AI backlog (165 сообщений)**
   - **Приоритет**: Средний
   - **Влияние**: Задержки в обогащении
   - **Решение**: Оптимизировать или масштабировать Crawl4AI

5. **Failed indexing (1313 постов)**
   - **Приоритет**: Средний
   - **Влияние**: Посты не в поиске
   - **Решение**: Повторная индексация после решения проблемы с rate limits

### Связь с проблемами бота:

**Возможное влияние на бота**:
- Worker занят обработкой ошибок и retry логикой
- Общие ресурсы (БД, Redis) могут быть перегружены
- Но напрямую не должно влиять на команды бота

**Рекомендация**: Эти проблемы могут создавать общую нагрузку на систему, но основная проблема с зависанием команд бота связана с таймаутами Telegram API, а не с пайплайном.

---

## 🔧 Действия

1. ✅ Проверить rate limits GigaChat и оптимизировать запросы
2. ✅ Проверить настройки Telethon (интервалы парсинга)
3. ⚠️ Рассмотреть очистку старых DLQ сообщений
4. ⚠️ Мониторить Crawl4AI backlog
5. ✅ Проверить gpt2giga-proxy health
