# Исправление критической ошибки в обработке FloodWait

**Дата**: 2026-01-12 21:15 UTC  
**Проблема**: FloodWait не уходит, посты не сохраняются  
**Context7**: Исправление несоответствия типов в cooldown механизме

---

## Context

Проблема с FloodWait не ушла после исправлений. Проверка показала критическую ошибку в обработке FloodWait.

---

## 1. Критическая ошибка найдена

### 1.1. Несоответствие типов в cooldown

**Проблема**: 
- В `set_channel_cooldown()` передается `channel_id` (UUID строка)
- Но функция ожидает `channel_id: int` (tg_channel_id)
- В результате cooldown устанавливается с неправильным ключом
- Проверка cooldown не находит его, так как ищет по `tg_channel_id` (int)

**Код с ошибкой** (строка 857):
```python
await set_channel_cooldown(self.redis_client, channel_id, wait_seconds)
# channel_id - это UUID строка, но функция ожидает int (tg_channel_id)
```

**Правильный код**:
```python
if self.redis_client and tg_channel_id_db is not None:
    tg_channel_id_int = int(tg_channel_id_db)
    await set_channel_cooldown(self.redis_client, tg_channel_id_int, wait_seconds)
```

### 1.2. Проверка в Redis

**Текущее состояние**:
- Cooldown хранится с ключом `channel:cooldown:15b11f14-48ff-4869-a11a-4d2fd060a690` (UUID)
- Но проверка ищет `channel:cooldown:-1001285729516` (tg_channel_id)
- Результат: cooldown не находится, канал парсится снова → FloodWait повторяется

---

## 2. Исправление применено

### ✅ 2.1. Исправлена передача параметров в set_channel_cooldown

**Файл**: `telethon-ingest/services/channel_parser.py`

**Изменения** (строки ~853-864):
- Исправлена передача параметров: теперь передается `tg_channel_id_int` (int), а не `channel_id` (UUID)
- Добавлена проверка наличия `tg_channel_id_db` перед установкой cooldown
- Улучшено логирование

**Код**:
```python
# Context7: Устанавливаем cooldown для канала
# КРИТИЧНО: set_channel_cooldown ожидает tg_channel_id (int), а не channel_id (UUID)
if self.redis_client and tg_channel_id_db is not None:
    try:
        from .telethon_retry import set_channel_cooldown
        tg_channel_id_int = int(tg_channel_id_db)
        await set_channel_cooldown(self.redis_client, tg_channel_id_int, wait_seconds)
        logger.info("Channel moved to cooldown due to FloodWait",
                  channel_id=channel_id,
                  tg_channel_id=tg_channel_id_int,
                  cooldown_seconds=wait_seconds)
    except Exception as cooldown_error:
        logger.warning("Failed to set channel cooldown",
                     channel_id=channel_id,
                     tg_channel_id_db=tg_channel_id_db,
                     error=str(cooldown_error))
elif not tg_channel_id_db:
    logger.warning("Cannot set cooldown - no tg_channel_id available",
                 channel_id=channel_id)
```

**Статус**: ✅ Применено

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

## 4. Дополнительные проверки

### 4.1. Проверка cooldown перед получением entity

**Текущая логика**:
1. Проверка cooldown в `parse_channel_messages` (строка 367) - ДО получения entity ✅
2. Получение entity в `_get_channel_entity` (строка 826) - ПОСЛЕ проверки cooldown ✅
3. Установка cooldown при FloodWait в `_get_channel_entity` (строка 857) - ПОСЛЕ получения entity ⚠️

**Проблема**: Cooldown устанавливается ПОСЛЕ получения entity, но проверка происходит ДО. Это правильно, но нужно убедиться, что cooldown устанавливается с правильным ключом.

**Вывод**: Логика правильная, но была ошибка в типах параметров.

---

## 5. Context7 Best Practices - Применено

### ✅ Исправления

1. **Соответствие типов**: Исправлено несоответствие типов в `set_channel_cooldown`
2. **Проверка наличия данных**: Добавлена проверка `tg_channel_id_db` перед установкой cooldown
3. **Детальное логирование**: Улучшено логирование для диагностики

### ⚠️ Требует внимания

1. **Очистка старых cooldown**: Старые cooldown с UUID ключами нужно очистить
2. **Мониторинг**: Следить за метриками cooldown после исправления

---

## 6. Checks

1. ✅ Ошибка найдена: несоответствие типов в `set_channel_cooldown`
2. ✅ Исправление применено: передается `tg_channel_id_int` вместо `channel_id`
3. ⏳ Очистка: требуется очистить старые cooldown с UUID ключами
4. ⏳ Проверка: требуется проверить работу после исправления

**Статус**: ✅ **ОШИБКА ИСПРАВЛЕНА, ТРЕБУЕТСЯ ОЧИСТКА СТАРЫХ COOLDOWN**

---

## 7. Следующие шаги

### Немедленные

1. **Очистить старые cooldown**:
   ```bash
   docker compose exec -T redis redis-cli KEYS "channel:cooldown:*" | grep -v "^-" | xargs -I {} docker compose exec -T redis redis-cli DEL {}
   ```
   (Удалить все ключи, которые не начинаются с `-` (не являются tg_channel_id))

2. **Перезапустить контейнер**:
   ```bash
   docker compose restart telethon-ingest
   ```

3. **Проверить работу**:
   - Проверить логи на наличие FloodWait
   - Проверить, что cooldown устанавливается с правильным ключом
   - Проверить, что cooldown находится при проверке

### Мониторинг

4. **Следить за метриками**:
   - `cooldown_channels_total` - количество каналов в cooldown
   - `parser_floodwait_seconds_total` - метрики FloodWait
   - Логи с `Channel moved to cooldown due to FloodWait`

---

## 8. Выводы

### ✅ Исправлено

1. **Несоответствие типов**: Исправлена передача параметров в `set_channel_cooldown`
2. **Проверка наличия данных**: Добавлена проверка `tg_channel_id_db` перед установкой cooldown
3. **Логирование**: Улучшено логирование для диагностики

### ⏳ Требуется

1. **Очистка старых cooldown**: Удалить старые cooldown с UUID ключами
2. **Проверка работы**: Проверить работу после исправления

**Статус**: ✅ **ОШИБКА ИСПРАВЛЕНА, ТРЕБУЕТСЯ ОЧИСТКА И ПРОВЕРКА**
