# Исправление проблемы CrawlTriggerQueueDepthHigh

**Дата**: 2025-12-03  
**Проблема**: Сообщения читаются, но не обрабатываются (глубина очереди: 9976)

---

## 🔍 Проблема

### Симптомы

- **Глубина очереди**: 9976 сообщений (критически высокая)
- **Скорость обработки**: 0 triggers/sec
- **xreadgroup возвращает пустые результаты**: `len=0 messages=[]`
- **Все сообщения уже прочитаны**: `entries-read: 9976`, `lag: 0`
- **Consumer неактивен**: `idle: 1081 секунд`

### Причина

Consumer group читает только новые сообщения (`">"`), но все существующие сообщения уже прочитаны. Сообщения были прочитаны, но не обработаны (нет логов о обработке).

---

## ✅ Решение

### Добавлена обработка pending сообщений

Добавлен метод `_process_pending_messages()` для обработки сообщений, которые были прочитаны, но не обработаны:

```python
async def _process_pending_messages(self) -> int:
    """
    Context7: Обработка pending сообщений через XAUTOCLAIM.
    Возвращает количество обработанных сообщений.
    """
    # Используем xautoclaim для получения pending сообщений
    # min_idle_time: 5000ms - сообщения, которые не обрабатывались 5+ секунд
    result = await self.redis.xautoclaim(
        name=self.stream_in,
        groupname=self.consumer_group,
        consumername=self.consumer_name,
        min_idle_time=5000,
        start_id="0-0",
        count=50,
        justid=False
    )
    # ... обработка сообщений
```

### Периодическая проверка pending

Добавлена периодическая проверка pending сообщений каждые 60 секунд:

```python
pending_check_interval = 60  # Проверять pending каждые 60 секунд
last_pending_check = time.time()

# В цикле:
if current_time - last_pending_check >= pending_check_interval:
    try:
        await self._process_pending_messages()
        last_pending_check = current_time
    except Exception as e:
        logger.error("Error processing pending messages", error=str(e))
```

---

## 📊 Ожидаемый результат

После применения исправления:

1. **Pending сообщения будут обрабатываться** каждые 60 секунд
2. **Глубина очереди начнет уменьшаться** по мере обработки
3. **Метрика `crawl_trigger_queue_depth_current`** будет уменьшаться
4. **Скорость обработки** (`rate(crawl_triggers_total[5m])`) увеличится

---

## 🔍 Мониторинг

### Проверка работы

```bash
# Проверка глубины очереди
curl http://localhost:9090/api/v1/query?query=crawl_trigger_queue_depth_current

# Проверка скорости обработки
curl http://localhost:9090/api/v1/query?query=rate\(crawl_triggers_total\[5m\]\)

# Проверка pending сообщений
docker exec telegram-assistant-redis-1 redis-cli XPENDING stream:posts:tagged crawl_trigger_workers

# Проверка логов
docker logs telegram-assistant-worker-1 --tail 100 | grep -i "pending\|Processed pending"
```

### Метрики для отслеживания

- `crawl_trigger_queue_depth_current` - должна уменьшаться
- `rate(crawl_triggers_total[5m])` - должна увеличиться
- `histogram_quantile(0.95, rate(crawl_trigger_processing_latency_seconds_bucket[5m]))` - latency обработки

---

## ⚠️ Важные замечания

1. **Обработка может занять время**: 9976 сообщений будут обрабатываться постепенно
2. **Pending сообщения обрабатываются каждые 60 секунд**: не мгновенно
3. **Если сообщения не обрабатываются**: проверьте логи на наличие ошибок

---

## 📝 Следующие шаги

1. ✅ Исправление применено
2. ⏳ Перезапуск worker для применения изменений
3. ⏳ Мониторинг глубины очереди
4. ⏳ Проверка скорости обработки

---

## ✅ Статус

**Исправление**: Применено  
**Статус**: Ожидание перезапуска worker и проверки результата

