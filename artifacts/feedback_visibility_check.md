# Проверка видимости feedback для админов

## Context
Проверка того, что все feedback, оставленные пользователями, видны админам.

## Анализ кода

### 1. Создание feedback (`POST /api/feedback/`)

**Файл:** `api/routers/feedback.py:68-140`

**Логика:**
- `user_id` определяется из тела запроса или из JWT токена
- Пользователь загружается из БД
- `tenant_id` берется из `user.tenant_id`
- Feedback создается с этим `tenant_id`

**Проверки:**
- ✅ `user_id` обязателен (проверка на строке 93-94)
- ✅ Пользователь должен существовать (проверка на строке 97-99)
- ✅ `tenant_id` теперь проверяется на None (добавлена проверка)

**Потенциальные проблемы:**
- ❌ Раньше не было явной проверки на `tenant_id == None` (теперь добавлена)
- ⚠️ Если `user.tenant_id` None (не должно быть из-за `nullable=False`), то будет ошибка БД при создании

### 2. Просмотр списка feedback админом (`GET /api/feedback/`)

**Файл:** `api/routers/feedback.py:143-216`

**Логика:**
- Админ определяется через `get_admin_user` dependency
- Фильтр: `UserFeedback.tenant_id == admin_user.tenant_id`
- Опциональные фильтры: по статусу и `user_id`

**Проверки:**
- ✅ Только админы могут получить список (через `get_admin_user`)
- ✅ Фильтрация по `tenant_id` админа (строка 166)
- ✅ Все feedback своего tenant видны админу

**Потенциальные проблемы:**
- ✅ Нет проблем - все feedback своего tenant видны админу

### 3. Просмотр конкретного feedback (`GET /api/feedback/{feedback_id}`)

**Файл:** `api/routers/feedback.py:219-276`

**Логика:**
- Админ может видеть feedback своего tenant
- Пользователь может видеть только свой feedback

**Проверки:**
- ✅ Админ видит feedback своего tenant (строка 244)
- ✅ Пользователь видит только свой feedback (строка 259)

**Потенциальные проблемы:**
- ✅ Нет проблем - правильная изоляция по tenant

### 4. Модель данных

**Файл:** `api/models/database.py:225-254`

**Поля:**
- `tenant_id`: `nullable=False` - обязательное поле
- `user_id`: `nullable=False` - обязательное поле
- Foreign key constraints с `ondelete="CASCADE"`

**Проверки:**
- ✅ `tenant_id` обязателен на уровне БД
- ✅ `user_id` обязателен на уровне БД
- ✅ Индексы для производительности запросов

## Выводы

### ✅ Что работает правильно:

1. **Все feedback имеют tenant_id** - поле обязательное (`nullable=False`)
2. **Админы видят все feedback своего tenant** - фильтр по `tenant_id` работает корректно
3. **Правильная изоляция** - админы не видят feedback других tenant
4. **Валидация при создании** - проверки на существование пользователя и наличие `tenant_id`

### ⚠️ Потенциальные проблемы (исправлены):

1. **Отсутствие явной проверки на `tenant_id == None`** - добавлена проверка в `create_feedback`
   - Раньше: если `user.tenant_id` None, была бы ошибка БД
   - Теперь: явная проверка с понятным сообщением об ошибке

### 📊 Статистика (нужно проверить в БД):

Для полной проверки нужно выполнить SQL запросы:

```sql
-- Проверка feedback без tenant_id (не должно быть)
SELECT COUNT(*) FROM user_feedback WHERE tenant_id IS NULL;

-- Проверка feedback с несуществующими tenant_id
SELECT COUNT(*) FROM user_feedback uf
WHERE NOT EXISTS (SELECT 1 FROM tenants t WHERE t.id = uf.tenant_id);

-- Проверка feedback с несуществующими user_id
SELECT COUNT(*) FROM user_feedback uf
WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id = uf.user_id);

-- Статистика по tenant
SELECT 
    t.name as tenant_name,
    COUNT(uf.id) as feedback_count,
    COUNT(DISTINCT u.id) FILTER (WHERE u.role = 'admin') as admin_count
FROM tenants t
LEFT JOIN user_feedback uf ON uf.tenant_id = t.id
LEFT JOIN users u ON u.tenant_id = t.id
GROUP BY t.id, t.name
ORDER BY feedback_count DESC;
```

## Рекомендации

1. ✅ **Добавлена проверка на `tenant_id == None`** в `create_feedback`
2. 🔄 **Запустить SQL запросы** для проверки данных в БД
3. 📝 **Добавить мониторинг** - метрики по количеству feedback по tenant
4. 🧪 **Добавить тесты** - проверка видимости feedback для админов

## Изменения

### `api/routers/feedback.py`

Добавлена проверка на `tenant_id == None` перед созданием feedback:

```python
# Context7: Проверка, что tenant_id установлен (не должно быть None из-за nullable=False, но проверяем для безопасности)
if not tenant_id:
    logger.error(
        "User has no tenant_id when creating feedback",
        user_id=str(user.id)
    )
    raise HTTPException(
        status_code=400,
        detail="User tenant_id is not set. Cannot create feedback."
    )
```

## Checks

Для проверки видимости feedback:

1. **Создать feedback через бота:**
   ```bash
   # Отправить команду /feedback в боте
   ```

2. **Проверить через API (как админ):**
   ```bash
   curl -H "Authorization: Bearer $ADMIN_TOKEN" \
        "https://$API_HOST/api/feedback/?limit=100"
   ```

3. **Проверить через веб-интерфейс:**
   - Открыть админ-панель
   - Перейти в раздел Feedback
   - Убедиться, что все feedback видны

4. **Проверить SQL запросами:**
   - Выполнить SQL запросы из раздела "Статистика"

## Impact / Rollback

**Impact:**
- ✅ Безопасность: добавлена проверка на `tenant_id`
- ✅ Логирование: добавлено логирование ошибки при отсутствии `tenant_id`
- ✅ UX: более понятное сообщение об ошибке

**Rollback:**
- Удалить проверку на `tenant_id == None` (строки 103-111 в `api/routers/feedback.py`)
