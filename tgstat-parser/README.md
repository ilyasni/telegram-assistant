# TGStat Parser Service

Сервис для парсинга топ-30 каналов по темам с TGStat и сохранения в PostgreSQL.

## Описание

Сервис автоматически парсит страницы TGStat (https://tgstat.ru/tags/theme) для получения списка тем и топ-30 каналов по каждой теме. Данные сохраняются в PostgreSQL и обновляются автоматически раз в месяц.

## Архитектура

- **Парсер**: Извлечение данных из TGStat с rate limiting и обработкой ошибок
- **База данных**: PostgreSQL с таблицами `themes` и `theme_channels`
- **Планировщик**: APScheduler для ежемесячного автоматического запуска
- **API**: HTTP endpoints для ручного запуска и мониторинга

## Установка и запуск

### Docker Compose

Сервис уже добавлен в `docker-compose.yml`. Для запуска:

```bash
docker compose up tgstat-parser
```

### Переменные окружения

- `DATABASE_URL` - URL подключения к PostgreSQL (обязательно)
- `TGSTAT_BASE_URL` - Базовый URL TGStat (по умолчанию: https://tgstat.ru)
- `TGSTAT_LOG_LEVEL` - Уровень логирования (по умолчанию: INFO)
- `TGSTAT_RATE_LIMIT_PER_SECOND` - Лимит запросов в секунду (по умолчанию: 1.0)
- `TGSTAT_RATE_LIMIT_MAX_REQUESTS` - Максимум запросов за сессию (по умолчанию: 200)
- `TGSTAT_HTTP_TIMEOUT` - Таймаут HTTP запросов в секундах (по умолчанию: 30)
- `TGSTAT_SCHEDULER_MONTHLY_HOUR` - Час запуска ежемесячной задачи (по умолчанию: 3)
- `TGSTAT_API_PORT` - Порт HTTP API (по умолчанию: 8020)

## API Endpoints

### Health Check

```bash
GET /health
GET /health/details
```

### Синхронизация

```bash
# Синхронизация всех тем
POST /sync

# Синхронизация конкретной темы
POST /sync/{theme_slug}
```

### Получение данных

```bash
# Список всех тем
GET /themes

# Каналы конкретной темы
GET /themes/{theme_slug}/channels
```

### Метрики Prometheus

```bash
GET /metrics
```

## Расписание

Сервис автоматически запускает синхронизацию всех тем:
- **Частота**: Первый день каждого месяца
- **Время**: 03:00 UTC (настраивается через `TGSTAT_SCHEDULER_MONTHLY_HOUR`)

## База данных

### Таблицы

#### `themes`
- `id` - UUID первичный ключ
- `slug` - Slug темы (уникальный)
- `name` - Название темы
- `description` - Описание темы (опционально)
- `channels_count` - Количество каналов в теме (0-30)
- `indexed_at` - Время последнего обновления
- `created_at` - Время создания

#### `theme_channels`
- `id` - UUID первичный ключ
- `theme_id` - Ссылка на тему
- `channel_username` - Username канала (без @)
- `title` - Название канала
- `subscribers` - Число подписчиков
- `er` - Engagement Rate в процентах (опционально)
- `url` - Ссылка на канал (https://t.me/...)
- `rank_in_theme` - Позиция в топе (1-30)
- `indexed_at` - Время последнего обновления

### Миграция

Миграция БД находится в `supabase/volumes/db/init/20260113_add_tgstat_themes.sql` и применяется автоматически при инициализации БД.

## Мониторинг

### Prometheus метрики

- `tgstat_parser_themes_total` - Количество обработанных тем (по статусу)
- `tgstat_parser_channels_total` - Количество обработанных каналов (по статусу)
- `tgstat_parser_requests_total` - Количество HTTP запросов к TGStat (по статусу)
- `tgstat_parser_errors_total` - Количество ошибок (по типу)
- `tgstat_parser_duration_seconds` - Время выполнения операций (Histogram)
- `tgstat_parser_rate_limit_hits_total` - Количество срабатываний rate limit
- `tgstat_parser_sync_in_progress` - Флаг выполнения синхронизации

### Логирование

Сервис использует структурированное логирование (structlog) в формате JSON. Все логи содержат контекст:
- `theme_slug` - Slug темы
- `channel_username` - Username канала
- `error_type` - Тип ошибки
- И другие релевантные поля

## Context7 Best Practices

Сервис реализован с использованием Context7 best practices:

- **Безопасность**: Валидация входных данных, параметризованные SQL запросы, sanitization
- **Observability**: Структурированное логирование, Prometheus метрики, health checks
- **Надёжность**: Retry с экспоненциальным backoff, rate limiting, graceful degradation
- **Производительность**: Connection pooling для БД, кеширование HTML hash
- **Документация**: Комментарии в коде на русском, docstrings для всех методов

## Разработка

### Структура проекта

```
tgstat-parser/
├── Dockerfile
├── requirements.txt
├── main.py                 # Точка входа
├── config.py               # Конфигурация
├── parser/
│   ├── tgstat_parser.py   # Основной парсер
│   ├── rate_limiter.py    # Rate limiting
│   └── quality_checker.py # Проверка качества данных
├── database/
│   ├── connection.py       # Подключение к БД
│   └── models.py           # SQLAlchemy модели
├── scheduler/
│   └── tasks.py            # Задачи планировщика
└── api/
    └── endpoints.py        # HTTP API endpoints
```

### Запуск локально

```bash
# Установка зависимостей
pip install -r requirements.txt

# Настройка переменных окружения
export DATABASE_URL="postgresql://user:password@localhost/dbname"

# Запуск сервиса
python main.py
```

## Лицензия

Часть проекта Telegram Assistant.
