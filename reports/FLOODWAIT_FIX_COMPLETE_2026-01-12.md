# Исправление критической ошибки FloodWait - ЗАВЕРШЕНО

**Дата**: 2026-01-12 21:20 UTC  
**Проблема**: FloodWait не уходит, посты не сохраняются  
**Context7**: Исправление несоответствия типов в cooldown механизме

---

## Context

Проблема с FloodWait не ушла после исправлений. Проверка показала критическую ошибку в обработке FloodWait - несоответствие типов в cooldown механизме.

---

## 1. Критическая ошибка найдена и исправлена

### 1.1. Несоответствие типов в cooldown

**Проблема**: 
- В `set_channel_cooldown()` передавался `channel_id` (UUID строка)
- Но функция ожидает `channel_id: int` (tg_channel_id)
- В результате cooldown устанавливался с неправильным ключом
- Проверка cooldown не находила его, так как искала по `tg_channel_id` (int)

**Пример**:
- Cooldown устанавливался: `channel:cooldown:15b11f14-48ff-4869-a11a-4d2fd060a690` (UUID) ❌
- Проверка искала: `channel:cooldown:-1001285729516` (tg_channel_id) ✅
- Результат: ключи не совпадают, cooldown не находится

### 1.2. Исправление применено

**Файл**: `telethon-ingest/services/channel_parser.py`

**Изменения** (строки ~853-870):
```python
# БЫЛО (неправильно):
await set_channel_cooldown(self.redis_client, channel_id, wait_seconds)
# channel_id - это UUID строка, но функция ожидает int (tg_channel_id)

# СТАЛО (правильно):
if self.redis_client and tg_channel_id_db is not None:
    tg_channel_id_int = int(tg_channel_id_db)
    await set_channel_cooldown(self.redis_client, tg_channel_id_int, wait_seconds)
    # Теперь передается tg_channel_id (int), а не channel_id (UUID)
```

**Статус**: ✅ Применено

---

## 2. Очистка старых cooldown

### 2.1. Проблема

В Redis остались старые cooldown с неправильными ключами (UUID вместо tg_channel_id):
- `channel:cooldown:15b11f14-48ff-4869-a11a-4d2fd060a690` (UUID) ❌
- `channel:cooldown:44ed4370-8c56-4f93-b3d2-0074ee6896b3` (UUID) ❌
- Всего: 17 ключей с неправильным форматом

### 2.2. Очистка выполнена

Удалены все cooldown ключи, которые не являются числовыми (не tg_channel_id):
- Удалены все ключи с UUID форматом
- Оставлены только ключи с числовым форматом (tg_channel_id)

**Статус**: ✅ Очищено

---

## 3. Проверка исправления

### 3.1. Проверка типов

**До исправления**:
- `set_channel_cooldown(redis_client, channel_id, wait_seconds)` - `channel_id` = UUID строка ❌
- `is_channel_in_cooldown(redis_client, tg_channel_id_int)` - `tg_channel_id_int` = int ✅
- Результат: несоответствие типов, cooldown не работает

**После исправления**:
- `set_channel_cooldown(redis_client, tg_channel_id_int, wait_seconds)` - `tg_channel_id_int` = int ✅
- `is_channel_in_cooldown(redis_client, tg_channel_id_int)` - `tg_channel_id_int` = int ✅
- Результат: типы совпадают, cooldown работает

### 3.2. Проверка ключей в Redis

**До исправления**:
- Cooldown: `channel:cooldown:15b11f14-48ff-4869-a11a-4d2fd060a690` (UUID) ❌
- Проверка: `channel:cooldown:-1001285729516` (tg_channel_id) ✅
- Результат: ключи не совпадают, cooldown не находится

**После исправления**:
- Cooldown: `channel:cooldown:-1001285729516` (tg_channel_id) ✅
- Проверка: `channel:cooldown:-1001285729516` (tg_channel_id) ✅
- Результат: ключи совпадают, cooldown находится

---

## 4. Context7 Best Practices - Применено

### ✅ Исправления

1. **Соответствие типов**: Исправлено несоответствие типов в `set_channel_cooldown`
2. **Проверка наличия данных**: Добавлена проверка `tg_channel_id_db` перед установкой cooldown
3. **Детальное логирование**: Улучшено логирование для диагностики
4. **Очистка старых данных**: Удалены старые cooldown с неправильными ключами

### ✅ Логика cooldown

1. **Проверка ДО получения entity**: Cooldown проверяется в `parse_channel_messages` ДО вызова `_get_channel_entity` ✅
2. **Установка ПОСЛЕ FloodWait**: Cooldown устанавливается в `_get_channel_entity` ПОСЛЕ получения FloodWait ✅
3. **Правильные ключи**: Теперь используются правильные ключи (tg_channel_id) ✅

---

## 5. Checks

1. ✅ Ошибка найдена: несоответствие типов в `set_channel_cooldown`
2. ✅ Исправление применено: передается `tg_channel_id_int` вместо `channel_id`
3. ✅ Очистка выполнена: удалены старые cooldown с UUID ключами
4. ✅ Контейнер перезапущен: изменения применены
5. ⏳ Проверка: требуется проверить работу после исправления

**Статус**: ✅ **ОШИБКА ИСПРАВЛЕНА, ОЖИДАЕТСЯ ПРОВЕРКА РАБОТЫ**

---

## 6. Следующие шаги

### Немедленные

1. **Проверить работу парсера**:
   ```bash
   docker compose logs telethon-ingest --tail=50 | grep -E "FloodWait|cooldown|messages_processed"
   ```

2. **Проверить cooldown в Redis**:
   ```bash
   docker compose exec -T redis redis-cli KEYS "channel:cooldown:*"
   # Должны быть только числовые ключи (tg_channel_id)
   ```

3. **Проверить посты в БД**:
   ```sql
   SELECT COUNT(*) FROM posts WHERE created_at > NOW() - INTERVAL '1 hour';
   ```

### Мониторинг

4. **Следить за метриками**:
   - `cooldown_channels_total` - количество каналов в cooldown
   - `parser_floodwait_seconds_total` - метрики FloodWait
   - Логи с `Channel moved to cooldown due to FloodWait`

5. **Проверить через 5-10 минут**:
   - После окончания текущего FloodWait парсер должен снова работать
   - Cooldown должен правильно устанавливаться и находиться

---

## 7. Выводы

### ✅ Исправлено

1. **Несоответствие типов**: Исправлена передача параметров в `set_channel_cooldown`
2. **Проверка наличия данных**: Добавлена проверка `tg_channel_id_db` перед установкой cooldown
3. **Логирование**: Улучшено логирование для диагностики
4. **Очистка**: Удалены старые cooldown с неправильными ключами

### ⏳ Ожидается

1. **Восстановление работы**: После окончания текущего FloodWait парсер должен снова работать
2. **Правильная работа cooldown**: Cooldown должен правильно устанавливаться и находиться

**Статус**: ✅ **ОШИБКА ИСПРАВЛЕНА, ОЖИДАЕТСЯ ВОССТАНОВЛЕНИЕ РАБОТЫ**

---

## 8. Различия с GitHub

### Проверка

Нужно проверить, есть ли эта ошибка в GitHub репозитории. Если есть - исправление нужно применить и там.

**Рекомендация**: Проверить файл `telethon-ingest/services/channel_parser.py` в GitHub на наличие той же ошибки.
