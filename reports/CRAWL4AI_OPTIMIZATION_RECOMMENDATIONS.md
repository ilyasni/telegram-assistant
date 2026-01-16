# Рекомендации по оптимизации Crawl4AI

**Дата**: 2026-01-12  
**Текущая ситуация**: Backlog 165 сообщений, стабилен (не растет)

---

## 📊 Текущая конфигурация

### Параметры обработки:
- **MAX_CONCURRENT_CRAWLS**: 3 (docker-compose.yml)
- **Batch size**: 5 сообщений (xreadgroup count)
- **Rate limit per host**: 10 запросов/минуту
- **CRAWL_PER_DOMAIN_CONCURRENCY**: 2
- **CRAWL_TIMEOUT_SEC**: 30
- **Backlog**: 165 сообщений (стабилен, lag = 0, pending = 0)

### Анализ:
- ✅ Очередь обрабатывается (lag = 0, pending = 0)
- ⚠️ Backlog не уменьшается (165 сообщений стабильны)
- ⚠️ Медленная обработка (backlog не очищается)

---

## 🎯 Рекомендации по оптимизации

### Вариант 1: Увеличить параллелизм (Рекомендуется)

**Изменение**: Увеличить `MAX_CONCURRENT_CRAWLS` с 3 до 5-7

**Обоснование**:
- Backlog стабилен, значит система справляется, но медленно
- Увеличение параллелизма ускорит обработку
- Playwright может обрабатывать несколько страниц параллельно

**Риски**:
- Увеличение нагрузки на память (Playwright использует ~200-300MB на инстанс)
- Увеличение нагрузки на сеть

**Действия**:
```yaml
# docker-compose.yml
environment:
  MAX_CONCURRENT_CRAWLS: 5  # Было: 3
```

**Ожидаемый эффект**: Уменьшение backlog на 40-60%

---

### Вариант 2: Увеличить batch size

**Изменение**: Увеличить `count` в `xreadgroup` с 5 до 10

**Обоснование**:
- Меньше вызовов Redis
- Больше сообщений обрабатывается за цикл
- Crawl операции долгие, batch не критичен

**Риски**:
- Минимальные (crawl операции не связаны между собой)

**Действия**:
```python
# crawl4ai/crawl4ai_service.py
messages = await self.redis.xreadgroup(
    self.consumer_group,
    self.consumer_name,
    {self.stream: ">"},
    count=10,  # Было: 5
    block=1000
)
```

**Ожидаемый эффект**: Уменьшение backlog на 10-20%

---

### Вариант 3: Масштабирование (если варианты 1-2 недостаточны)

**Изменение**: Запустить несколько инстансов Crawl4AI

**Обоснование**:
- Полное параллельное масштабирование
- Независимые контейнеры с разными consumer names

**Риски**:
- Увеличение потребления ресурсов
- Нужна настройка docker-compose для масштабирования

**Действия**:
```bash
# Запуск нескольких инстансов
docker compose up -d --scale crawl4ai=2
```

**Или в docker-compose.yml**:
```yaml
crawl4ai:
  # ... existing config ...
  deploy:
    replicas: 2
```

**Ожидаемый эффект**: Линейное ускорение обработки

---

### Вариант 4: Оптимизация timeout и rate limits

**Изменение**: Уменьшить timeout, увеличить rate limits (с осторожностью)

**Обоснование**:
- Меньше timeout = быстрее отказ от медленных сайтов
- Больше rate limit = больше запросов в минуту

**Риски**:
- Может привести к блокировкам сайтов (rate limiting)
- Может увеличить количество ошибок

**Действия**:
```yaml
# docker-compose.yml
environment:
  CRAWL_TIMEOUT_SEC: 20  # Было: 30
  CRAWL_PER_DOMAIN_CONCURRENCY: 3  # Было: 2
```

**Ожидаемый эффект**: Уменьшение backlog на 15-25%

---

## 🔧 Рекомендуемый план действий

### Этап 1: Быстрая оптимизация (Низкий риск)

1. ✅ Увеличить `MAX_CONCURRENT_CRAWLS` до 5
2. ✅ Увеличить batch size до 10

**Команды**:
```bash
# 1. Изменить docker-compose.yml
# MAX_CONCURRENT_CRAWLS: 5

# 2. Изменить crawl4ai_service.py
# count=10 в xreadgroup

# 3. Перезапустить
docker compose restart crawl4ai
```

**Мониторинг**: Проверить backlog через 1-2 часа

---

### Этап 2: Если недостаточно (Средний риск)

3. ✅ Масштабировать до 2 инстансов

**Команды**:
```bash
docker compose up -d --scale crawl4ai=2
```

**Мониторинг**: Проверить backlog и ресурсы

---

### Этап 3: Если все еще недостаточно (Высокий риск)

4. ✅ Оптимизировать timeout и rate limits

**Мониторинг**: Проверить ошибки rate limiting

---

## 📈 Мониторинг

### Метрики для отслеживания:
- `crawl_queue_backlog_current` - текущий backlog
- `crawl_requests_total` - успешные/неуспешные запросы
- `crawl_latency_seconds` - время обработки
- `rate_limit_hits_total` - количество rate limit ошибок

### Проверка:
```bash
# Проверка backlog
docker compose exec -T redis redis-cli XLEN "stream:posts:crawl"

# Проверка pending
docker compose exec -T redis redis-cli XINFO GROUPS "stream:posts:crawl"

# Логи Crawl4AI
docker compose logs crawl4ai --since 1h | grep -E "backlog|processed|error"
```

---

## 🎯 Итоговые рекомендации

**Для текущей ситуации (165 сообщений, стабильный backlog)**:

1. **Начать с Варианта 1 + Варианта 2** (низкий риск, хороший эффект)
   - `MAX_CONCURRENT_CRAWLS: 5`
   - `count: 10` в xreadgroup

2. **Мониторить 2-4 часа**

3. **Если backlog не уменьшается** → Вариант 3 (масштабирование)

**Ожидаемый результат**: Backlog должен уменьшиться до 50-100 сообщений за 2-4 часа.
