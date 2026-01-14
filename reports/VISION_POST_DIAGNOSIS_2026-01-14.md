# Диагностика проблемного поста без Vision анализа

**Дата**: 2026-01-14T12:30:00+03:00  
**Post ID**: `9c96c8be-5128-465b-8c2c-8c86de943fcd`

---

## Context

Проверка причин, почему один пост с медиа не получил vision анализ, несмотря на то, что:
- ✅ Событие `posts.vision.uploaded` было отправлено в `stream:posts:vision`
- ✅ Медиа файл загружен в S3
- ✅ Медиа файл существует в `media_objects`

---

## Результаты диагностики

### 1. Идемпотентность

**Проверка**: Ключи идемпотентности в Redis
- **Ключ**: `vision:processed:9c96c8be-5128-465b-8c2c-8c86de943fcd:8b6f11660e490c68d60557b2aacc7c040fab96a67b37266d516cec31f86ac353`
- **Результат**: ❌ Ключ не существует
- **Вывод**: Медиа файл НЕ был обработан ранее (не идемпотентность)

### 2. Обработка события

**Проверка**: Статус обработки события в consumer group
- **Event Message ID**: `1768374398835-0`
- **Last Delivered ID**: `1768377845879-0`
- **Результат**: ✅ Событие было прочитано (event <= last_delivered)
- **Pending**: 0 (событие было ACK'нуто)

**Проверка**: Событие в `stream:posts:vision:analyzed`
- **Message ID**: `1768374429609-0`
- **Статус**: Событие найдено в analyzed stream
- **Вывод**: Событие было обработано и эмитировано в analyzed stream

### 3. Метрики Prometheus

- **Failed events**: 0
- **Skipped events**: 0 (но метрика `vision_events_total{status="skipped", reason="all_media_skipped"}` показывает 1)
- **Processed events**: 4

### 4. Vision Enrichment в БД

**Проверка**: `post_enrichment` для поста
- **Результат**: ❌ Vision enrichment не найден
- **Вывод**: Результаты vision анализа не были сохранены в БД

---

## Выводы

### ✅ Идемпотентность - НЕ причина

Ключи идемпотентности не найдены, значит медиа файл не был обработан ранее. Идемпотентность не является причиной пропуска.

### ❌ Ошибка при обработке - НАЙДЕНА ПРИЧИНА

**Причина**: Ошибка в метриках Prometheus при обработке медиа файла

**Детали события `posts.vision.skipped`**:
```json
{
  "event_type": "posts.vision.skipped",
  "post_id": "9c96c8be-5128-465b-8c2c-8c86de943fcd",
  "reasons": [
    {
      "media_id": "8b6f11660e490c68d60557b2aacc7c040fab96a67b37266d516cec31f86ac353",
      "reason": "exception",
      "details": {
        "error": "histogram metric is missing label values"
      }
    }
  ]
}
```

**Что произошло**:
1. ✅ Событие было прочитано consumer group
2. ✅ Начата обработка медиа файла
3. ❌ При записи метрики Prometheus возникла ошибка: `histogram metric is missing label values`
4. ❌ Ошибка привела к пропуску медиа файла (exception handling)
5. ✅ Событие было эмитировано как `posts.vision.skipped`
6. ✅ Событие было ACK'нуто

**Проблема**: Histogram метрика в VisionAnalysisTask не получает все необходимые label values, что приводит к исключению и пропуску медиа файла.

**Детали ошибки**:
- Ошибка: `histogram metric is missing label values`
- Место: При записи метрики `vision_media_duration_seconds` или `vision_analysis_duration_seconds`
- Время: 2026-01-14T07:07:09.608834+00:00

**Анализ кода**:
- Метрики `vision_media_duration_seconds` и `vision_analysis_duration_seconds` определены БЕЗ labels (строки 131-136, 182-187)
- Используются без `.labels()` (строка 976-977): `vision_media_duration_seconds.observe(media_duration)`
- Возможная причина: Где-то в коде есть попытка использовать `.labels()` перед `.observe()`, или метрика была переопределена с labels в другом месте

**Требуется**: Проверить, нет ли использования `.labels()` для этих метрик, или переопределения метрик с labels.

---

## Рекомендации

### 1. Проверить детали skipped события

Нужно получить полные данные события из `stream:posts:vision:analyzed` для Message ID `1768374429609-0`, чтобы понять:
- Почему медиа файлы были пропущены
- Какие `skipped_reasons` были указаны
- Был ли это `all_media_skipped` или другая причина

### 2. Проверить логи VisionAnalysisTask

Проверить логи API контейнера за время обработки события (около 07:06:38 UTC) для:
- Логов обработки этого поста
- Ошибок при сохранении в БД
- Причин пропуска медиа файлов

### 3. Проверить Policy Engine и Budget Gate

Проверить, не были ли медиа файлы пропущены из-за:
- Policy Engine (неподдерживаемый MIME тип, размер файла, и т.д.)
- Budget Gate (исчерпана квота)
- Других фильтров

---

## Checks

Для дальнейшей диагностики:

```bash
# Получить детали skipped события
docker compose exec api python3 -c "
import asyncio
import redis.asyncio as redis
import json

async def check():
    r = redis.from_url('redis://redis:6379/0', decode_responses=True)
    msg_id = '1768374429609-0'
    fields = await r.xrange('stream:posts:vision:analyzed', msg_id, msg_id)
    if fields:
        for stream_id, data in fields:
            if 'data' in data:
                parsed = json.loads(data['data'])
                print(json.dumps(parsed, indent=2, ensure_ascii=False))
    await r.aclose()

asyncio.run(check())
"

# Проверить логи за время обработки
docker compose logs --since 6h api | grep -iE "(9c96c8be-5128-465b-8c2c-8c86de943fcd|1768374398835-0|1768374429609-0)"
```

---

**Context7 Best Practices**: Диагностика проведена с использованием Context7 best practices для observability и traceability.
