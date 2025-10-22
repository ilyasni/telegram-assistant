# Схема базы данных Telegram Assistant

## Обзор архитектуры

Система построена на принципах **глобальных каналов и постов** с per-user подписками. Основные компоненты:

- **Tenants** — изоляция пользователей по арендаторам
- **Global Channels** — глобальные каналы без tenant_id
- **Global Posts** — глобальные посты без tenant_id  
- **Users ↔ Channels** — many-to-many подписки пользователей на каналы
- **Groups** — per-tenant мониторинг групповых чатов и упоминаний
- **Enrichment** — тегирование, OCR, vision, crawl4ai результаты

### Ключевые преимущества

- ✅ **Экономия места**: один канал вместо N копий
- ✅ **Производительность**: меньше данных для индексации
- ✅ **Простота**: нет синхронизации каналов между тенантами
- ✅ **Масштабируемость**: проще добавлять новых пользователей
- ✅ **Telegram метрики**: полная поддержка просмотров, реакций, репостов, комментариев

## ER Диаграмма

```mermaid
erDiagram
  TENANTS ||--o{ USERS : has
  USERS ||--o{ USER_CHANNEL : subscribes
  CHANNELS ||--o{ USER_CHANNEL : has
  CHANNELS ||--o{ POSTS : publishes
  POSTS ||--|| POST_ENRICHMENT : has
  POSTS ||--o{ POST_MEDIA : has
  POSTS ||--o{ POST_REACTIONS : has
  POSTS ||--o{ POST_FORWARDS : has
  POSTS ||--o{ POST_REPLIES : has

  TENANTS ||--o{ GROUPS : owns
  GROUPS ||--o{ GROUP_MESSAGES : contains
  USERS ||--o{ USER_GROUP : joins
  GROUPS ||--o{ USER_GROUP : has
  GROUP_MESSAGES ||--o{ GROUP_MENTIONS : has
  USERS ||--o{ GROUP_MENTIONS : mentioned

  TENANTS {
    uuid id PK
    text name
    timestamptz created_at
    jsonb settings
  }
  USERS {
    uuid id PK
    uuid tenant_id FK
    bigint telegram_id UK
    text username
    text first_name
    text last_name
    timestamptz created_at
    timestamptz last_active_at
    jsonb settings
  }
  CHANNELS {
    uuid id PK
    bigint tg_channel_id UK
    text username
    text title
    boolean is_active
    timestamptz last_message_at
    timestamptz created_at
    jsonb settings
  }
  USER_CHANNEL {
    uuid user_id FK
    uuid channel_id FK
    timestamptz subscribed_at
    boolean is_active
    jsonb settings
  }
  POSTS {
    uuid id PK
    uuid channel_id FK
    bigint tg_message_id
    text content
    jsonb media_urls
    timestamptz posted_at
    text url
    boolean has_media
    integer yyyymm
    timestamptz created_at
    boolean is_processed
    integer views_count
    integer forwards_count
    integer reactions_count
    integer replies_count
    boolean is_pinned
    boolean is_edited
    timestamptz edited_at
    text post_author
    bigint reply_to_message_id
    bigint via_bot_id
    boolean is_silent
    boolean noforwards
    timestamptz last_metrics_update
  }
  POST_ENRICHMENT {
    uuid post_id PK/FK
    jsonb tags
    jsonb vision_labels
    text ocr_text
    text crawl_md
    varchar enrichment_provider
    timestamptz enriched_at
    integer enrichment_latency_ms
    jsonb metadata
    timestamptz updated_at
  }
  POST_MEDIA {
    uuid id PK
    uuid post_id FK
    varchar media_type
    text media_url
    text thumbnail_url
    bigint file_size_bytes
    integer width
    integer height
    integer duration_seconds
    text tg_file_id
    text tg_file_unique_id
    text sha256
    timestamptz created_at
  }
  GROUPS {
    uuid id PK
    uuid tenant_id FK
    bigint tg_chat_id
    text title
    text username
    boolean is_active
    timestamptz last_checked_at
    timestamptz created_at
    jsonb settings
  }
  USER_GROUP {
    uuid user_id FK
    uuid group_id FK
    boolean monitor_mentions
    timestamptz subscribed_at
    boolean is_active
    jsonb settings
  }
  GROUP_MESSAGES {
    uuid id PK
    uuid group_id FK
    bigint tg_message_id
    bigint sender_tg_id
    text sender_username
    text content
    timestamptz posted_at
    timestamptz created_at
  }
  GROUP_MENTIONS {
    uuid id PK
    uuid group_message_id FK
    uuid mentioned_user_id FK
    bigint mentioned_user_tg_id
    text context_snippet
    boolean is_processed
    timestamptz processed_at
    timestamptz created_at
  }
  POST_REACTIONS {
    uuid id PK
    uuid post_id FK
    varchar reaction_type
    text reaction_value
    bigint user_tg_id
    boolean is_big
    timestamptz created_at
  }
  POST_FORWARDS {
    uuid id PK
    uuid post_id FK
    bigint from_chat_id
    bigint from_message_id
    text from_chat_title
    text from_chat_username
    timestamptz forwarded_at
  }
  POST_REPLIES {
    uuid id PK
    uuid post_id FK
    uuid reply_to_post_id FK
    bigint reply_message_id
    bigint reply_chat_id
    bigint reply_author_tg_id
    text reply_content
    timestamptz reply_posted_at
  }
```

## Описание таблиц

### Основные сущности

#### `tenants`
Корневая таблица для мульти-тенантности. Каждый арендатор изолирован.

**Ключевые поля:**
- `id` — UUID первичный ключ
- `name` — название арендатора
- `settings` — JSONB настройки арендатора

#### `users`
Пользователи системы, привязанные к арендаторам.

**Ключевые поля:**
- `telegram_id` — уникальный Telegram ID пользователя
- `tenant_id` — связь с арендатором
- `settings` — персональные настройки пользователя

#### `channels`
Каналы для парсинга, привязанные к арендаторам.

**Ключевые поля:**
- `tg_channel_id` — Telegram ID канала (отрицательное число)
- `title` — название канала
- `is_active` — активность парсинга

### Связи many-to-many

#### `user_channel`
Связывает пользователей с каналами для подписок.

**Особенности:**
- Составной первичный ключ `(user_id, channel_id)`
- `is_active` — для мягкого удаления подписок
- `settings` — персональные настройки подписки

### Посты и обогащение

#### `posts`
Глобальные посты из всех каналов.

**Ключевые поля:**
- `tg_message_id` — ID сообщения в Telegram
- `posted_at` — время публикации поста
- `has_media` — флаг наличия медиа (автоматически обновляется триггерами)
- `yyyymm` — вычисляемое поле для партиционирования

#### `post_enrichment`
Обогащённые данные постов.

**Ключевые поля:**
- `tags` — JSONB массив тегов от GigaChat/OpenRouter
- `vision_labels` — JSONB результаты анализа изображений
- `ocr_text` — текст из OCR
- `crawl_md` — markdown контент от crawl4ai

#### `post_media`
Медиа-файлы постов с Telegram-специфичными идентификаторами.

**Ключевые поля:**
- `tg_file_id` — Telegram file ID
- `tg_file_unique_id` — стабильный уникальный ID
- `sha256` — хеш файла для дедупликации

### Группы и упоминания

#### `groups`
Групповые чаты для мониторинга.

**Ключевые поля:**
- `tg_chat_id` — Telegram ID группы
- `last_checked_at` — время последней проверки

#### `group_messages`
Сообщения из групповых чатов.

**Ключевые поля:**
- `sender_tg_id` — ID отправителя
- `content` — текст сообщения

#### `group_mentions`
Упоминания пользователей в группах.

**Ключевые поля:**
- `mentioned_user_tg_id` — ID упомянутого пользователя
- `context_snippet` — контекст упоминания
- `is_processed` — флаг обработки

## Индексная стратегия

### Производительность запросов

#### Основные индексы
```sql
-- FK индексы для JOIN производительности
CREATE INDEX ix_posts_channel_id ON posts(channel_id);
CREATE INDEX ix_post_enrichment_post_id ON post_enrichment(post_id);
CREATE INDEX ix_groups_tenant_id ON groups(tenant_id);

-- Временные запросы
CREATE INDEX ix_posts_posted_at ON posts(posted_at DESC);
CREATE INDEX ix_posts_channel_posted ON posts(channel_id, posted_at DESC);
```

#### GIN индексы для JSONB
```sql
-- Универсальные GIN индексы для всех операторов
CREATE INDEX ix_post_enrichment_tags_gin ON post_enrichment USING GIN(tags);
CREATE INDEX ix_post_enrichment_vision_gin ON post_enrichment USING GIN(vision_labels);
```

#### Partial индексы для активных записей
```sql
-- Только активные подписки
CREATE INDEX ix_user_channel_user ON user_channel(user_id) WHERE is_active = true;
CREATE INDEX ix_groups_active ON groups(is_active) WHERE is_active = true;
```

### Уникальные ограничения
```sql
-- Предотвращение дубликатов
CREATE UNIQUE INDEX ux_channels_tg ON channels(tg_channel_id);
CREATE UNIQUE INDEX ux_posts_chan_msg ON posts(channel_id, tg_message_id);
CREATE UNIQUE INDEX ux_group_messages ON group_messages(group_id, tg_message_id);
```

## RLS политики

### Принцип изоляции
Пользователи видят только посты из каналов, на которые подписаны.

#### Политика для posts
```sql
CREATE POLICY posts_by_subscription ON posts
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM users u
        JOIN user_channel uc ON uc.user_id = u.id
        WHERE u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
          AND u.tenant_id = (current_setting('app.current_tenant_id', true)::UUID)
          AND uc.channel_id = posts.channel_id
          AND uc.is_active = true
    )
);
```

#### Политика для post_enrichment
```sql
CREATE POLICY enrichment_by_subscription ON post_enrichment
FOR SELECT TO authenticated
USING (
    EXISTS (
        SELECT 1
        FROM posts p
        JOIN user_channel uc ON uc.channel_id = p.channel_id
        JOIN users u ON u.id = uc.user_id
        WHERE p.id = post_enrichment.post_id
          AND u.telegram_id = (current_setting('app.current_user_tg_id', true)::BIGINT)
          AND u.tenant_id = (current_setting('app.current_tenant_id', true)::UUID)
          AND uc.is_active = true
    )
);
```

### Bypass для воркеров
```sql
-- Роль для background workers
CREATE ROLE worker_role;
GRANT worker_role TO postgres;

-- Bypass RLS для всех рабочих таблиц
CREATE POLICY posts_worker_bypass ON posts FOR ALL TO worker_role USING (true) WITH CHECK (true);
```

## Миграция на глобальные каналы

### Что изменилось

- **Channels**: Убран `tenant_id`, добавлен глобальный уникальный индекс на `tg_channel_id`
- **Posts**: Убран `tenant_id`, добавлен уникальный индекс на `(channel_id, tg_message_id)`
- **Доступ**: Контролируется через `user_channel` подписки + RLS политики
- **Groups**: Остались per-tenant (пользователь сам их подключает)

### Преимущества

- ✅ **Экономия места**: один канал вместо N копий
- ✅ **Производительность**: меньше данных для индексации  
- ✅ **Простота**: нет синхронизации каналов между тенантами
- ✅ **Масштабируемость**: проще добавлять новых пользователей

## Telegram метрики

### Поддерживаемые метрики

- **Просмотры** (`views_count`) — количество просмотров поста
- **Репосты** (`forwards_count`) — количество репостов в другие чаты
- **Реакции** (`reactions_count`) — количество уникальных реакций
- **Комментарии** (`replies_count`) — количество ответов/комментариев
- **Закрепление** (`is_pinned`) — закреплён ли пост в канале
- **Редактирование** (`is_edited`, `edited_at`) — был ли пост отредактирован
- **Автор** (`post_author`) — автор поста (если доступен)

### Детальные таблицы

- **`post_reactions`** — детальные реакции (эмодзи, кастомные, платные)
- **`post_forwards`** — информация о репостах (откуда, когда)
- **`post_replies`** — комментарии и ответы на посты

### Автоматическое обновление

Триггеры автоматически обновляют счётчики при изменении связанных таблиц:
- При добавлении/удалении реакции → обновляется `reactions_count`
- При добавлении/удалении репоста → обновляется `forwards_count`
- При добавлении/удалении комментария → обновляется `replies_count`

## Примеры запросов

### Получить посты пользователя по подпискам
```sql
-- Установить контекст пользователя
SET LOCAL app.current_user_tg_id = '123456789';
SET LOCAL app.current_tenant_id = '00000000-0000-0000-0000-000000000000';

-- Получить последние посты
SELECT p.id, p.posted_at, c.title, p.content
FROM posts p
JOIN channels c ON c.id = p.channel_id
WHERE p.posted_at >= NOW() - INTERVAL '7 days'
ORDER BY p.posted_at DESC
LIMIT 50;
```

### Фильтр по тегам
```sql
-- Найти посты с определёнными тегами
SELECT p.id, p.content, pe.tags
FROM post_enrichment pe
JOIN posts p ON p.id = pe.post_id
WHERE pe.tags @> '[{"name":"design"}]'
ORDER BY p.posted_at DESC;
```

### Упоминания пользователя в группах
```sql
-- Найти упоминания пользователя
SELECT gm.content, g.title as group_title, gm.posted_at
FROM group_mentions gm
JOIN group_messages gm_msg ON gm_msg.id = gm.group_message_id
JOIN groups g ON g.id = gm_msg.group_id
WHERE gm.mentioned_user_tg_id = 123456789
  AND gm.is_processed = false
ORDER BY gm.created_at DESC;
```

### Статистика по каналам
```sql
-- Количество постов по каналам за месяц
SELECT 
    c.title,
    COUNT(p.id) as posts_count,
    COUNT(pm.id) as media_count
FROM channels c
LEFT JOIN posts p ON p.channel_id = c.id 
    AND p.posted_at >= DATE_TRUNC('month', NOW())
LEFT JOIN post_media pm ON pm.post_id = p.id
WHERE c.is_active = true
GROUP BY c.id, c.title
ORDER BY posts_count DESC;
```

## Триггеры и автоматизация

### Автоматическое обновление has_media
```sql
-- Триггер обновляет has_media при изменении post_media
CREATE OR REPLACE FUNCTION sync_post_has_media() RETURNS TRIGGER AS $$
BEGIN
  UPDATE posts p
     SET has_media = EXISTS(SELECT 1 FROM post_media pm WHERE pm.post_id = p.id)
   WHERE p.id = COALESCE(NEW.post_id, OLD.post_id);
  RETURN NULL;
END; $$ LANGUAGE plpgsql;
```

### Автоматическое обновление updated_at
```sql
-- Триггер для отслеживания изменений
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN 
  NEW.updated_at = NOW(); 
  RETURN NEW; 
END; $$ LANGUAGE plpgsql;
```

## Масштабирование

### Партиционирование posts
При объёме >10M записей:
```sql
-- Партиционирование по месяцам
CREATE TABLE posts_y2024m01 PARTITION OF posts
FOR VALUES FROM (202401) TO (202402);
```

### Мониторинг производительности
```sql
-- Размеры таблиц
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size
FROM pg_tables 
WHERE schemaname = 'public'
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Использование индексов
SELECT 
    schemaname,
    tablename,
    indexname,
    idx_scan,
    idx_tup_read,
    idx_tup_fetch
FROM pg_stat_user_indexes
WHERE schemaname = 'public'
ORDER BY idx_scan DESC;
```

## Безопасность

### Шифрование
- **Sensitive data** — PGP шифрование для API ключей
- **Passwords** — bcrypt с солью
- **Sessions** — зашифрованные Telethon StringSession

### Аудит
- **Audit trail** — через `created_at`, `updated_at`
- **Change tracking** — кто и когда изменил данные
- **Access logging** — через Supabase logs

### RLS проверки
```sql
-- Проверить активные RLS политики
SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual
FROM pg_policies 
WHERE schemaname = 'public'
ORDER BY tablename, policyname;
```
