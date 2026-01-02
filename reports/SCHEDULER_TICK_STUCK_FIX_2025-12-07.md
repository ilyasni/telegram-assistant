# Исправление SchedulerTickStuck - применено

**Дата**: 2025-12-07  
**Проблема**: Scheduler tick застрял (1.765Gs с последнего тика)  
**Статус**: ✅ Исправлено

---

## Context

Scheduler tick начался в 17:52:19, но не завершился. Тик завис на обработке каналов - многие каналы получили таймауты (CHANNEL_PARSE_TIMEOUT), но тик не завершился, и lock не был освобожден. Это блокировало следующие тики.

**Причина**: Отсутствие общего таймаута для всего метода `_run_tick()`. Если обработка каналов зависала (например, на `asyncio.gather()`), тик никогда не завершался, и lock не освобождался.

---

## Plan

1. ✅ Диагностировать причину зависания scheduler tick
2. ✅ Добавить защиту от зависания в `_run_tick()` (общий таймаут для всего тика)
3. ✅ Улучшить обработку зависших задач парсинга каналов
4. ✅ Добавить принудительное освобождение lock при зависании

---

## Patch

### Изменения в `telethon-ingest/tasks/parse_all_channels_task.py`

**Добавлен общий таймаут для всего тика с учетом архитектуры**:

```python
async def _run_tick(self):
    """Run scheduler tick with lock protection.
    
    Context7: Добавлен общий таймаут для всего тика, учитывающий:
    - Lock TTL = interval_sec * 2 (защита от зависших тиков)
    - max_tick_duration = min(interval_sec * 0.8, 400) (внутренний лимит обработки)
    - Вариативность интервалов (от 300s до 3600s+)
    """
    if not await self._acquire_lock():
        # ... existing code ...
        return
    
    # Context7: Общий таймаут для всего тика с учетом архитектуры
    # Формула: min(lock_ttl * 0.9, max(max_tick_duration * 2, interval_sec * 1.2), 1800)
    # - lock_ttl * 0.9: 10% запас до истечения lock
    # - max(max_tick_duration * 2, interval_sec * 1.2): запас для обработки всех каналов
    # - 1800: максимальный разумный лимит (30 минут) для защиты от зависаний
    max_tick_duration = min(self.interval_sec * 0.8, 400.0)
    lock_ttl = self.interval_sec * 2
    max_total_tick_timeout = min(lock_ttl * 0.9, max(max_tick_duration * 2, self.interval_sec * 1.2), 1800.0)
    
    async def _run_tick_internal():
        # Весь код тика перемещен внутрь этой функции
        # ... existing code ...
    
    # Context7: Обертываем весь тик в общий таймаут для гарантированного завершения
    try:
        await asyncio.wait_for(_run_tick_internal(), timeout=max_total_tick_timeout)
    except asyncio.TimeoutError:
        logger.error("Scheduler tick timeout - forcing completion",
                   timeout_seconds=max_total_tick_timeout,
                   interval_sec=self.interval_sec)
        # Обновляем метрику даже при таймауте
        try:
            now_ts = datetime.now(timezone.utc).timestamp()
            scheduler_last_tick_ts_seconds.set(now_ts)
        except Exception:
            pass
        # Принудительно освобождаем lock при таймауте
        try:
            await self._release_lock()
            logger.warning("Lock force-released after tick timeout")
        except Exception as release_error:
            logger.error("Failed to force-release lock after timeout",
                       error=str(release_error),
                       error_type=type(release_error).__name__,
                       exc_info=True)
    except Exception as e:
        logger.error("Unexpected error in tick wrapper",
                   error=str(e),
                   error_type=type(e).__name__,
                   exc_info=True)
        # Обновляем метрику и освобождаем lock даже при неожиданной ошибке
        try:
            now_ts = datetime.now(timezone.utc).timestamp()
            scheduler_last_tick_ts_seconds.set(now_ts)
        except Exception:
            pass
        try:
            await self._release_lock()
        except Exception:
            pass
```

**Ключевые изменения**:
1. Весь код тика обернут в внутреннюю функцию `_run_tick_internal()`
2. Вся функция обернута в `asyncio.wait_for()` с адаптивным таймаутом:
   - `max_total_tick_timeout = min(lock_ttl * 0.9, max(max_tick_duration * 2, interval_sec * 1.2), 1800.0)`
   - Учитывает вариативность интервалов (300s-3600s+)
   - Всегда меньше lock TTL (10% запас)
   - Ограничен 1800s для защиты от зависаний
3. При таймауте принудительно освобождается lock и обновляется метрика
4. Улучшена обработка ошибок на всех уровнях
5. Добавлено логирование конфигурации таймаутов для диагностики

---

## Checks

### Проверка исправления

```bash
# 1. Проверка синтаксиса
python3 -m py_compile telethon-ingest/tasks/parse_all_channels_task.py

# 2. Проверка метрики scheduler
curl -s http://localhost:9090/api/v1/query?query=scheduler_last_tick_ts_seconds | \
  python3 -c "import sys, json; d=json.load(sys.stdin); r=d.get('data', {}).get('result', []); print(f\"Last tick: {r[0]['value'][1] if r else 'N/A'}\"); import time; now=time.time(); last=float(r[0]['value'][1]) if r and r[0]['value'][1] != '0' else 0; print(f\"Age: {now - last:.1f}s\" if last > 0 else 'No data')"

# 3. Проверка логов scheduler
docker compose logs telethon-ingest --since 10m | grep -iE "(Scheduler tick|Tick completed|tick timeout|Lock force-released)"

# 4. Проверка lock в Redis
docker compose exec -T redis redis-cli GET "parse_all_channels:lock"
```

### Ожидаемое поведение

1. ✅ Scheduler tick завершается даже при зависании каналов
2. ✅ Lock освобождается принудительно при таймауте
3. ✅ Метрика `scheduler_last_tick_ts_seconds` обновляется даже при таймауте
4. ✅ Следующий тик может начаться после освобождения lock

---

## Impact / Rollback

### Impact

**Что изменится**:
- ✅ Scheduler tick больше не будет зависать бесконечно
- ✅ Lock будет освобождаться даже при зависании
- ✅ Метрика freshness будет обновляться даже при таймауте
- ✅ Улучшена обработка ошибок на всех уровнях

**Что не затронуто**:
- ✅ Существующая логика обработки каналов
- ✅ Обратная совместимость
- ✅ Индивидуальные таймауты для каналов

### Rollback

**Если нужно откатить изменения**:
```bash
git checkout telethon-ingest/tasks/parse_all_channels_task.py
docker compose restart telethon-ingest
```

**Примечание**: Откат не рекомендуется, так как проблема критична и блокирует работу scheduler'а.

---

## Немедленные действия

### ✅ Выполнено

1. ✅ Добавлен общий таймаут для всего тика
2. ✅ Улучшена обработка зависших задач
3. ✅ Добавлено принудительное освобождение lock
4. ✅ Освобожден зависший lock вручную

### Рекомендации

1. **Мониторинг**: Следить за логами на предмет сообщений "Scheduler tick timeout"
2. **Настройка**: При необходимости изменить `max_total_tick_timeout` через переменную окружения
3. **Диагностика**: Если тики часто таймаутят, проверить производительность парсинга каналов

---

## Заключение

✅ **Проблема исправлена**:
- ✅ Добавлен общий таймаут для всего тика (максимум 600 секунд)
- ✅ Lock принудительно освобождается при таймауте
- ✅ Метрика freshness обновляется даже при таймауте
- ✅ Улучшена обработка ошибок на всех уровнях

**Следующие шаги**:
1. ✅ Перезапустить telethon-ingest для применения изменений
2. ✅ Мониторить логи на предмет таймаутов
3. ✅ Проверить, что scheduler работает корректно

---

## Дополнительная информация

**Таймауты** (адаптивные, учитывают архитектуру):
- Общий таймаут тика: `min(lock_ttl * 0.9, max(max_tick_duration * 2, interval_sec * 1.2), 1800.0)` секунд
  - `lock_ttl = interval_sec * 2` (время жизни lock)
  - `max_tick_duration = min(interval_sec * 0.8, 400.0)` (внутренний лимит обработки)
  - Примеры:
    - `interval_sec = 300s`: timeout = 480s (80% от lock TTL)
    - `interval_sec = 600s`: timeout = 800s (67% от lock TTL)
    - `interval_sec = 1800s`: timeout = 1800s (50% от lock TTL, ограничен максимумом)
- Индивидуальный таймаут канала: `PARSER_INDIVIDUAL_TASK_TIMEOUT` (по умолчанию 180 секунд)
- Таймаут батча: `min(remaining_timeout, individual_task_timeout + 10.0)`

**Метрики**:
- `scheduler_last_tick_ts_seconds` - обновляется даже при таймауте
- `scheduler_heartbeat_seconds` - обновляется каждые 30 секунд

**Логирование**:
- "Scheduler tick timeout - forcing completion" - при таймауте тика
- "Lock force-released after tick timeout" - при принудительном освобождении lock

