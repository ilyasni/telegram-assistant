# Интеграция TGStat Parser с основным API

## Context

TGStat Parser Service парсит топ-30 каналов по темам с TGStat и сохраняет в PostgreSQL. Данный документ описывает интеграцию этих данных с основным API приложения.

## Архитектура

```
┌─────────────────┐
│ TGStat Parser   │
│   Service       │
│  (Port 8020)    │
└────────┬────────┘
         │
         │ Парсинг и синхронизация
         │
         ▼
┌─────────────────┐
│   PostgreSQL    │
│  themes         │
│  theme_channels │
└────────┬────────┘
         │
         │ SQL запросы
         │
         ▼
┌─────────────────┐
│  TGStat Service │
│  (api/services) │
└────────┬────────┘
         │
         │ Бизнес-логика
         │
         ▼
┌─────────────────┐
│  Themes Router  │
│  (api/routers)  │
└────────┬────────┘
         │
         │ HTTP API
         │
         ▼
┌─────────────────┐
│   Clients       │
│  (Bot, WebApp)  │
└─────────────────┘
```

## API Endpoints

### Получение списка тем

```http
GET /api/themes?limit=100&offset=0&search=technology
```

**Параметры:**
- `limit` (optional, default: 100, max: 500) - Максимальное количество тем
- `offset` (optional, default: 0) - Смещение для пагинации
- `search` (optional) - Поиск по названию или описанию темы

**Ответ:**
```json
{
  "themes": [
    {
      "id": "uuid",
      "slug": "technology",
      "name": "Technology",
      "description": "Technology channels",
      "channels_count": 30,
      "indexed_at": "2026-01-13T12:00:00Z",
      "created_at": "2026-01-13T10:00:00Z"
    }
  ],
  "total": 369,
  "limit": 100,
  "offset": 0
}
```

### Получение темы по slug

```http
GET /api/themes/{theme_slug}
```

**Ответ:**
```json
{
  "id": "uuid",
  "slug": "technology",
  "name": "Technology",
  "description": "Technology channels",
  "channels_count": 30,
  "indexed_at": "2026-01-13T12:00:00Z",
  "created_at": "2026-01-13T10:00:00Z"
}
```

### Получение каналов темы

```http
GET /api/themes/{theme_slug}/channels?limit=30&offset=0&min_subscribers=100000&min_er=5.0
```

**Параметры:**
- `limit` (optional, default: 30, max: 100) - Максимальное количество каналов
- `offset` (optional, default: 0) - Смещение для пагинации
- `min_subscribers` (optional) - Минимальное количество подписчиков
- `min_er` (optional) - Минимальный Engagement Rate (%)

**Ответ:**
```json
{
  "channels": [
    {
      "id": "uuid",
      "channel_username": "durov",
      "title": "Telegram",
      "subscribers": 1000000,
      "er": 5.5,
      "url": "https://t.me/durov",
      "rank_in_theme": 1,
      "indexed_at": "2026-01-13T12:00:00Z"
    }
  ],
  "total": 30,
  "limit": 30,
  "offset": 0,
  "theme_slug": "technology"
}
```

### Получение рекомендованных каналов

```http
GET /api/themes/{theme_slug}/recommendations?limit=10&min_subscribers=100000&min_er=5.0
```

**Параметры:**
- `limit` (optional, default: 10, max: 50) - Максимальное количество рекомендаций
- `min_subscribers` (optional) - Минимальное количество подписчиков
- `min_er` (optional) - Минимальный Engagement Rate (%)

**Ответ:**
```json
[
  {
    "id": "uuid",
    "channel_username": "durov",
    "title": "Telegram",
    "subscribers": 1000000,
    "er": 5.5,
    "url": "https://t.me/durov",
    "rank_in_theme": 1,
    "indexed_at": "2026-01-13T12:00:00Z"
  }
]
```

## Использование в коде

### Пример использования сервиса

```python
from api.services.tgstat_service import get_tgstat_service
from models.database import get_db

# Получение сервиса
service = get_tgstat_service()

# Получение сессии БД
db = next(get_db())

# Получение всех тем
themes = service.get_all_themes(db=db, limit=10, offset=0)

# Получение темы по slug
theme = service.get_theme_by_slug(db=db, slug='technology')

# Получение каналов темы
channels = service.get_theme_channels(
    db=db,
    theme_slug='technology',
    limit=10,
    min_subscribers=100000
)

# Получение рекомендованных каналов
recommendations = service.get_recommended_channels(
    db=db,
    theme_slug='technology',
    limit=10,
    min_subscribers=100000,
    min_er=5.0
)
```

### Пример использования в роутере

```python
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from models.database import get_db
from api.services.tgstat_service import get_tgstat_service

router = APIRouter(prefix="/my-feature", tags=["my-feature"])

@router.get("/recommendations")
async def get_recommendations(
    theme_slug: str,
    db: Session = Depends(get_db)
):
    service = get_tgstat_service()
    channels = service.get_recommended_channels(
        db=db,
        theme_slug=theme_slug,
        limit=10
    )
    return channels
```

## Метрики Prometheus

Сервис экспортирует следующие метрики:

- `tgstat_themes_requests_total` - Количество запросов к темам (по операции и статусу)
- `tgstat_channels_requests_total` - Количество запросов к каналам (по операции и статусу)
- `tgstat_service_duration_seconds` - Время выполнения операций (Histogram)

## Безопасность

- Все SQL запросы используют параметризованные запросы для предотвращения SQL injection
- Валидация входных данных через Pydantic модели
- Валидация slug (только буквы, цифры, дефисы, подчёркивания)
- Ограничения на параметры запросов (limit, offset)

## Производительность

- Использование индексов БД для быстрого поиска
- Пагинация для больших списков
- Connection pooling через SQLAlchemy
- Метрики для мониторинга производительности

## Тестирование

### Unit тесты

```bash
pytest tests/unit/test_tgstat_service.py
```

### Integration тесты

```bash
pytest tests/integration/test_themes_api.py
```

## Следующие шаги

1. Интеграция с ботом для рекомендаций каналов
2. Использование в системе трендов для обогащения данных
3. Кеширование часто запрашиваемых тем/каналов в Redis
4. Дашборд Grafana для мониторинга использования

## См. также

- [TGStat Parser README](../tgstat-parser/README.md)
- [API Documentation](../api/README.md)
