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

### DELETE /api/channels/users/{user_id}/channels/{channel_id}
Удаление канала.

**Response 204** - Канал удалён

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
