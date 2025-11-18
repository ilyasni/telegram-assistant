# API Контракты Telegram Assistant

## Обзор

Telegram Assistant предоставляет REST API для управления каналами, пользователями, QR-авторизацией и RAG-поиском.

**Base URL**: `https://your-domain.com/api`  
**Swagger UI**: `https://your-domain.com/docs`  
**ReDoc**: `https://your-domain.com/redoc`

## Аутентификация

### JWT токены
Для защищённых endpoints требуется JWT токен в заголовке:
```
Authorization: Bearer <jwt_token>
```

### Rate Limiting
- **QR Init**: 5 запросов в минуту на IP
- **QR Status**: 30 запросов в минуту на IP
- **Admin API**: 50 запросов в час на пользователя

## QR Авторизация

### POST /tg/qr/start
**Алиас**: `POST /qr-auth/init`

Создание новой QR-сессии для авторизации.

**Request Body**:
```json
{
  "telegram_user_id": "int|str",
  "client_meta": {
    "ua": "Mozilla/5.0...",
    "locale": "ru-RU"
  },
  "invite_code": "STRING|optional"
}
```

**Response 200**:
```json
{
  "qr_session_id": "uuid",
  "png_url": "/tg/qr/png/{qr_session_id}",
  "expires_in": 600
}
```

**Errors**:
- `400` - Неверный invite_code или некорректные данные
- `429` - Превышен rate limit
- `500` - Внутренняя ошибка сервера

**Example**:
```bash
curl -X POST "https://your-domain.com/api/tg/qr/start" \
  -H "Content-Type: application/json" \
  -d '{
    "telegram_user_id": "123456789",
    "client_meta": {
      "ua": "TelegramBot/1.0",
      "locale": "ru-RU"
    },
    "invite_code": "ABC123XYZ456"
  }'
```

### GET /tg/qr/status/{qr_session_id}
**Алиас**: `GET /qr-auth/status/{id}`

Получение статуса QR-сессии.

**Path Parameters**:
- `qr_session_id` (string) - UUID QR-сессии

**Response 200**:
```json
{
  "status": "pending|scanned|authorized|expired|failed",
  "user_id": "uuid|optional",
  "updated_at": "2025-01-15T10:30:00Z"
}
```

**Response 404**:
```json
{
  "detail": "QR session not found or expired"
}
```

**Example**:
```bash
curl "https://your-domain.com/api/tg/qr/status/550e8400-e29b-41d4-a716-446655440000"
```

### GET /tg/qr/png/{qr_session_id}
**Алиас**: `GET /qr-auth/png/{session}`

Получение PNG изображения QR-кода.

**Path Parameters**:
- `qr_session_id` (string) - UUID QR-сессии

**Response**:
- `200` - `image/png` (изображение QR-кода)
- `404` - QR-сессия не найдена или истекла

**Headers**:
```
Cache-Control: no-store, no-cache, must-revalidate
Pragma: no-cache
Expires: 0
```

**Example**:
```bash
curl "https://your-domain.com/api/tg/qr/png/550e8400-e29b-41d4-a716-446655440000" \
  -o qr_code.png
```

### POST /tg/qr/cancel
Отмена QR-сессии.

**Request Body**:
```json
{
  "session_token": "jwt_token"
}
```

**Response 200**:
```json
{
  "ok": true
}
```

## Пользователи

### GET /api/users/{telegram_id}
Получение информации о пользователе по Telegram ID.

**Path Parameters**:
- `telegram_id` (int) - Telegram ID пользователя

**Response 200**:
```json
{
  "id": "uuid",
  "telegram_id": 123456789,
  "username": "username",
  "first_name": "Имя",
  "last_name": "Фамилия",
  "created_at": "2025-01-15T10:30:00Z",
  "last_active_at": "2025-01-15T10:30:00Z",
  "settings": {},
  "tenant_id": "uuid"
}
```

**Response 404**:
```json
{
  "detail": "User not found"
}
```

### POST /api/users/
Создание нового пользователя.

**Request Body**:
```json
{
  "telegram_id": 123456789,
  "username": "username",
  "first_name": "Имя",
  "last_name": "Фамилия"
}
```

**Response 201**:
```json
{
  "id": "uuid",
  "telegram_id": 123456789,
  "username": "username",
  "created_at": "2025-01-15T10:30:00Z",
  "tenant_id": "uuid"
}
```

### GET /api/users/{user_id}/subscription
Получение информации о подписке пользователя.

**Response 200**:
```json
{
  "subscription_type": "free|trial|basic|premium|enterprise",
  "channels_limit": 10,
  "posts_limit": 500,
  "rag_queries_limit": 50,
  "subscription_expires_at": "2025-12-31T23:59:59Z"
}
```

## Каналы

### GET /api/channels/users/{user_id}/channels
Получение списка каналов пользователя.

**Response 200**:
```json
[
  {
    "id": "uuid",
    "telegram_id": -1001234567890,
    "username": "channel_name",
    "title": "Название канала",
    "is_active": true,
    "last_message_at": "2025-01-15T10:30:00Z",
    "created_at": "2025-01-15T10:30:00Z"
  }
]
```

### POST /api/channels/users/{user_id}/channels
Добавление нового канала.

**Request Body**:
```json
{
  "telegram_id": -1001234567890,
  "username": "channel_name",
  "title": "Название канала",
  "settings": {}
}
```

**Response 201**:
```json
{
  "id": "uuid",
  "telegram_id": -1001234567890,
  "username": "channel_name",
  "title": "Название канала",
  "is_active": true,
  "created_at": "2025-01-15T10:30:00Z"
}
```

### POST /api/groups/{group_id}/digest
Запуск мультиагентного пайплайна группового дайджеста (LangGraph → worker). Возвращает `202 Accepted` после постановки события в очередь.

**Feature flag / rollout**  
Доступно только если `DIGEST_AGENT_ENABLED=1` или tenant присутствует в `DIGEST_AGENT_CANARY_TENANTS`. При отсутствии доступа API возвращает `403`.

**Request Body**:
```json
{
  "tenant_id": "uuid",
  "user_id": "uuid",
  "window_size_hours": 24,
  "delivery_channel": "telegram",
  "delivery_format": "telegram_html",
  "trigger": "manual"
}
```

- `window_size_hours` — только `4|6|12|24`.
- `delivery_channel` — пока поддерживается только `telegram`.
- `delivery_format` — `telegram_html` (по умолчанию), `json` или `cards`.
- `trigger` — источник запуска (`manual|scheduler|retry`).
- `context_stats` — агрегаты Stage 5 (кол-во сообщений до/после dedup, топ-k).
- `context_ranking` — список top-k сообщений с оценками (`score`, `timestamp_iso`).
- `context_duplicates` — карта `id → [duplicates]` для soft/hard dedup.
- `context_history_links` — соответствия новых сообщений с историческими окнами (для анализа повторов).

**Response 202**:
```json
{
  "history_id": "uuid",
  "group_window_id": "uuid",
  "message_count": 42,
  "participant_count": 7,
  "status": "queued",
  "context_stats": {
    "deduplicated_messages": 38,
    "duplicates_removed": 4
  },
  "context_ranking": [
    {
      "message_id": "1",
      "score": 0.87
    }
  ],
  "context_duplicates": {
    "1": ["2"]
  },
  "context_history_links": {
    "3": {
      "matched_id": "hist-42",
      "similarity": 0.92
    }
  ]
}
```

**Ошибки**:
- `400` — неверное значение `window_size_hours`.
- `403` — фича недоступна для арендатора (feature flag).
- `404` — группа не найдена для указанного tenant.
- `422` — валидационные ошибки Pydantic.

**Example**:
```bash
curl -X POST "https://your-domain.com/api/groups/3f6c0b3a-5f3a-4f93-a1e4-0fd3a3d2b6b5/digest" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt>" \
  -d '{
    "tenant_id": "1d7a788c-5fc3-4a04-9d92-9d76c52d8110",
    "user_id": "d9f0fddb-2d57-4dff-b7a7-0c1f0d9fb761",
    "window_size_hours": 24,
    "delivery_channel": "telegram",
    "delivery_format": "telegram_html",
    "trigger": "manual"
  }'
```

### DELETE /api/channels/users/{user_id}/channels/{channel_id}
Удаление канала.

**Response 204** - Канал удалён

## Группы

### GET /api/groups
Получение списка групп арендатора.

**Query Parameters**:
- `tenant_id` (uuid, optional) — по умолчанию tenant текущего пользователя
- `status` (`active|disabled`) — фильтр по активности
- `limit` / `offset` — постраничная навигация

**Response 200**:
```json
{
  "groups": [
    {
      "id": "uuid",
      "tg_chat_id": -1009876543210,
      "title": "Product Support",
      "username": "product_support",
      "is_active": true,
      "last_checked_at": "2025-11-09T08:15:00Z",
      "created_at": "2025-10-01T12:00:00Z",
      "settings": {
        "digest": {
          "default_window_hours": 12,
          "delivery_channel": "telegram"
        },
        "limits": {
          "max_daily_digests": 4
        }
      }
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

### POST /api/groups
Подключение новой Telegram-группы. Используется Mini App или Bot команды.

**Request Body**:
```json
{
  "tg_chat_id": -1009876543210,
  "title": "Product Support",
  "username": "product_support",
  "invite_link": "https://t.me/+abc123",
  "settings": {
    "digest": {
      "default_window_hours": 12,
      "delivery_channel": "telegram"
    }
  }
}
```

**Response 201**:
```json
{
  "id": "uuid",
  "tg_chat_id": -1009876543210,
  "title": "Product Support",
  "username": "product_support",
  "is_active": true,
  "created_at": "2025-11-09T08:15:00Z"
}
```

### PATCH /api/groups/{group_id}
Обновление настроек группы (лимиты дайджеста, активность).

**Request Body**:
```json
{
  "is_active": false,
  "settings": {
    "digest": {
      "default_window_hours": 24,
      "delivery_channel": "email",
      "delivery_address": "ops@example.com"
    },
    "limits": {
      "max_daily_digests": 2
    }
  }
}
```

**Response 200** — обновлённый объект группы.

### POST /api/groups/{group_id}/digest
Запросить дайджест обсуждений в группе. Допускает окна 4/6/12/24 часа.

**Request Body**:
```json
{
  "window_size_hours": 24,
  "format": "markdown",
  "delivery_channel": "telegram",
  "delivery_address": null,
  "include_sections": ["topics", "metrics", "participants"],
  "force_regeneration": false
}
```

**Response 202**:
```json
{
  "request_id": "uuid",
  "window_id": "uuid",
  "group_id": "uuid",
  "status": "queued"
}
```

### GET /api/groups/{group_id}/digests/{digest_id}
Получение готового дайджеста (внутренний API/админка).

**Response 200**:
```json
{
  "digest_id": "uuid",
  "group_id": "uuid",
  "window": {
    "size_hours": 24,
    "start": "2025-11-08T08:00:00Z",
    "end": "2025-11-09T08:00:00Z"
  },
  "summary": "Основные обсуждения сконцентрированы вокруг...",
  "metrics": {
    "tone": "neutral",
    "sentiment": 0.1,
    "conflict": 0.2,
    "collaboration": 0.8,
    "stress": 0.4,
    "enthusiasm": 0.6
  },
  "topics": [
    {
      "topic": "Проблема с вебвью",
      "priority": "high",
      "message_count": 15,
      "highlights": [
        {
          "message_id": "uuid",
          "excerpt": "Вебвью падает при переходе на страницу оплаты"
        }
      ]
    }
  ],
  "participants": [
    {
      "telegram_id": 1234567,
      "username": "boyversus",
      "role": "initiator",
      "message_count": 10,
      "summary": "Инициировал обсуждение, предложил workaround"
    }
  ],
  "attachments": [],
  "evaluation_scores": {
    "ragas": {
      "faithfulness": 0.92,
      "answer_relevance": 0.88
    },
    "trajectory": {
      "llm_as_judge": 0.93
    }
  },
  "delivery_status": "sent"
}
```

## RAG Поиск

### POST /api/rag/query
Выполнение RAG-запроса.

**Request Body**:
```json
{
  "query": "Что нового в искусственном интеллекте?",
  "user_id": "uuid"
}
```

**Response 200**:
```json
{
  "result": {
    "answer": "В области ИИ произошли значительные изменения...",
    "sources": [
      {
        "title": "Новости ИИ за январь 2025",
        "url": "https://example.com/news",
        "relevance_score": 0.95
      }
    ]
  },
  "query_id": "uuid",
  "processing_time": 1.23
}
```

## Администрирование

### POST /api/admin/invites
Создание нового инвайт-кода.

**Headers**: `Authorization: Bearer <admin_jwt>`

**Request Body**:
```json
{
  "tenant_id": "uuid",
  "role": "user|admin",
  "uses_limit": 10,
  "expires_at": "2025-12-31T23:59:59Z",
  "notes": "Приглашение для команды разработки"
}
```

**Response 201**:
```json
{
  "code": "ABC123XYZ456",
  "tenant_id": "uuid",
  "role": "user",
  "uses_limit": 10,
  "uses_count": 0,
  "expires_at": "2025-12-31T23:59:59Z",
  "created_at": "2025-01-15T10:30:00Z"
}
```

### GET /api/admin/invites/{code}
Получение информации об инвайт-коде.

**Response 200**:
```json
{
  "code": "ABC123XYZ456",
  "tenant_id": "uuid",
  "role": "user",
  "uses_limit": 10,
  "uses_count": 3,
  "active": true,
  "expires_at": "2025-12-31T23:59:59Z",
  "created_at": "2025-01-15T10:30:00Z",
  "last_used_at": "2025-01-15T10:30:00Z"
}
```

### POST /api/admin/invites/{code}/revoke
Отзыв инвайт-кода.

**Response 200**:
```json
{
  "code": "ABC123XYZ456",
  "status": "revoked",
  "revoked_at": "2025-01-15T10:30:00Z"
}
```

### GET /api/admin/invites
Получение списка инвайт-кодов.

**Query Parameters**:
- `tenant_id` (optional) - Фильтр по tenant
- `status` (optional) - Фильтр по статусу (active|revoked|expired)
- `limit` (optional) - Количество записей (по умолчанию: 50)
- `offset` (optional) - Смещение (по умолчанию: 0)

**Response 200**:
```json
{
  "invites": [
    {
      "code": "ABC123XYZ456",
      "tenant_id": "uuid",
      "role": "user",
      "uses_limit": 10,
      "uses_count": 3,
      "active": true,
      "expires_at": "2025-12-31T23:59:59Z",
      "created_at": "2025-01-15T10:30:00Z"
    }
  ],
  "total": 1,
  "limit": 50,
  "offset": 0
}
```

## Health Checks

### GET /health
Проверка состояния API.

**Response 200**:
```json
{
  "status": "healthy",
  "timestamp": "2025-01-15T10:30:00Z",
  "version": "1.0.0",
  "services": {
    "database": "ok",
    "redis": "ok",
    "qdrant": "ok"
  }
}
```

### GET /health/auth
Проверка состояния QR-авторизации.

**Response 200**:
```json
{
  "status": "healthy",
  "qr_sessions_active": 5,
  "last_qr_created": "2025-01-15T10:30:00Z"
}
```

### GET /health/bot
Проверка готовности бота.

**Response 200**:
```json
{
  "bot_ready": true
}
```

## Метрики

### GET /metrics
Prometheus метрики.

**Response**: Prometheus format

**Основные метрики**:
- `auth_qr_start_total` - Количество запущенных QR-сессий
- `auth_qr_success_total` - Успешные авторизации
- `auth_qr_expired_total` - Истёкшие сессии
- `http_requests_total` - HTTP запросы по endpoint'ам
- `http_request_duration_seconds` - Время обработки запросов

## Ошибки

### Стандартные HTTP коды
- `200` - Успех
- `201` - Создано
- `204` - Нет содержимого
- `400` - Неверный запрос
- `401` - Не авторизован
- `403` - Доступ запрещён
- `404` - Не найдено
- `409` - Конфликт
- `410` - Истёк (для инвайт-кодов)
- `429` - Слишком много запросов
- `500` - Внутренняя ошибка сервера

### Формат ошибок
```json
{
  "detail": "Описание ошибки",
  "error_code": "INVALID_INVITE_CODE",
  "timestamp": "2025-01-15T10:30:00Z"
}
```

## Event Bus

### Channel Events
- `ChannelSubscribedEventV1`
- `ChannelUnsubscribedEventV1`

### Group Events
- `GroupLinkedEventV1` — пользователь подключил группу (Источник: Mini App / Bot). Payload соответствует `api/events/schemas/groups_v1.py`.
- `GroupConversationWindowReadyEventV1` — окно обсуждений подготовлено ingestion/worker сервисом.
- `GroupDigestRequestedEventV1` — пользователь инициировал генерацию дайджеста (4/6/12/24 часа).
- `GroupDigestGeneratedEventV1` — мультиагентная система завершила генерацию дайджеста, доступны темы, участники, метрики, оценки качества.
- `GroupDigestDeliveredEventV1` — дайджест доставлен по выбранному каналу (Telegram, e-mail или webhook).

**Общие требования:**
- `trace_id` и `idempotency_key` обязательны для всех событий.
- В payload всегда указывается `tenant_id` и `group_id`.
- Индикаторы (`conflict`, `collaboration`, `stress`, `enthusiasm`) передаются в нормированном виде `[0;1]`.
- Оценки качества (`evaluation_scores`) хранятся в формате LangSmith (`trajectory_accuracy`, `faithfulness`, `answer_relevance`).

**Пример: GroupDigestGeneratedEventV1**
```json
{
  "event_id": "f1f17fbe-4725-4b11-8ac1-6f4f92fc6af3",
  "schema_version": "v1",
  "trace_id": "req-2025-11-09-12345",
  "occurred_at": "2025-11-09T09:05:00Z",
  "idempotency_key": "digest-group-42-window-24h",
  "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
  "digest_id": "9b89ee8d-8396-46a6-9e40-4f5abf505f12",
  "window_id": "95c2c6bd-1d8f-4d82-a2b5-9146f17b6319",
  "window_size_hours": 24,
  "summary": "Основные обсуждения сфокусированы на проблеме вебвью...",
  "topics": [
    {
      "topic": "Проблема с вебвью",
      "priority": "high",
      "message_count": 15,
      "highlights": [
        {"message_id": "dc5d97b0-044f-4f9c-a7f0-8aa18d74b7fb", "excerpt": "Вебвью падает при оплате"}
      ]
    }
  ],
  "participants": [
    {
      "telegram_id": 1234567,
      "username": "boyversus",
      "role": "initiator",
      "message_count": 10,
      "summary": "Инициировал обсуждение и предложил обходной путь"
    }
  ],
  "metrics": {
    "tone": "neutral",
    "sentiment": 0.12,
    "stress": 0.4,
    "conflict": 0.2,
    "collaboration": 0.8,
    "enthusiasm": 0.6
  },
  "evaluation_scores": {
    "ragas": {
      "faithfulness": 0.94,
      "answer_relevance": 0.91
    },
    "trajectory_accuracy": 0.95
  }
}
```

## Примеры использования

### Полный цикл QR-авторизации

1. **Создание QR-сессии**:
```bash
curl -X POST "https://your-domain.com/api/tg/qr/start" \
  -H "Content-Type: application/json" \
  -d '{
    "telegram_user_id": "123456789",
    "invite_code": "ABC123XYZ456"
  }'
```

2. **Получение QR-кода**:
```bash
curl "https://your-domain.com/api/tg/qr/png/550e8400-e29b-41d4-a716-446655440000" \
  -o qr_code.png
```

3. **Проверка статуса**:
```bash
curl "https://your-domain.com/api/tg/qr/status/550e8400-e29b-41d4-a716-446655440000"
```

### RAG-поиск

```bash
curl -X POST "https://your-domain.com/api/rag/query" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <jwt_token>" \
  -d '{
    "query": "Что нового в машинном обучении?",
    "user_id": "550e8400-e29b-41d4-a716-446655440000"
  }'
```

### Управление инвайтами

```bash
# Создание инвайта
curl -X POST "https://your-domain.com/api/admin/invites" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <admin_jwt>" \
  -d '{
    "tenant_id": "550e8400-e29b-41d4-a716-446655440000",
    "role": "user",
    "uses_limit": 5,
    "expires_at": "2025-12-31T23:59:59Z"
  }'
```

## Безопасность

### CORS
Разрешённые origins настраиваются через переменную `CORS_ALLOWED_ORIGINS`.

### Rate Limiting
- Реализован через Redis
- Ключи: `ratelimit:{route}:{ip}`
- Настраивается через ENV переменные

### Security Headers
- `Strict-Transport-Security`
- `Content-Security-Policy`
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`

### Логирование
- Все запросы логируются в структурированном JSON формате
- Секретные данные (токены, пароли) маскируются
- Логи доступны через `/metrics` endpoint
