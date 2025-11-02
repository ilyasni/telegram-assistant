# ✅ Чеклист развертывания адаптивных порогов

## Выполнено

1. ✅ Код реализован и протестирован
2. ✅ Добавлены переменные окружения в docker-compose.yml
3. ✅ Обновлен env.example
4. ✅ Пересобран контейнер telethon-ingest
5. ✅ Перезапущен сервис
6. ✅ Проверен импорт всех новых функций

## Статус конфигурации

- **FEATURE_ADAPTIVE_THRESHOLDS_ENABLED**: `true` (по умолчанию в docker-compose.yml)
- **PARSER_STATS_WINDOW_DAYS**: `14` (по умолчанию)
- **Адаптивные пороги**: Включены по умолчанию

## Следующие шаги для мониторинга

### 1. Проверить работу в реальном времени

```bash
# Мониторить логи на наличие расчета статистики
docker compose logs -f telethon-ingest | grep -E "interarrival|adaptive threshold|quiet hours"

# Проверить метрики Prometheus
curl http://localhost:9090/metrics | grep "channel_gap_seconds\|adaptive_threshold_seconds"
```

### 2. Проверить работу в выходные/ночи

- Система должна использовать увеличенные пороги в выходные (коэффициент 1.8x)
- В ночное время (22:00-08:00 MSK) коэффициент 1.5x
- Логи должны показывать контекст `quiet_reason` при превышении порогов

### 3. Мониторинг метрик

**Ключевые метрики:**
- `channel_gap_seconds{channel_id}` - текущий gap
- `adaptive_threshold_seconds{channel_id}` - текущий порог
- `channel_last_post_timestamp_seconds{channel_id}` - время последнего поста
- `interarrival_seconds{channel_id}` - histogram интервалов

**Алерты:**
```promql
# Gap превышает адаптивный порог (но не в quiet hours)
channel_gap_seconds > adaptive_threshold_seconds AND hour() < 22 AND hour() >= 8 AND weekday() < 5
```

### 4. Проверка Redis кеша

```bash
# Проверить наличие кеша статистики
docker compose exec redis redis-cli KEYS "interarrival_stats:*"

# Проверить low watermarks
docker compose exec redis redis-cli KEYS "low_watermark:*"

# Проверить backfill locks
docker compose exec redis redis-cli KEYS "lock:backfill:*"
```

## Откат

Если потребуется отключить адаптивные пороги:

```bash
# Установить в .env или через переменную окружения
FEATURE_ADAPTIVE_THRESHOLDS_ENABLED=false

# Перезапустить
docker compose restart telethon-ingest
```

Система вернется к фиксированным порогам (1 час) с базовым overlap (5 минут).

