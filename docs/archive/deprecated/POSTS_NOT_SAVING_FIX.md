# Исправление проблемы сохранения новых постов

**Дата**: 2025-01-22  
**Context7**: Критическая ошибка SQL в `atomic_db_saver.py` блокировала сохранение новых постов

---

## Проблема

Новые посты не сохранялись в БД из-за SQL синтаксической ошибки в `atomic_db_saver.py`.

### Симптомы

1. **telethon-ingest парсит каналы** - логи показывают успешный парсинг
2. **Находит новые сообщения** - логи показывают `processed: 4, skipped: 1`
3. **Ошибка при сохранении** - SQL синтаксическая ошибка блокирует INSERT

### Ошибка в логах

```
[ERROR] syntax error at or near ":"
[SQL: 
    ...
    :forward_from_peer_id::jsonb, $29, $30,
    ...
]
```

### Причина

В SQL запросе использовался синтаксис `:forward_from_peer_id::jsonb`, который конфликтует с параметризацией SQLAlchemy/asyncpg. PostgreSQL не может обработать именованный параметр с оператором приведения типа `::jsonb` в одном выражении.

---

## Исправление

### Изменение

**Файл**: `telethon-ingest/services/atomic_db_saver.py`

**Было**:
```sql
:forward_from_peer_id::jsonb, :forward_from_chat_id, ...
```

**Стало**:
```sql
CAST(:forward_from_peer_id AS jsonb), :forward_from_chat_id, ...
```

### Обоснование

1. **CAST() синтаксис** - стандартный SQL способ приведения типов, совместимый с параметризацией
2. **Данные уже сериализованы** - в строке 781 `forward_from_peer_id` уже сериализуется в JSON строку через `json.dumps()`
3. **Context7 best practice** - использование стандартных SQL конструкций вместо PostgreSQL-специфичных операторов в параметризованных запросах

---

## Проверка

### Команды для проверки

```bash
# Проверка последних постов в БД
docker compose exec supabase-db psql -U postgres -d postgres -c "
SELECT id, channel_id, telegram_message_id, created_at 
FROM posts 
ORDER BY created_at DESC 
LIMIT 10;
"

# Проверка логов telethon-ingest на ошибки
docker compose logs telethon-ingest --tail 100 | grep -E "(error|ERROR|syntax)"

# Проверка событий в Redis Stream
docker compose exec redis redis-cli XREVRANGE stream:posts:parsed + - COUNT 5
```

### Ожидаемый результат

1. ✅ Новые посты сохраняются в БД без ошибок
2. ✅ События `posts.parsed` публикуются в Redis Stream
3. ✅ Worker обрабатывает события и сохраняет посты через `post_persistence`

---

## Impact / Rollback

### Влияние

- ✅ **Критично**: Блокировало сохранение всех новых постов
- ✅ **Исправлено**: Теперь посты должны сохраняться корректно
- ✅ **Обратная совместимость**: Изменение не влияет на существующие данные

### Rollback

Если потребуется откат:

1. Вернуть `:forward_from_peer_id::jsonb` в SQL запрос
2. Перезапустить `telethon-ingest`: `docker compose restart telethon-ingest`

**Примечание**: Откат вернет проблему, поэтому не рекомендуется.

---

## Context7 Best Practices

1. ✅ **Использование CAST() вместо ::** - стандартный SQL синтаксис для приведения типов
2. ✅ **Параметризация запросов** - все значения передаются через параметры, не через строковую интерполяцию
3. ✅ **Обработка ошибок** - детальное логирование ошибок для диагностики
4. ✅ **Мониторинг** - метрики для отслеживания успешных/неуспешных операций

---

## Статус

✅ **Исправлено** - SQL синтаксическая ошибка устранена, новые посты должны сохраняться корректно.

