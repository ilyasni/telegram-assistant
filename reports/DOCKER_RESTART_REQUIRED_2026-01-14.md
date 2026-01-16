# Нужна ли пересборка контейнеров после исправлений?

**Дата**: 2026-01-14T12:25:00+03:00  
**Вопрос**: Нужно ли пересобирать контейнеры после исправлений метрик?

---

## Context

После исправления метрик Prometheus в `api/worker/tasks/vision_analysis_task.py` нужно определить, требуется ли пересборка контейнеров или достаточно перезапуска.

---

## Анализ конфигурации Docker

### 1. API сервис

**Volumes** (docker-compose.yml, строки 494-502):
```yaml
volumes:
  - ./api:/app:ro  # Context7: монтируем код для разработки (read-only для безопасности)
```

**Вывод**: ✅ Код монтируется как volume, изменения видны сразу после перезапуска.

### 2. Worker сервис

**Volumes** (docker-compose.yml, строки 619-637):
```yaml
volumes:
  - ./api:/opt/telegram-assistant/api:ro
  - ./worker/tasks:/app/tasks:ro
  - ./shared:/app/shared:ro
```

**Вывод**: ✅ Код монтируется как volume, изменения видны сразу после перезапуска.

### 3. Telethon-ingest сервис

**Volumes** (docker-compose.yml, строки 710-722):
```yaml
volumes:
  - ./telethon-ingest:/app:ro
  - ./api:/opt/telegram-assistant/api:ro
```

**Вывод**: ✅ Код монтируется как volume, изменения видны сразу после перезапуска.

---

## Ответ: Пересборка НЕ нужна

**Достаточно перезапуска контейнеров**:

```bash
# Перезапуск всех сервисов
docker compose restart api worker

# Или перезапуск конкретного сервиса
docker compose restart worker
```

---

## Когда нужна пересборка?

Пересборка нужна только если:

1. **Изменения в Dockerfile** - добавлены новые системные зависимости, изменены переменные окружения на этапе сборки
2. **Изменения в requirements.txt** - добавлены новые Python пакеты
3. **Изменения в shared пакете для telethon-ingest** - shared монтируется только в worker, но копируется в telethon-ingest при сборке (см. комментарий в docker-compose.yml, строка 716-718)

---

## Проверка после перезапуска

После перезапуска проверьте:

```bash
# Проверка логов на ошибки
docker compose logs worker --tail=50 | grep -i error

# Проверка работы Vision анализа
docker compose logs worker | grep -i "vision.*metric\|vision.*error"

# Проверка метрик Prometheus
curl -s http://localhost:8001/metrics | grep vision_analysis_duration_seconds
```

---

## Рекомендации

1. ✅ **Перезапустите контейнеры**: `docker compose restart worker`
2. ✅ **Проверьте логи**: Убедитесь, что нет ошибок импорта или инициализации
3. ✅ **Проверьте метрики**: Убедитесь, что метрики записываются без ошибок
4. ⚠️ **Если есть ошибки**: Проверьте, что файлы действительно изменились в контейнере

---

**Context7 Best Practices**: Использование volume mounts для кода позволяет быстро применять изменения без пересборки образов, что ускоряет разработку и отладку.
