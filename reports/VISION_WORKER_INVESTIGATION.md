# Исследование алерта VisionWorkerNotProcessing

**Дата**: 2025-12-03  
**Проблема**: Vision worker не обрабатывает события

---

## 📊 Текущее состояние

### Алерт

- **Название**: VisionWorkerNotProcessing
- **Условие**: `rate(vision_worker_processed_total[5m]) == 0` for 10m
- **Severity**: critical
- **Статус**: Активен (с 2025-12-03T18:45:37)

### Состояние очереди

- **Сообщений в stream:posts:vision**: 6981
- **Обработано (entries-read)**: 6977
- **Lag**: 4 сообщения
- **Pending сообщений**: 1
- **Consumer'ов**: 40

### Метрики

- **vision_worker_processed_total**: ❌ Метрика не найдена в Prometheus (не экспортируется)
- **Скорость обработки**: 0 событий/сек (метрика отсутствует)

---

## 🔍 Анализ проблемы

### Проблема 1: Метрика не экспортируется

**Наблюдения**:
- Метрика `vision_worker_processed_total` определена в коде через `_safe_create_metric()`
- `_safe_create_metric()` может вернуть `MockMetric` при ошибке импорта prometheus_client
- Метрика не появляется в Prometheus

**Возможные причины**:
1. Ошибка при создании метрики (возвращен MockMetric)
2. Метрика не регистрируется в Prometheus registry
3. Worker не экспортирует метрики

### Проблема 2: Vision worker может не запускаться

**Наблюдения**:
- VisionAnalysisTask зарегистрирован в supervisor
- Но может пропускаться при отсутствии credentials (GIGACHAT_CLIENT_ID, GIGACHAT_CLIENT_SECRET)
- В логах нет записей о запуске VisionAnalysisTask

**Код**:
```python
except ValueError as e:
    logger.warning(f"VisionAnalysisTask skipped: {e}")
    logger.warning("Для включения Vision Analysis установите GIGACHAT_CLIENT_ID и GIGACHAT_CLIENT_SECRET")
```

---

## 🔧 Решения

### Решение 1: Проверить запуск Vision worker

1. Проверить наличие credentials:
   ```bash
   docker exec telegram-assistant-worker-1 printenv | grep GIGACHAT
   ```

2. Проверить логи на пропуск задачи:
   ```bash
   docker logs telegram-assistant-worker-1 | grep -i "vision.*skipped\|vision.*credentials"
   ```

3. Если credentials отсутствуют:
   - Установить `GIGACHAT_CLIENT_ID` и `GIGACHAT_CLIENT_SECRET`
   - Перезапустить worker

### Решение 2: Исправить экспорт метрик

1. Проверить, что метрика не является MockMetric:
   ```python
   from worker.tasks.vision_analysis_task import vision_worker_processed_total
   print(type(vision_worker_processed_total))  # Должно быть Counter, не MockMetric
   ```

2. Если MockMetric - проверить импорт prometheus_client:
   ```python
   from prometheus_client import Counter
   # Должен импортироваться без ошибок
   ```

3. Убедиться, что метрика регистрируется в Prometheus registry

### Решение 3: Проверить обработку pending сообщений

1. Проверить pending сообщения:
   ```bash
   docker exec telegram-assistant-redis-1 redis-cli XPENDING stream:posts:vision vision_workers
   ```

2. Если есть pending - обработать их через XAUTOCLAIM (как в crawl_trigger_task)

---

## 📝 Рекомендации

1. **Немедленные действия**:
   - Проверить наличие GIGACHAT credentials
   - Проверить логи на пропуск VisionAnalysisTask
   - Проверить, что метрика не является MockMetric

2. **Долгосрочные решения**:
   - Убедиться, что Vision worker запускается
   - Исправить экспорт метрик (если проблема)
   - Добавить обработку pending сообщений (если нужно)

3. **Мониторинг**:
   - Отслеживать метрику `vision_worker_processed_total`
   - Отслеживать lag в очереди `stream:posts:vision`
   - Отслеживать количество pending сообщений

---

## ✅ Следующие шаги

1. Проверить наличие GIGACHAT credentials
2. Проверить логи на пропуск VisionAnalysisTask
3. Проверить, что метрика не является MockMetric
4. Если credentials отсутствуют - установить их
5. Если метрика MockMetric - исправить импорт prometheus_client

