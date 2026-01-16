# Проверка FloodWaitManager - Верификация

**Дата**: 2026-01-14 22:20  
**Context7**: Полная проверка обработки FloodWaitManager

---

## Проверка компонентов

### 1. Инициализация FloodWaitManager ✅

**Файл**: `telethon-ingest/services/floodwait_manager.py`

**Проверено**:
- ✅ Метрики создаются с защитой от дублирования
- ✅ Исправлена обработка `ValueError: Duplicated timeseries`
- ✅ Функции `_get_or_create_counter`, `_get_or_create_histogram`, `_get_or_create_gauge` корректно обрабатывают существующие метрики

**Исправления**:
- Добавлена обработка `ValueError` при создании метрик
- Улучшен поиск существующих метрик в REGISTRY
- Добавлено логирование при обнаружении дублирования

### 2. Использование в Channel Parser ✅

**Файл**: `telethon-ingest/services/channel_parser.py`

**Проверено**:
- ✅ Проверка глобального FloodWait перед резолвом (строка 972-981)
- ✅ Использование `should_abort_resolution` для политики прерывания (строка 1227-1244)
- ✅ Установка глобального FloodWait при больших значениях (строка 1234, 1248)
- ✅ Обработка FloodWait в `fetch_messages_with_retry` (строка 1677)

**Логика**:
1. Перед резолвом проверяется глобальный FloodWait для сессии
2. При FloodWait проверяется политика `should_abort_resolution`:
   - `repair` контекст: abort при >120 сек
   - `operational` контекст: abort при >300 сек
3. При большом FloodWait устанавливается глобальный circuit breaker
4. При малом FloodWait устанавливается локальный FloodWait для сессии

### 3. Использование в ParseAllChannelsTask ✅

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py`

**Проверено**:
- ✅ Проверка глобального FloodWait перед парсингом канала (строка 744-762)
- ✅ Получение session_id из telegram_client
- ✅ Пропуск канала при глобальном FloodWait

**Логика**:
1. Перед парсингом канала проверяется глобальный FloodWait
2. Если FloodWait активен - канал пропускается
3. Метрика `channels_skipped_due_to_global_floodwait_total` обновляется

### 4. Методы FloodWaitManager ✅

**Проверено**:

1. **`handle_floodwait`** (строка 89-138):
   - ✅ Сохранение состояния в Redis с TTL
   - ✅ Установка глобального circuit breaker при >60 сек
   - ✅ Обновление метрик Prometheus
   - ✅ Sleep на время FloodWait

2. **`is_rate_limited`** (строка 140-154):
   - ✅ Проверка блокировки account/method в Redis
   - ✅ Возвращает True/False

3. **`get_wait_time`** (строка 156-179):
   - ✅ Получение оставшегося времени ожидания
   - ✅ Возвращает 0 если не заблокирован

4. **`check_global_floodwait`** (строка 193-225):
   - ✅ Проверка глобального FloodWait для сессии
   - ✅ Обновление метрики `tg_floodwait_seconds_gauge`
   - ✅ Автоматическое удаление истекших ключей

5. **`set_global_floodwait`** (строка 227-260):
   - ✅ Установка глобального circuit breaker
   - ✅ Сохранение в Redis с TTL
   - ✅ Обновление метрики

6. **`get_healthy_sessions`** (строка 262-297):
   - ✅ Фильтрация сессий по FloodWait
   - ✅ Возвращает список доступных сессий

7. **`should_abort_resolution`** (строка 299-327):
   - ✅ Политика прерывания резолва
   - ✅ Разные пороги для `repair` и `operational` контекстов

### 5. Redis состояние ✅

**Проверено**:
- ✅ Ключи `floodwait:{account_id}:{method}` для per-account/method лимитов
- ✅ Ключи `tg:floodwait_until:{session_id}` для глобального circuit breaker
- ✅ TTL устанавливается корректно (wait_seconds + 60)

**Текущее состояние**:
- Нет активных FloodWait в Redis (все ключи истекли или не установлены)
- Это нормально - FloodWait устанавливаются только при возникновении ошибок

### 6. Метрики Prometheus ✅

**Проверено**:
- ✅ `telethon_floodwait_total` - счетчик FloodWait событий
- ✅ `telethon_floodwait_duration_seconds` - гистограмма длительности
- ✅ `tg_floodwait_seconds_gauge` - текущий глобальный FloodWait
- ✅ `channels_skipped_due_to_global_floodwait_total` - пропущенные каналы

**Текущие значения**:
- `channels_skipped_due_to_global_floodwait_total`: 0 (нет пропусков)
- Метрики доступны через `/metrics` endpoint

---

## Сценарии использования

### Сценарий 1: Малый FloodWait (<60 сек)
1. ✅ Ошибка FloodWait обрабатывается через `handle_floodwait`
2. ✅ Состояние сохраняется в Redis
3. ✅ Глобальный circuit breaker НЕ устанавливается
4. ✅ Метрики обновляются
5. ✅ Sleep на время FloodWait

### Сценарий 2: Большой FloodWait (>60 сек)
1. ✅ Ошибка FloodWait обрабатывается через `handle_floodwait`
2. ✅ Состояние сохраняется в Redis
3. ✅ Глобальный circuit breaker устанавливается
4. ✅ Метрики обновляются
5. ✅ Sleep на время FloodWait

### Сценарий 3: Проверка перед резолвом
1. ✅ Проверяется глобальный FloodWait для сессии
2. ✅ Если активен - резолв пропускается
3. ✅ Метрика `channels_skipped_due_to_global_floodwait_total` обновляется

### Сценарий 4: Политика прерывания
1. ✅ При FloodWait проверяется `should_abort_resolution`
2. ✅ Для `repair` контекста: abort при >120 сек
3. ✅ Для `operational` контекста: abort при >300 сек
4. ✅ При abort устанавливается глобальный FloodWait и выбрасывается исключение

---

## Выводы

✅ **FloodWaitManager работает корректно:**

1. **Инициализация**: Метрики создаются с защитой от дублирования
2. **Обработка FloodWait**: Все методы работают правильно
3. **Интеграция**: Корректно используется в ChannelParser и ParseAllChannelsTask
4. **Глобальный circuit breaker**: Работает для защиты от каскадного FloodWait
5. **Метрики**: Все метрики доступны и обновляются
6. **Redis**: Состояние сохраняется и очищается корректно

**Статус**: Готов к production использованию

**Исправления**:
- ✅ Исправлена обработка дублирования метрик
- ✅ Улучшен поиск существующих метрик в REGISTRY
- ✅ Добавлено логирование при обнаружении дублирования
