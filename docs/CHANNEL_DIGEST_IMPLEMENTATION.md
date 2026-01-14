# Реализация дайджеста по каналу с выбором периода

**Дата**: 2025-12-19  
**Статус**: ✅ Реализовано и протестировано  
**Context7**: Соответствует best practices

## Контекст

Реализован функционал генерации дайджеста по конкретному каналу с возможностью выбора периода анализа (1, 7 или 30 дней). Пользователь может запросить дайджест для канала, который давно не читал, чтобы узнать, что интересного там было.

## Архитектурные решения (Context7)

### 1. User-scoped Endpoint

**Endpoint**: `POST /api/users/{user_id}/channels/{channel_id}/digest?period={period}`

- Соответствует паттерну API проекта (`/users/{user_id}/channels/...`)
- Жесткая валидация доступа через `user_channel` JOIN с проверкой `tenant_id`
- Все логи и метрики включают `tenant_id`
- Минимизирует риск утечки данных между tenant'ами

### 2. Гибридное ранжирование постов

**Кандидаты (из БД)**:
- Фильтр: `posted_at >= now - period_days`
- Сортировка: `engagement_score DESC` + бонус за свежесть (до 10%)

**Diversity фильтр**:
- Не более 3 постов с одинаковым тегом
- Дедупликация по URL (репосты одного контента)
- Равномерная временная выборка

**LLM rerank** (только для month):
- Применяется только к top-20 кандидатам
- Снижает стоимость при сохранении качества

### 3. Токен-бюджет подход

**Оценка токенов**: `len(text) / 4` (грубая оценка для русского текста)

**Адаптивный бюджет контекста**:
- День: до 7K токенов, ~600 символов/пост
- Неделя: до 7K токенов, ~500 символов/пост
- Месяц: до 25-30K токенов (для 32K модели), ~400 символов/пост

### 4. Двухступенчатый саммари для месяца (map-reduce)

**Stage A (группировка и саммари по чанкам)**:
- Группировка постов по неделям (или по 3-5 дней для очень активных каналов)
- Генерация коротких саммари для каждого чанка через LLM

**Stage B (финальный дайджест)**:
- Executive summary + highlights из результатов Stage A
- Стабилизирует стоимость и снижает риск переполнения контекста

### 5. Sync vs Async выполнение

**День/неделя**: синхронный вызов (быстро, < 30 сек)

**Месяц**: пока синхронно (TODO: async job в будущем)

### 6. Облегченный формат дайджеста

**Структура**:
- Executive Summary (3-6 буллетов)
- Топ-10 важных постов (заголовок / 1-2 строки / почему важно / ссылка)
- Тренды/повторяющиеся темы (3-5 пунктов)

**Отличие от тематического**: более практичный, соответствует use case "давно не читал канал"

### 7. Кеширование и персистентное хранение

**Redis кеш**:
- Ключ: `channel_digest:{tenant_id}:{user_id}:{channel_id}:{period_days}:{window_end_date}`
- TTL: день (2 часа), неделя (8 часов), месяц (24 часа)

**Postgres**:
- Сохранение в `digest_history` для аналитики и повторного использования

### 8. Метрики Prometheus (низкая кардинальность)

- `channel_digest_generation_total{tenant_id, period}` - счетчик запросов
- `channel_digest_generation_duration_seconds{tenant_id, period}` - гистограмма времени
- `channel_digest_posts_count{tenant_id, period}` - gauge с количеством постов
- `channel_digest_context_tokens{tenant_id, period}` - gauge с размером контекста
- `channel_digest_cache_hits_total{tenant_id, period}` - счетчик попаданий в кеш

**Важно**: Без `channel_id` в labels для предотвращения взрыва кардинальности.

## Реализованные компоненты

### 1. DigestService методы

**Файл**: `api/services/digest_service.py`

#### Методы сбора и обработки:
- `_collect_channel_posts_for_digest()` - гибридное ранжирование, diversity фильтр
- `_llm_rerank_posts()` - rerank для top-20 (для месяца)
- `_assemble_channel_context()` - токен-бюджет подход
- `_generate_map_reduce_digest()` - двухступенчатый саммари для месяца
- `_estimate_tokens()` - оценка токенов

#### Методы кеширования:
- `_get_cache_key()` - генерация ключа кеша
- `_get_cache_ttl()` - получение TTL в зависимости от периода
- `_get_cached_digest()` - получение из кеша (поддержка sync/async Redis)
- `_save_to_cache()` - сохранение в кеш

#### Основной метод:
- `generate_channel_digest()` - генерация дайджеста с полной логикой

#### Промпты:
- `channel_digest_prompt` - основной промпт (для дня/недели)
- `channel_digest_map_prompt` - промпт для Stage A (саммари по чанкам)
- `channel_digest_reduce_prompt` - промпт для Stage B (финальный дайджест)

### 2. API Endpoint

**Файл**: `api/routers/channels.py`

**Endpoint**: `POST /api/users/{user_id}/channels/{channel_id}/digest`

**Параметры**:
- `user_id` (path) - UUID или telegram_id пользователя
- `channel_id` (path) - UUID канала
- `period` (query) - период в днях (1, 7 или 30), по умолчанию 7

**Валидация**:
- Проверка доступа через `user_channel` JOIN с проверкой `tenant_id`
- Валидация периода через Pydantic Query (ge=1, le=30)
- Проверка активности канала

**Response**: `ChannelDigestResponse` с полями:
- `digest_id` - ID дайджеста (опционально)
- `status` - статус ("completed" или "processing" для async)
- `content` - содержимое дайджеста
- `posts_count` - количество проанализированных постов
- `period_days` - период анализа
- `generated_at` - время генерации
- `job_id` - ID async job (для будущей реализации)

### 3. Bot Integration

**Файл**: `api/bot/handlers/base.py`

#### Клавиатуры:
- Обновлена `_kb_channel_actions()` - добавлена кнопка "📰 Дайджест"
- `_kb_channel_digest_period()` - клавиатура выбора периода

#### Обработчики:
- `on_channel_digest()` - обработчик callback для выбора периода
- `_generate_channel_digest()` - обработка генерации дайджеста

**UX Flow**:
1. Пользователь нажимает "📰 Дайджест" в деталях канала
2. Показывается клавиатура выбора периода (День/Неделя/Месяц)
3. После выбора вызывается API endpoint
4. Показывается результат или индикатор загрузки (для месяца)

## Context7 Best Practices

### ✅ Структурированное логирование

Все логи включают:
- `tenant_id` - для multi-tenant изоляции
- `user_id` - для трассировки пользователя
- `channel_id` - для диагностики
- `period` - для анализа производительности

### ✅ Prometheus метрики (низкая кардинальность)

Метрики используют только `tenant_id` и `period` в labels (без `channel_id`).

### ✅ Обработка ошибок

- Graceful degradation: ошибки кеша/БД не блокируют генерацию
- Fallback на OpenRouter при фильтрации GigaChat
- Информативные сообщения пользователю

### ✅ Валидация доступа

- Жесткая проверка через `user_channel` JOIN
- Дополнительная проверка `tenant_id` для безопасности
- Проверка активности канала

### ✅ Кеширование

- Redis кеш с TTL в зависимости от периода
- Персистентное хранение в БД для аналитики
- Поддержка как sync, так и async Redis клиентов

## Тестирование

### Ручное тестирование через API

```bash
# Получить пользователя
curl -X GET "http://localhost:8000/api/users/{telegram_id}"

# Сгенерировать дайджест за день
curl -X POST "http://localhost:8000/api/users/{user_id}/channels/{channel_id}/digest?period=1"

# Сгенерировать дайджест за неделю
curl -X POST "http://localhost:8000/api/users/{user_id}/channels/{channel_id}/digest?period=7"

# Сгенерировать дайджест за месяц
curl -X POST "http://localhost:8000/api/users/{user_id}/channels/{channel_id}/digest?period=30"
```

### Тестирование через бота

1. Открыть бота в Telegram
2. Отправить `/my_channels`
3. Выбрать канал
4. Нажать "📰 Дайджест"
5. Выбрать период (День/Неделя/Месяц)
6. Получить результат

### Автоматические тесты

Создан файл `tests/integration/test_channel_digest.py` с тестами:
- `test_collect_channel_posts_ranking()` - тест ранжирования
- `test_assemble_context_token_budget()` - тест токен-бюджета
- `test_cache_operations()` - тест кеширования
- `test_map_reduce_digest_structure()` - тест двухступенчатого саммари
- `test_token_estimation()` - тест оценки токенов
- `test_cache_key_generation()` - тест генерации ключа
- `test_cache_ttl_by_period()` - тест TTL

Запуск:
```bash
pytest tests/integration/test_channel_digest.py -v
```

### Валидация Context7

Запущен скрипт проверки:
```bash
python3 scripts/test_channel_digest_validation.py
```

Результат: ✅ Все проверки пройдены

## Проверка метрик

Метрики доступны через Prometheus endpoint:
```bash
curl http://localhost:8000/metrics | grep channel_digest
```

Доступные метрики:
- `channel_digest_generation_total{tenant_id="...", period="1|7|30"}`
- `channel_digest_generation_duration_seconds{tenant_id="...", period="1|7|30"}`
- `channel_digest_posts_count{tenant_id="...", period="1|7|30"}`
- `channel_digest_context_tokens{tenant_id="...", period="1|7|30"}`
- `channel_digest_cache_hits_total{tenant_id="...", period="1|7|30"}`

## Мониторинг

### Логи

Все операции логируются со структурированными данными:
- Начало генерации
- Попадания в кеш
- Ошибки доступа
- Завершение генерации с метриками

### Метрики для алертов

Рекомендуемые алерты:
- Высокое время генерации (> 60 сек для месяца)
- Низкий процент попаданий в кеш (< 50%)
- Ошибки генерации (> 5% запросов)

## Известные ограничения

1. **Async job для месяца**: Пока месяц генерируется синхронно. В будущем можно добавить async job через BackgroundTasks или очередь.

2. **LLM rerank**: Текущая реализация `_llm_rerank_posts()` просто возвращает top-N по engagement. В будущем можно улучшить через промпт для LLM rerank.

3. **Выбор модели**: Для месяца автоматически выбирается модель с большим контекстом, но явный выбор модели можно добавить.

## Улучшения для будущего

1. Сохранение `digest_id` в ответе API для отслеживания
2. Push-notifications для async операций
3. Настройка формата дайджеста пользователем
4. История дайджестов по каналам отдельно от общих дайджестов
5. Сравнение дайджестов (что изменилось с прошлого раза)

## Файлы изменены

- `api/services/digest_service.py` - добавлено ~600 строк (методы, промпты, метрики)
- `api/routers/channels.py` - добавлено ~130 строк (endpoint, модели)
- `api/bot/handlers/base.py` - добавлено ~120 строк (handlers, клавиатуры)
- `tests/integration/test_channel_digest.py` - создан файл с тестами
- `docs/CHANNEL_DIGEST_IMPLEMENTATION.md` - документация (этот файл)

## Checklist реализации

- [x] Методы DigestService для канального дайджеста
- [x] Гибридное ранжирование постов (engagement + freshness + diversity)
- [x] Токен-бюджет подход для контекста
- [x] Двухступенчатый саммари для месяца
- [x] Промпты для канального дайджеста
- [x] Кеширование (Redis + БД)
- [x] User-scoped API endpoint
- [x] Валидация доступа через JOIN
- [x] Bot integration (клавиатуры, handlers)
- [x] Prometheus метрики (низкая кардинальность)
- [x] Структурированное логирование
- [x] Обработка ошибок с fallback
- [x] Тесты и валидация

## Использование

### Через API

```python
import httpx

async with httpx.AsyncClient() as client:
    response = await client.post(
        "http://localhost:8000/api/users/{user_id}/channels/{channel_id}/digest",
        params={"period": 7}
    )
    result = response.json()
    print(result["content"])
```

### Через бота

1. `/my_channels` → выбрать канал → "📰 Дайджест" → выбрать период

## Troubleshooting

### Дайджест не генерируется

**Проверьте**:
1. Есть ли посты в канале за выбранный период
2. Логи на ошибки доступа к каналу
3. Метрики Prometheus на ошибки генерации

**Команды**:
```bash
# Проверить посты в канале
psql $DATABASE_URL -c "SELECT COUNT(*) FROM posts WHERE channel_id = '...' AND posted_at >= NOW() - INTERVAL '7 days';"

# Проверить логи
docker logs api | grep "channel digest"

# Проверить метрики
curl http://localhost:8000/metrics | grep channel_digest
```

### Медленная генерация для месяца

**Причина**: Месяц использует двухступенчатый саммари, что занимает время.

**Решение**: В будущем будет async job. Пока можно выбрать меньший период или подождать.

### Ошибка доступа

**Причина**: Канал не добавлен в подписки пользователя или неактивен.

**Решение**: Проверить подписки через `/my_channels` и убедиться, что канал активен.
