# Multi-Tenant Architecture (Context7)

## Обзор

Система поддерживает multi-tenant архитектуру, где один Telegram-пользователь может быть участником нескольких тенантов с разными правами и настройками в каждом.

## Ключевые концепции

### Identity (Глобальная личность)

**Identity** — глобальная сущность, представляющая Telegram-пользователя.

- **telegram_id**: Уникальный идентификатор Telegram (BIGINT, UNIQUE)
- **id**: UUID первичный ключ
- **meta**: JSON метаданные
- **created_at**: Время создания

Один `telegram_id` → одна `Identity`.

### Membership (Участник тенанта)

**Membership** (таблица `users`) — связь между Identity и Tenant.

- **id**: UUID первичный ключ
- **tenant_id**: FK → tenants.id (ON DELETE CASCADE)
- **identity_id**: FK → identities.id (ON DELETE RESTRICT)
- **telegram_id**: Dual-write для обратной совместимости (не уникален!)
- **tier**: Тарифный план (free/basic/premium/pro)
- Другие поля: username, first_name, last_name, settings, etc.

**Уникальность**: `UNIQUE(tenant_id, identity_id)` — один Telegram-пользователь может быть только один раз в каждом тенанте.

## Модель данных

```
┌─────────────┐         ┌─────────────┐         ┌──────────┐
│  Identity   │         │  Membership │         │  Tenant  │
│             │         │   (users)   │         │          │
├─────────────┤         ├─────────────┤         ├──────────┤
│ id (PK)     │◄───────┤│ identity_id │         │ id (PK)  │
│ telegram_id │         │ tenant_id   ├────────►│ name     │
│ meta        │         │ telegram_id │         │ settings │
└─────────────┘         │ tier        │         └──────────┘
                        │ username    │
                        │ ...         │
                        └─────────────┘
```

**Пример**: Telegram-пользователь с `telegram_id=123456789` может быть:
- В Tenant A с tier="pro"
- В Tenant B с tier="free"

Это два разных Membership, но одна Identity.

## Row Level Security (RLS)

Для изоляции данных используется PostgreSQL Row Level Security:

### Включение RLS

```sql
-- Включено на таблицах:
- users
- identities  
- channels
- telegram_sessions
- posts
```

### Политики RLS

```sql
-- Пример для users:
CREATE POLICY users_tenant_isolation ON users
  FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid);
```

RLS автоматически фильтрует данные по `app.tenant_id`, установленному middleware из JWT токена.

### Использование в коде

```python
# Middleware автоматически устанавливает app.tenant_id из JWT
# В get_db() устанавливается через set_tenant_id_in_session()

from api.models.database import get_db
from dependencies.auth import get_current_tenant_id

def my_endpoint(request: Request, db: Session = Depends(get_db)):
    tenant_id = get_current_tenant_id(request)  # Из JWT
    # Все запросы через db автоматически фильтруются по tenant_id
    users = db.query(User).all()  # Только для текущего tenant
```

## JWT Payload

JWT токены содержат расширенный payload для multi-tenant:

```json
{
  "sub": "123456789",           // telegram_id (legacy)
  "tenant_id": "uuid-tenant",   // ID тенанта
  "membership_id": "uuid-user", // ID membership
  "identity_id": "uuid-identity", // ID identity
  "tier": "pro",                // Тарифный план
  "exp": 1234567890,
  "iat": 1234567890
}
```

## Утилиты (Context7: DRY)

### Извлечение tenant_id из JWT

```python
from dependencies.auth import get_current_tenant_id, get_current_tenant_id_optional

# Обязательный (бросит 401 если нет)
tenant_id = get_current_tenant_id(request)

# Опциональный (вернёт None если нет)
tenant_id = get_current_tenant_id_optional(request)
```

### Upsert Identity и Membership

```python
from utils.identity_membership import (
    upsert_identity_and_membership_sync,  # Для синхронного кода (API)
    upsert_identity_and_membership_async  # Для асинхронного кода (воркеры)
)

# Синхронная версия (API)
identity_id, user_id = upsert_identity_and_membership_sync(
    db=db,
    tenant_id=tenant.id,
    telegram_id=123456789,
    username="user",
    first_name="John",
    tier="pro"
)

# Асинхронная версия (воркеры)
identity_id, user_id = await upsert_identity_and_membership_async(
    db_session=async_session,
    tenant_id=str(tenant.id),
    telegram_id=123456789,
    username="user",
    tier="free"
)
```

## Миграции

### 1. `20251103_add_identities`
- Создание таблицы `identities`
- Добавление `users.identity_id` (nullable)
- Создание FK и индексов

### 2. `20251103_backfill_identity`
- Backfill данных из `users.telegram_id` в `identities`
- Установка `identity_id` NOT NULL
- Создание `UNIQUE(tenant_id, identity_id)`

### 3. `20251103_enable_rls`
- Включение RLS на таблицах
- Создание политик изоляции

### 4. `20251103_remove_telegram_id_uniq`
- Удаление старого `UNIQUE` на `users.telegram_id`

## Feature Flags

В `api/config.py`:

```python
feature_rls_enabled: bool = False  # Включить RLS (поэтапный rollout)
feature_identity_enabled: bool = True  # Использование Identity модели
feature_rate_limit_per_user: bool = True  # Per-membership rate limiting
```

## Redis Namespacing

Все Redis ключи используют префикс `t:{tenant_id}:*`:

```
t:{tenant_id}:qr:session
t:{tenant_id}:session
t:{tenant_id}:lock:qr
t:{tenant_id}:rl:membership:{id}:{route}
```

## Qdrant Collections

Per-tenant коллекции для векторного поиска:

```
t{tenant_id}_posts  # Вместо user_{tenant_id}_posts
```

Фильтрация по `tenant_id` обязательна во всех поисковых запросах.

## Best Practices

### 1. Всегда используйте tenant_id из JWT

```python
# ✅ Правильно
tenant_id = get_current_tenant_id(request)
users = db.query(User).filter(User.tenant_id == tenant_id).all()

# ❌ Неправильно (без изоляции)
users = db.query(User).all()
```

### 2. Используйте Identity для поиска пользователей

```python
# ✅ Правильно (multi-tenant aware)
identity = db.query(Identity).filter(Identity.telegram_id == telegram_id).first()
user = db.query(User).filter(
    User.identity_id == identity.id,
    User.tenant_id == tenant_id
).first()

# ❌ Неправильно (может вернуть пользователя из другого tenant)
user = db.query(User).filter(User.telegram_id == telegram_id).first()
```

### 3. Используйте общие утилиты

```python
# ✅ Используйте общие утилиты из utils.identity_membership
from utils.identity_membership import upsert_identity_and_membership_sync

# ❌ Не дублируйте логику upsert
```

## Тестирование

Запуск тестов multi-tenant:

```bash
docker compose exec api python /opt/telegram-assistant/scripts/test_multitenant_simple.py
```

Или через SQL:

```sql
-- Создание тестовых данных
DO $$
DECLARE
    v_tenant1 UUID;
    v_tenant2 UUID;
    v_identity UUID;
BEGIN
    -- Создаём tenants
    INSERT INTO tenants (id, name) VALUES 
        (gen_random_uuid(), 'Tenant 1'),
        (gen_random_uuid(), 'Tenant 2')
    RETURNING id INTO v_tenant1;
    
    -- Создаём identity
    INSERT INTO identities (id, telegram_id) 
    VALUES (gen_random_uuid(), 999888777)
    ON CONFLICT DO NOTHING
    RETURNING id INTO v_identity;
    
    -- Создаём memberships в разных tenants
    INSERT INTO users (tenant_id, identity_id, telegram_id, tier)
    VALUES 
        (v_tenant1, v_identity, 999888777, 'pro'),
        (v_tenant2, v_identity, 999888777, 'free')
    ON CONFLICT (tenant_id, identity_id) DO UPDATE SET tier = EXCLUDED.tier;
END $$;
```

## Откат (Rollback)

Если нужно откатить multi-tenant:

1. Выключить RLS: `feature_rls_enabled = False`
2. Откатить миграции (в обратном порядке):
   ```bash
   alembic downgrade 20251103_add_identities
   ```
3. Использовать старый код поиска по `telegram_id` напрямую

**Внимание**: Откат возможен только если:
- Нет дубликатов `telegram_id` в разных tenants
- Все `identity_id` можно восстановить из `telegram_id`

## Дополнительная документация

- [Context7 Best Practices](./CONTEXT7_BEST_PRACTICES.md)
- [Database Schema](./DATABASE_SCHEMA.md)
- [Architecture Principles](./ARCHITECTURE_PRINCIPLES.md)

