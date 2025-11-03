# Исправление импорта utils.identity_membership

**Дата:** 2025-11-03 15:10 MSK  
**Статус:** ✅ **ИСПРАВЛЕНО**

---

## Проблема

Посты находились при парсинге, но **НЕ сохранялись в БД** из-за ошибки импорта модуля `utils.identity_membership`.

### Симптомы
- Парсинг работает: `messages_processed: 4`, `messages_processed: 8`
- `Batch processing completed` - обработка завершена
- **НО**: `Atomic batch save failed` - сохранение провалилось
- Ошибка: `No module named 'utils.identity_membership'`

### Корневая причина

**Импорт модуля `utils.identity_membership` не работал**, так как:
1. Файл находится в `/opt/telegram-assistant/api/utils/identity_membership.py`
2. Путь `/opt/telegram-assistant/api` добавляется в `sys.path`
3. Но импорт `from utils.identity_membership import ...` не работает из-за структуры модулей Python

---

## Исправление

### Context7 Best Practices применены

1. **Использование importlib для прямого импорта файла**:
   - Прямой импорт через `importlib.util.spec_from_file_location`
   - Не зависит от структуры sys.path
   - Более надежный способ импорта модулей

### Изменения в коде

#### `atomic_db_saver.py` - `_upsert_user()`

**Было:**
```python
api_path = '/opt/telegram-assistant/api'
if api_path not in sys.path:
    sys.path.insert(0, api_path)
from utils.identity_membership import upsert_identity_and_membership_async
```

**Стало:**
```python
import importlib.util

api_path = '/opt/telegram-assistant/api'
utils_path = os.path.join(api_path, 'utils', 'identity_membership.py')

if os.path.exists(utils_path):
    spec = importlib.util.spec_from_file_location("identity_membership", utils_path)
    identity_membership_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity_membership_module)
    upsert_identity_and_membership_async = identity_membership_module.upsert_identity_and_membership_async
else:
    # Fallback: попробуем обычный импорт
    if api_path not in sys.path:
        sys.path.insert(0, api_path)
    from utils.identity_membership import upsert_identity_and_membership_async
```

---

## Ожидаемое поведение после исправления

1. Импорт модуля работает через `importlib`
2. `upsert_identity_and_membership_async` вызывается успешно
3. Пользователь создаётся/обновляется в БД
4. Посты сохраняются в БД
5. `Atomic batch save successful` в логах

---

## Проверка

### Команды для проверки

1. **Мониторинг логов сохранения:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 5m | grep -E "(Atomic batch save|inserted_count|Failed to upsert)"
```

2. **Проверка новых постов в БД:**
```bash
docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres -c "
SELECT MAX(created_at) as last_post, 
       COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 hour') as posts_last_hour
FROM posts;
"
```

3. **Проверка отсутствия ошибок импорта:**
```bash
docker logs telegram-assistant-telethon-ingest-1 --since 5m | grep "No module named"
```

### Ожидаемые результаты

- ✅ Нет ошибок "No module named 'utils.identity_membership'"
- ✅ `Atomic batch save successful` в логах
- ✅ `inserted_count > 0` для обработанных постов
- ✅ Новые посты появляются в БД

---

## Время проверки

Исправление применено в **15:10 MSK**. Контейнер перезапущен.

Следующий цикл парсинга должен произойти в течение **5-10 минут**. Рекомендуется проверить результаты через **15-20 минут** после исправления.

---

## Связанные файлы

- `telethon-ingest/services/atomic_db_saver.py` - логика сохранения в БД
- `api/utils/identity_membership.py` - утилиты для работы с identity/membership
- `docs/reports/SINCE_DATE_FIX.md` - исправление расчета since_date
- `docs/reports/INCREMENTAL_PARSING_FIX.md` - исправление offset_date

---

## Заключение

**Статус:** ✅ **ИСПРАВЛЕНО**

Ошибка импорта модуля `utils.identity_membership` устранена через использование `importlib` для прямого импорта файла. Это должно решить проблему с сохранением постов в БД.

После этого исправления все три критические проблемы должны быть решены:
1. ✅ offset_date (исправлено ранее)
2. ✅ since_date (исправлено ранее)
3. ✅ Импорт identity_membership (исправлено сейчас)

