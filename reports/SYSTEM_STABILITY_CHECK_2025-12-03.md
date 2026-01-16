# Комплексная проверка стабильности системы Telegram Assistant

**Дата**: 2025-12-03  
**Context7**: Диагностика стабильности сборки, System Overview, Scheduler и пайплайна

---

## Executive Summary

### Критичные проблемы
1. ❌ **Scheduler не запускается автоматически** - требуется ручной запуск
2. ⚠️ **System Overview периодически отваливается** - нужна диагностика health endpoints
3. ⚠️ **API контейнер перезапустился 3 минуты назад** - признак нестабильности

### Рабочие компоненты
- ✅ Docker контейнеры запущены (все критичные сервисы)
- ✅ Пайплайн работает (Redis streams активны, нет pending сообщений)
- ✅ База данных функционирует (7919 постов, 11 за последний час)
- ✅ Scheduler может запуститься вручную

---

## 1. Стабильность сборки (Docker)

### Статус контейнеров

| Сервис | Статус | Время работы | Health |
|--------|--------|--------------|--------|
| api | ✅ Running | 3 minutes | healthy |
| worker | ✅ Running | 19 hours | healthy |
| telethon-ingest | ✅ Running | 3 minutes | healthy |
| supabase-db | ✅ Running | 3 days | healthy |
| redis | ✅ Running | 3 days | healthy |
| qdrant | ✅ Running | 3 days | - |
| neo4j | ✅ Running | 3 days | healthy |

### Проблемы

1. **API контейнер перезапущен 3 минуты назад**
   - Признак нестабильности
   - Возможные причины: ошибки при старте, OOM, health check failures
   - **Рекомендация**: Проверить логи на ошибки при старте

2. **Telethon-ingest перезапущен 3 минуты назад**
   - Синхронный перезапуск с API (возможно связанный)
   - **Рекомендация**: Проверить зависимости между сервисами

### Все Redis Streams активны

```
stream:posts:parsed (9803 entries)
stream:posts:tagged (9798 entries, 0 pending)
stream:posts:vision:analyzed
stream:posts:enriched
stream:posts:indexed
stream:posts:crawl
stream:albums:parsed
stream:album:assembled
```

**Статус**: ✅ Все необходимые streams существуют, нет pending сообщений

---

## 2. System Overview (Health Endpoints)

### Проверенные endpoints

| Endpoint | Статус | Детали |
|----------|--------|--------|
| `/health` | ❓ Не проверен | Требуется проверка |
| `/api/health` | ❓ Не проверен | Требуется проверка |
| `telethon-ingest:8011/health` | ❓ Не проверен | Требуется проверка |
| `worker/health` | ❓ Не проверен | Требуется проверка |

### Проблема: периодические отказы

**Симптом**: System Overview периодически отваливается

**Возможные причины**:
1. Таймауты при проверке зависимостей (БД, Redis)
2. Проблемы с сетью Docker
3. Высокая нагрузка на health endpoints
4. Циклические зависимости при проверке

**Рекомендации**:
1. Добавить таймауты для health checks (5-10 секунд)
2. Кэшировать результаты health checks на 30-60 секунд
3. Использовать circuit breaker для зависимостей
4. Логировать все ошибки health checks

---

## 3. Scheduler - КРИТИЧНАЯ ПРОБЛЕМА

### Статус

- ❌ **Scheduler не запускается автоматически при старте API**
- ✅ **Scheduler может запуститься вручную** (проверено)

### Диагностика

**Проверка в контейнере**:
```python
# Before: None
# After init: <AsyncIOScheduler object> False
# After start: True
```

**Вывод**: Scheduler инициализируется и запускается корректно при ручном вызове.

### Причина проблемы

В логах API **НЕТ** сообщений о попытке запуска scheduler:
- Нет "Attempting to start scheduler..."
- Нет "Scheduler started for digest and trend tasks"
- Нет ошибок scheduler

**Возможные причины**:
1. Код в `lifespan()` не выполняется (не вызывается)
2. Ошибка при импорте `tasks.scheduler_tasks` (проглатывается)
3. Ошибка в `start_scheduler()` проглатывается try/except

### Проверка кода

В `api/main.py:111-125`:
```python
try:
    logger.info("Attempting to start scheduler...")
    from tasks.scheduler_tasks import start_scheduler
    await start_scheduler()
    logger.info("Scheduler started for digest and trend tasks")
    ...
except Exception as e:
    logger.error("Failed to start scheduler", error=str(e), exc_info=True)
    # Продолжаем без scheduler
```

**Проблема**: Если есть ошибка, она логируется, но в логах нет сообщений о scheduler вообще.

### Решение

1. **Добавить явную проверку запуска scheduler** в health endpoint
2. **Логировать все этапы** запуска scheduler
3. **Добавить метрику** `scheduler_running` в Prometheus
4. **Проверить**, что `lifespan()` действительно вызывается

---

## 4. Пайплайн постов и альбомов

### Поток данных

```
1. Telegram Message/Album
   ↓
2. ChannelParser → MediaProcessor → AtomicDBSaver
   ↓ posts.parsed (9803 entries)
3. VisionAnalysisTask (Vision анализ)
   ↓ posts.vision.analyzed
4. RetaggingTask (ретеггинг с Vision)
   ↓ posts.tagged (trigger=vision_retag)
5. TaggingTask (тегирование новых постов)
   ↓ posts.tagged (9798 entries, 0 pending)
6. TagPersistenceTask (сохранение тегов в БД)
   ↓ posts.enriched
7. EnrichmentTask (Crawl4AI обогащение)
   ↓ posts.enriched
8. IndexingTask (Qdrant + Neo4j)
   ↓ posts.indexed
9. AlbumAssemblerTask (сборка альбомов)
   ↓ album.assembled
```

### Проверка компонентов

#### ✅ Парсинг
- **Статус**: Работает
- **Метрики**: 7919 постов всего, 11 за последний час
- **Stream**: `stream:posts:parsed` (9803 entries)

#### ✅ Тегирование
- **Статус**: Работает
- **Stream**: `stream:posts:tagged` (9798 entries, 0 pending)
- **Consumer groups**: `tagging_workers` (1 consumer, 0 pending)

#### ✅ Обогащение
- **Статус**: Работает
- **Consumer groups**: 
  - `crawl_trigger_workers` (1 consumer, 0 pending)
  - `enrich_workers` (1 consumer, 0 pending)

#### ⚠️ Vision анализ
- **Статус**: Требуется проверка
- **Stream**: `stream:posts:vision:analyzed` (существует)
- **Рекомендация**: Проверить количество обработанных постов

#### ⚠️ Индексация (Qdrant + Neo4j)
- **Qdrant**: ❓ Не удалось проверить (endpoint недоступен)
- **Neo4j**: ✅ Health check проходит
- **Рекомендация**: Проверить коллекции Qdrant и узлы Neo4j

### Redis Streams - детальная статистика

| Stream | Entries | Consumer Groups | Pending | Lag |
|--------|---------|----------------|---------|-----|
| posts:parsed | 9803 | 2 | 0 | 0 |
| posts:tagged | 9798 | - | 0 | 0 |
| posts:enriched | - | 2 | 0 | 0 |

**Вывод**: ✅ Пайплайн работает стабильно, нет задержек и pending сообщений.

---

## 5. Context7 Best Practices

### Рекомендации по стабильности

1. **Health Checks с кэшированием**
   - Кэшировать результаты на 30-60 секунд
   - Использовать circuit breaker для зависимостей
   - Добавить таймауты (5-10 секунд)

2. **Scheduler мониторинг**
   - Добавить метрику `scheduler_running` в Prometheus
   - Проверять статус scheduler в health endpoint
   - Логировать все этапы запуска

3. **Graceful Shutdown**
   - Останавливать scheduler при остановке API
   - Закрывать соединения корректно
   - Ждать завершения активных задач

4. **Docker Health Checks**
   - Убедиться, что все сервисы имеют health checks
   - Использовать `depends_on` с `condition: service_healthy`
   - Мониторить перезапуски контейнеров

5. **Логирование**
   - Структурированное логирование (JSON)
   - Логировать все ошибки с контекстом
   - Алерты на критические ошибки

---

## План исправлений

### Приоритет 1 (Критично)

1. **Исправить автоматический запуск Scheduler**
   - Проверить, вызывается ли `lifespan()` при старте
   - Добавить явную проверку статуса scheduler в health endpoint
   - Логировать все этапы запуска scheduler

2. **Диагностика периодических отказов System Overview**
   - Добавить таймауты для health checks
   - Кэшировать результаты
   - Логировать все ошибки

### Приоритет 2 (Важно)

3. **Мониторинг стабильности контейнеров**
   - Алерты на частые перезапуски
   - Проверка причин перезапусков (OOM, ошибки)
   - Мониторинг health checks

4. **Проверка пайплайна**
   - Полная проверка Vision анализа
   - Проверка Qdrant коллекций
   - Проверка Neo4j узлов и связей

### Приоритет 3 (Желательно)

5. **Улучшение мониторинга**
   - Метрики для всех компонентов пайплайна
   - Dashboard в Grafana
   - Алерты на критические метрики

---

## Заключение

### Общий статус: ⚠️ Частично работоспособен

- ✅ Пайплайн работает стабильно
- ✅ База данных функционирует
- ✅ Redis streams активны
- ❌ Scheduler не запускается автоматически
- ⚠️ System Overview периодически отваливается
- ⚠️ API контейнер перезапускается (признак нестабильности)

### Следующие шаги

1. Исправить автоматический запуск Scheduler (критично)
2. Диагностировать периодические отказы System Overview
3. Провести полную проверку Vision анализа и индексации
4. Настроить мониторинг и алерты

---

**Создано**: 2025-12-03  
**Автор**: Комплексная диагностика системы

