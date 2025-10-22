# Event Envelope Specification

## Обзор

Event Envelope - это стандартизированный формат для всех событий в системе Telegram Assistant. Обеспечивает консистентность, трассируемость и расширяемость event-driven архитектуры.

## Структура Event Envelope

### Базовая структура

```json
{
  "event_id": "uuid",
  "event_type": "string",
  "tenant_id": "string",
  "user_id": "string|null",
  "correlation_id": "uuid",
  "occurred_at": "iso8601",
  "version": "string",
  "source": "string",
  "payload": "object"
}
```

### Обязательные поля

| Поле | Тип | Описание | Пример |
|------|-----|----------|--------|
| `event_id` | UUID | Уникальный идентификатор события | `"550e8400-e29b-41d4-a716-446655440000"` |
| `event_type` | String | Тип события (namespace.action) | `"auth.login.started"` |
| `tenant_id` | String | ID арендатора | `"tenant-123"` |
| `user_id` | String\|null | ID пользователя (null для системных событий) | `"user-456"` |
| `correlation_id` | UUID | ID для трассировки связанных событий | `"corr-789"` |
| `occurred_at` | ISO8601 | Время возникновения события | `"2025-01-15T10:30:00Z"` |
| `version` | String | Версия схемы события | `"1.0"` |
| `source` | String | Источник события | `"qr-auth-service"` |
| `payload` | Object | Данные события | `{...}` |

## Типы событий

### Authentication Events

#### auth.login.started
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "event_type": "auth.login.started",
  "tenant_id": "tenant-123",
  "user_id": null,
  "correlation_id": "corr-789",
  "occurred_at": "2025-01-15T10:30:00Z",
  "version": "1.0",
  "source": "qr-auth-service",
  "payload": {
    "qr_session_id": "qr-session-456",
    "telegram_user_id": "123456789",
    "invite_code": "ABC123XYZ456",
    "ip_address": "192.168.1.100",
    "user_agent": "TelegramBot/1.0",
    "client_meta": {
      "locale": "ru-RU",
      "timezone": "Europe/Moscow"
    }
  }
}
```

#### auth.login.scanned
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440001",
  "event_type": "auth.login.scanned",
  "tenant_id": "tenant-123",
  "user_id": null,
  "correlation_id": "corr-789",
  "occurred_at": "2025-01-15T10:30:15Z",
  "version": "1.0",
  "source": "qr-auth-service",
  "payload": {
    "qr_session_id": "qr-session-456",
    "telegram_user_id": "123456789",
    "scan_duration_ms": 15000
  }
}
```

#### auth.login.authorized
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440002",
  "event_type": "auth.login.authorized",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "correlation_id": "corr-789",
  "occurred_at": "2025-01-15T10:30:30Z",
  "version": "1.0",
  "source": "qr-auth-service",
  "payload": {
    "qr_session_id": "qr-session-456",
    "telegram_user_id": "123456789",
    "session_id": "session-789",
    "invite_code": "ABC123XYZ456",
    "user_data": {
      "username": "john_doe",
      "first_name": "John",
      "last_name": "Doe"
    },
    "authorization_duration_ms": 30000
  }
}
```

#### auth.login.failed
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440003",
  "event_type": "auth.login.failed",
  "tenant_id": "tenant-123",
  "user_id": null,
  "correlation_id": "corr-789",
  "occurred_at": "2025-01-15T10:30:45Z",
  "version": "1.0",
  "source": "qr-auth-service",
  "payload": {
    "qr_session_id": "qr-session-456",
    "telegram_user_id": "123456789",
    "error_code": "flood_wait",
    "error_message": "FloodWaitError: A wait of 60 seconds is required",
    "retry_after_seconds": 60
  }
}
```

#### auth.login.expired
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440004",
  "event_type": "auth.login.expired",
  "tenant_id": "tenant-123",
  "user_id": null,
  "correlation_id": "corr-789",
  "occurred_at": "2025-01-15T10:40:00Z",
  "version": "1.0",
  "source": "qr-auth-service",
  "payload": {
    "qr_session_id": "qr-session-456",
    "telegram_user_id": "123456789",
    "expiry_reason": "timeout",
    "session_duration_ms": 600000
  }
}
```

### Channel Events

#### channel.added
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440005",
  "event_type": "channel.added",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "correlation_id": "corr-790",
  "occurred_at": "2025-01-15T10:35:00Z",
  "version": "1.0",
  "source": "channel-service",
  "payload": {
    "channel_id": "channel-789",
    "telegram_channel_id": "-1001234567890",
    "username": "tech_news",
    "title": "Tech News Channel",
    "settings": {
      "auto_parse": true,
      "parse_frequency": "hourly"
    }
  }
}
```

#### channel.parsing.started
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440006",
  "event_type": "channel.parsing.started",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "correlation_id": "corr-791",
  "occurred_at": "2025-01-15T10:36:00Z",
  "version": "1.0",
  "source": "telethon-ingest",
  "payload": {
    "channel_id": "channel-789",
    "telegram_channel_id": "-1001234567890",
    "parse_job_id": "parse-job-123",
    "last_message_id": 12345
  }
}
```

#### channel.parsing.completed
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440007",
  "event_type": "channel.parsing.completed",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "correlation_id": "corr-791",
  "occurred_at": "2025-01-15T10:37:00Z",
  "version": "1.0",
  "source": "telethon-ingest",
  "payload": {
    "channel_id": "channel-789",
    "telegram_channel_id": "-1001234567890",
    "parse_job_id": "parse-job-123",
    "posts_parsed": 25,
    "posts_indexed": 23,
    "parsing_duration_ms": 60000,
    "last_message_id": 12370
  }
}
```

### RAG Events

#### rag.query.started
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440008",
  "event_type": "rag.query.started",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "correlation_id": "corr-792",
  "occurred_at": "2025-01-15T10:38:00Z",
  "version": "1.0",
  "source": "rag-service",
  "payload": {
    "query_id": "query-123",
    "query_text": "Что нового в искусственном интеллекте?",
    "user_id": "user-456",
    "session_id": "session-789"
  }
}
```

#### rag.query.completed
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440009",
  "event_type": "rag.query.completed",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "correlation_id": "corr-792",
  "occurred_at": "2025-01-15T10:38:05Z",
  "version": "1.0",
  "source": "rag-service",
  "payload": {
    "query_id": "query-123",
    "query_text": "Что нового в искусственном интеллекте?",
    "answer": "В области ИИ произошли значительные изменения...",
    "sources_count": 5,
    "processing_time_ms": 5000,
    "confidence_score": 0.85
  }
}
```

### System Events

#### system.health.check
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440010",
  "event_type": "system.health.check",
  "tenant_id": "system",
  "user_id": null,
  "correlation_id": "corr-793",
  "occurred_at": "2025-01-15T10:39:00Z",
  "version": "1.0",
  "source": "health-monitor",
  "payload": {
    "service": "api",
    "status": "healthy",
    "response_time_ms": 50,
    "memory_usage_mb": 256,
    "cpu_usage_percent": 15
  }
}
```

#### system.error.occurred
```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440011",
  "event_type": "system.error.occurred",
  "tenant_id": "tenant-123",
  "user_id": "user-456",
  "correlation_id": "corr-794",
  "occurred_at": "2025-01-15T10:40:00Z",
  "version": "1.0",
  "source": "qr-auth-service",
  "payload": {
    "error_code": "FLOOD_WAIT_ERROR",
    "error_message": "FloodWaitError: A wait of 60 seconds is required",
    "stack_trace": "...",
    "context": {
      "telegram_user_id": "123456789",
      "qr_session_id": "qr-session-456"
    }
  }
}
```

## Схемы валидации

### JSON Schema для Event Envelope

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": [
    "event_id",
    "event_type",
    "tenant_id",
    "correlation_id",
    "occurred_at",
    "version",
    "source",
    "payload"
  ],
  "properties": {
    "event_id": {
      "type": "string",
      "format": "uuid"
    },
    "event_type": {
      "type": "string",
      "pattern": "^[a-z]+\\.[a-z]+\\.[a-z]+$"
    },
    "tenant_id": {
      "type": "string",
      "minLength": 1
    },
    "user_id": {
      "type": ["string", "null"]
    },
    "correlation_id": {
      "type": "string",
      "format": "uuid"
    },
    "occurred_at": {
      "type": "string",
      "format": "date-time"
    },
    "version": {
      "type": "string",
      "pattern": "^\\d+\\.\\d+$"
    },
    "source": {
      "type": "string",
      "minLength": 1
    },
    "payload": {
      "type": "object"
    }
  }
}
```

## Правила именования

### Event Types
- Формат: `{namespace}.{entity}.{action}`
- Namespace: `auth`, `channel`, `rag`, `system`
- Entity: `login`, `parsing`, `query`, `health`
- Action: `started`, `completed`, `failed`, `expired`

### Примеры
- `auth.login.started`
- `channel.parsing.completed`
- `rag.query.failed`
- `system.health.check`

## Версионирование

### Семантическое версионирование
- **Major**: Breaking changes в схеме
- **Minor**: Добавление полей, новые типы событий
- **Patch**: Исправления, улучшения

### Обратная совместимость
- Старые версии событий должны поддерживаться
- Новые поля должны быть опциональными
- Удаление полей только в major версиях

## Трассировка

### Correlation ID
- Связывает связанные события
- Передается через все сервисы
- Используется для debugging и мониторинга

### Пример трассировки
```
auth.login.started (corr-789)
  ↓
auth.login.scanned (corr-789)
  ↓
auth.login.authorized (corr-789)
  ↓
channel.added (corr-790) // новый correlation_id
```

## Безопасность

### Конфиденциальные данные
- Не включать секреты в payload
- Маскировать чувствительные поля
- Использовать ссылки на данные вместо самих данных

### Примеры маскирования
```json
{
  "payload": {
    "telegram_user_id": "123456789",
    "session_string": "***MASKED***",
    "invite_code": "ABC***456"
  }
}
```

## Мониторинг

### Метрики событий
- `events_total{event_type, source}`
- `events_processing_duration_seconds`
- `events_failed_total{event_type, error_code}`

### Алерты
- Высокий rate событий
- Ошибки обработки событий
- Задержки в обработке

## Примеры использования

### Публикация события
```python
from events import EventPublisher

publisher = EventPublisher()

event = {
    "event_id": str(uuid.uuid4()),
    "event_type": "auth.login.started",
    "tenant_id": tenant_id,
    "user_id": None,
    "correlation_id": correlation_id,
    "occurred_at": datetime.utcnow().isoformat() + "Z",
    "version": "1.0",
    "source": "qr-auth-service",
    "payload": {
        "qr_session_id": qr_session_id,
        "telegram_user_id": telegram_user_id,
        "invite_code": invite_code
    }
}

await publisher.publish(event)
```

### Подписка на события
```python
from events import EventSubscriber

class RAGIndexer(EventSubscriber):
    async def handle_event(self, event):
        if event["event_type"] == "channel.parsing.completed":
            await self.index_posts(event["payload"])
```

## Заключение

Event Envelope обеспечивает стандартизированный, расширяемый и трассируемый способ обмена событиями между сервисами. Следование данной спецификации гарантирует консистентность и надежность event-driven архитектуры.
