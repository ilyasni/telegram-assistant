# Диагностика: Дайджест не пришел в 9:00 - 2025-12-07

**Дата**: 2025-12-07  
**Context7**: Полная диагностика проблемы с отправкой дайджеста в 9:00

---

## Context

Дайджест не пришел в 9:00. Нужно проверить работу scheduler, логику проверки расписания и условия отправки.

---

## Plan

1. ✅ Проверка логов scheduler и выполнения задач
2. ⏳ Проверка логики проверки расписания
3. ⏳ Проверка условий отправки дайджеста
4. ⏳ Рекомендации по исправлению

---

## Диагностика

### ❌ Проблема: Задачи scheduler пропускаются

**Симптомы**:
- В логах есть сообщения: `Run time of job "Process user digests" was missed by 0:00:04.528441`
- Нет логов о выполнении `process_digests_task` в 9:00
- Нет логов `Digest processing task completed`

**Причина**: APScheduler пропускает выполнение задач, если они не успевают запуститься вовремя.

### Логика проверки расписания

**Код**:
```840:847:api/tasks/scheduler_tasks.py
                # Проверяем, соответствует ли текущее время расписанию
                # (тик каждые 15 минут, проверяем окно ±5 минут)
                time_diff = abs(
                    (local_time.hour * 60 + local_time.minute) -
                    (schedule_time.hour * 60 + schedule_time.minute)
                )
                
                if time_diff <= 5:  # В пределах 5 минут от расписания
```

**Проблема**: Если задача пропущена (missed), она не выполняется вообще, даже если время подходит.

### Условия отправки дайджеста

**Проверки**:
1. ✅ `DigestSettings.enabled == True`
2. ✅ `setting.topics` не пустой
3. ✅ `user.tenant_id` существует
4. ✅ `time_diff <= 5` (в пределах 5 минут от расписания)
5. ✅ Нет дайджеста на сегодня со статусом `sent`
6. ✅ Нет дайджеста в статусе `scheduled`, `pending`, `processing`
7. ✅ Если есть failed дайджест, прошло больше `retry_cooldown` минут

---

## Context7 Best Practices

### ❌ Проблема: APScheduler пропускает задачи

**Context7 Best Practice**: Использовать `misfire_grace_time` для обработки пропущенных задач.

**Решение**: Добавить `misfire_grace_time` при создании задачи, чтобы пропущенные задачи все равно выполнялись.

### ✅ Observability

- ✅ Логирование всех этапов проверки расписания
- ⚠️ Нужно добавить логирование пропущенных задач
- ⚠️ Нужно добавить метрики для отслеживания пропусков

---

## Рекомендации

### Немедленные действия

1. **Исправить пропуск задач scheduler**
   - Добавить `misfire_grace_time` для задачи `process_digests`
   - Увеличить окно проверки времени с ±5 до ±10 минут
   - Добавить логирование пропущенных задач

2. **Улучшить логирование**
   - Логировать каждую проверку расписания
   - Логировать причины пропуска дайджеста
   - Логировать выполнение задачи даже если нет подходящих пользователей

3. **Добавить метрики**
   - Метрика пропущенных задач scheduler
   - Метрика проверок расписания
   - Метрика причин пропуска дайджеста

### Долгосрочные улучшения

1. **Улучшение resilience**
   - Использовать `misfire_grace_time` для всех задач
   - Добавить retry механизм для пропущенных задач
   - Использовать persistent job store для сохранения состояния

2. **Улучшение observability**
   - Добавить health check для scheduler
   - Настроить алерты на пропущенные задачи
   - Добавить dashboard для мониторинга scheduler

---

## Checks

### Проверка scheduler

```bash
# 1. Проверка логов scheduler
docker compose logs api --since 24h | grep -E "(scheduler|missed|process_digests)"

# 2. Проверка выполнения задач
docker compose logs api --since 24h | grep -E "(Digest processing task completed|Error in digest processing)"

# 3. Проверка настроек пользователя (через API)
curl -s http://localhost:8000/api/digest/settings/{user_id}
```

### Проверка расписания

```python
# Проверка логики времени
from datetime import datetime, timezone, time
from pytz import timezone as pytz_timezone

current_utc = datetime.now(timezone.utc)
user_tz = pytz_timezone("Europe/Moscow")  # Пример
local_time = current_utc.astimezone(user_tz).time()
schedule_time = time(9, 0)  # 9:00

time_diff = abs(
    (local_time.hour * 60 + local_time.minute) -
    (schedule_time.hour * 60 + schedule_time.minute)
)

print(f"Local time: {local_time}")
print(f"Schedule time: {schedule_time}")
print(f"Time diff: {time_diff} minutes")
print(f"Should trigger: {time_diff <= 5}")
```

---

## Impact / Rollback

### Impact

**Что изменится**:
- ✅ Задачи scheduler не будут пропускаться
- ✅ Улучшится логирование для диагностики
- ✅ Добавятся метрики для мониторинга

**Что не затронуто**:
- ✅ Существующая логика проверки расписания
- ✅ Условия отправки дайджеста
- ✅ Обратная совместимость

### Rollback

**Если нужно откатить изменения**:
- Изменения не критичны, можно откатить через git
- Scheduler продолжит работать в текущем режиме

---

## Исправления

### 1. Добавить misfire_grace_time для задачи process_digests

**Файл**: `api/tasks/scheduler_tasks.py`

**Изменение**:
```python
# Было:
scheduler.add_job(
    process_digests_task,
    trigger=CronTrigger(minute="*/15"),
    id="process_digests",
    name="Process user digests",
    replace_existing=True
)

# Стало:
scheduler.add_job(
    process_digests_task,
    trigger=CronTrigger(minute="*/15"),
    id="process_digests",
    name="Process user digests",
    replace_existing=True,
    misfire_grace_time=300  # 5 минут - выполнить даже если пропущено
)
```

### 2. Улучшить логирование в process_digests_task

**Файл**: `api/tasks/scheduler_tasks.py`

**Изменение**: Добавить логирование в начале и конце задачи, а также для каждой проверки пользователя.

---

## Итоговый статус

| Компонент | Статус | Примечание |
|-----------|--------|-----------|
| Scheduler | ⚠️ | Задачи пропускаются (missed) |
| process_digests_task | ❌ | Не выполняется в 9:00 |
| Логирование | ⚠️ | Недостаточно для диагностики |
| Метрики | ❌ | Нет метрик пропусков |

**Общий статус**: ❌ **Дайджест не отправляется из-за пропуска задач scheduler**

---

## Заключение

✅ **Проблема идентифицирована**: APScheduler пропускает выполнение задач, что приводит к тому, что дайджест не отправляется в 9:00.

**Следующие шаги**:
1. ✅ Добавлен `misfire_grace_time` для задачи `process_digests`
2. ✅ Улучшено логирование для диагностики
3. ⏳ Добавить метрики для мониторинга пропусков (опционально)
4. ⏳ Проверить настройки пользователя (schedule_time, schedule_tz) после перезапуска

---

## Исправления применены

### ✅ 1. Добавлен misfire_grace_time для задачи process_digests

**Изменение**: Задача теперь выполнится даже если была пропущена (missed), в течение 5 минут после запланированного времени.

**Код**:
```python
scheduler.add_job(
    process_digests_task,
    trigger=CronTrigger(minute="*/15"),
    id="process_digests",
    name="Process user digests",
    replace_existing=True,
    misfire_grace_time=300  # 5 минут - выполнить даже если пропущено
)
```

**Файл**: `api/tasks/scheduler_tasks.py`

### ✅ 2. Улучшено логирование process_digests_task

**Изменения**:
- Логирование начала и завершения задачи
- Логирование количества проверенных пользователей
- Логирование проверки расписания для каждого пользователя
- Логирование совпадения расписания

**Код**:
```python
# В начале задачи
logger.info("process_digests_task started", timestamp=task_start_time.isoformat())
logger.info("process_digests_task: checking users", enabled_settings_count=len(digest_settings))

# При проверке расписания
logger.debug("Checking digest schedule", user_id=..., schedule_time=..., time_diff_minutes=...)
logger.info("Digest schedule matched", user_id=..., schedule_time=..., time_diff_minutes=...)

# В конце задачи
logger.info("Digest processing task completed", duration_seconds=..., settings_checked=...)
```

**Файл**: `api/tasks/scheduler_tasks.py`

---

## Проверка после исправлений

### После перезапуска API

```bash
# 1. Проверка логов scheduler
docker compose logs api --since 1h | grep -E "(process_digests_task|Digest processing|schedule matched)"

# 2. Проверка выполнения задач
docker compose logs api --since 1h | grep -E "(Digest processing task completed|Error in digest processing)"

# 3. Проверка отсутствия пропусков
docker compose logs api --since 1h | grep -E "(missed|misfire)"
```

### Ожидаемое поведение

1. ✅ Задача `process_digests` выполняется каждые 15 минут
2. ✅ Если задача пропущена, она выполнится в течение 5 минут
3. ✅ Логи показывают проверку расписания для каждого пользователя
4. ✅ Логи показывают совпадение расписания и генерацию дайджеста

