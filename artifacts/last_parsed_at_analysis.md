# 📊 ПОЛНЫЙ АНАЛИЗ ЛОГИКИ last_parsed_at

## ⚙️ КОНФИГУРАЦИЯ

- `PARSER_INCREMENTAL_MINUTES`: 5 минут
  - Используется как fallback, когда нет `last_parsed_at`
  - НЕ ограничивает диапазон парсинга (исправлено)
  
- `PARSER_LPA_MAX_AGE_HOURS`: 48 часов
  - Safeguard: если `last_parsed_at` старше 48 часов → форсим historical режим
  
- `PARSER_MODE_OVERRIDE`: auto
  - Автоматическое определение режима на основе `last_parsed_at`

## 🔄 ЖИЗНЕННЫЙ ЦИКЛ last_parsed_at

### 1. ОПРЕДЕЛЕНИЕ РЕЖИМА ПАРСИНГА (`_decide_mode`)

```python
if last_parsed_at is None:
    → historical режим
elif age(last_parsed_at) > 48 hours:
    → historical режим (safeguard)
else:
    → incremental режим
```

### 2. ВЫЧИСЛЕНИЕ since_date (`_get_since_date`)

**Historical режим:**
- `since_date = now - 24 hours`

**Incremental режим:**
- Если есть `last_parsed_at`:
  - ✅ `since_date = last_parsed_at` (ИСПРАВЛЕНО)
  - ❌ БЫЛО: `since_date = max(last_parsed_at, now - 5 min)` → пропуск сообщений
- Если нет `last_parsed_at` (fallback):
  - `since_date = now - 5 minutes`

**Safeguard:**
- Если `last_parsed_at` > 48 часов:
  - Форсим historical: `since_date = now - 24 hours`

### 3. ОБНОВЛЕНИЕ ПОСЛЕ ПАРСИНГА (`_update_last_parsed_at`)

**Когда обновляется:**
- ✅ ВСЕГДА после завершения парсинга (даже если 0 сообщений)
- ✅ После cooldown skip (для отслеживания попыток)

**Как обновляется:**
```python
UPDATE channels SET last_parsed_at = NOW() WHERE id = channel_id
```

**Дополнительные действия:**
- Удаляется Redis HWM (`parse_hwm:{channel_id}`)
- Логируется обновление

## 🐛 ПРОБЛЕМА, КОТОРАЯ БЫЛА ИСПРАВЛЕНА

### До исправления:

```python
# ❌ НЕПРАВИЛЬНО
return max(base, now - timedelta(minutes=self.config.incremental_minutes))
```

**Проблема:**
- Если `last_parsed_at` = 10 минут назад
- `since_date` = max(10 мин назад, now - 5 мин) = now - 5 минут
- **ПРОПУСК**: сообщения между 10 и 5 минутами назад не парсятся!

### После исправления:

```python
# ✅ ПРАВИЛЬНО
return base  # Используем именно last_parsed_at
```

**Результат:**
- `since_date` = `last_parsed_at` (точно)
- Все сообщения между `last_parsed_at` и `now` будут парситься

## 📍 ГДЕ ИСПОЛЬЗУЕТСЯ last_parsed_at

1. **parse_all_channels_task.py** (`_decide_mode`)
   - Определение режима парсинга (historical/incremental)

2. **channel_parser.py** (`_get_since_date`)
   - Вычисление `since_date` для фильтрации сообщений

3. **channel_parser.py** (`_update_last_parsed_at`)
   - Обновление после парсинга

4. **channel_parser.py** (cooldown skip)
   - Обновление для отслеживания попыток

5. **Redis HWM** (`parse_hwm:{channel_id}`)
   - Временное хранилище до обновления `last_parsed_at`
   - Удаляется после успешного обновления

## ✅ ТЕКУЩЕЕ СОСТОЯНИЕ

- ✅ Логика исправлена
- ✅ `since_date` = `last_parsed_at` в incremental режиме
- ✅ Сообщения не пропускаются
- ✅ Обновление работает корректно

## 📋 РЕКОМЕНДАЦИИ

1. **Мониторинг:**
   - Следить за каналами с `last_parsed_at` > 1 часа (возможно, нет новых сообщений)
   - Проверять каналы с NULL `last_parsed_at` (должны парситься в historical режиме)

2. **Оптимизация:**
   - Рассмотреть уменьшение `PARSER_INCREMENTAL_MINUTES` для более частого парсинга
   - Настроить `PARSER_LPA_MAX_AGE_HOURS` в зависимости от частоты постов в каналах

3. **Тестирование:**
   - Проверить парсинг каналов после исправления
   - Убедиться, что новые сообщения не пропускаются
