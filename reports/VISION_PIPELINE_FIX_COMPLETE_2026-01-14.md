# Исправление Vision пайплайна

**Дата**: 2026-01-14T07:03:13 UTC  
**Статус**: ✅ Критическая ошибка исправлена

---

## Context

Обнаружена критическая проблема: MediaProcessor не инициализировался из-за синтаксической ошибки в `media_processor.py`, что приводило к тому, что:
1. Посты с медиа не обрабатывались (has_media=true, но media_count=0)
2. События posts.vision.uploaded не публиковались
3. Vision анализ не выполнялся для новых постов

---

## Проблема

### Синтаксическая ошибка в media_processor.py

**Ошибка**: `SyntaxError: expected 'except' or 'finally' block (media_processor.py, line 860)`

**Причина**: В методе `_upload_to_s3` был блок `try`, но отсутствовал блок `except` или `finally` для обработки исключений на верхнем уровне.

**Последствия**:
- MediaProcessor не мог быть импортирован
- `has_media_processor = false` во всех экземплярах ChannelParser
- Медиа файлы не обрабатывались при парсинге
- События posts.vision.uploaded не публиковались
- Vision анализ не выполнялся

---

## Исправления

### 1. Исправлена синтаксическая ошибка в media_processor.py

**Файл**: `telethon-ingest/services/media_processor.py`

**Изменение**: Добавлен блок `except Exception` для метода `_upload_to_s3`:

```python
        except Exception as e:
            # Context7: Обработка неожиданных ошибок при проверке квоты или других операциях
            logger.error(
                "Unexpected error in _upload_to_s3",
                error=str(e),
                error_type=type(e).__name__,
                mime_type=mime_type,
                size_bytes=len(content),
                trace_id=trace_id,
                exc_info=True
            )
            media_processing_failed_total.labels(reason="unexpected_error").inc()
            return None
```

**Результат**: ✅ MediaProcessor теперь инициализируется успешно

### 2. Улучшена обработка has_media в channel_parser.py

**Файл**: `telethon-ingest/services/channel_parser.py`

**Изменения**:
- ✅ `has_media` устанавливается в `True` только если медиа успешно обработаны
- ✅ `has_media` устанавливается в `False`, если медиа есть, но не обработано
- ✅ Добавлено предупреждение при наличии медиа, но отсутствии обработки

**Результат**: ✅ Более точное отражение состояния медиа в БД

### 3. Добавлена обработка pending сообщений в AlbumAssemblerTask

**Файл**: `api/worker/tasks/album_assembler_task.py`

**Изменения**:
- ✅ Добавлен метод `_process_pending_messages()` для обработки через XAUTOCLAIM
- ✅ Добавлен метод `_process_pending_periodically()` для периодической обработки (каждые 60 секунд)
- ✅ Обработка pending для обоих стримов: `stream:posts:vision:analyzed` и `stream:albums:parsed`

**Результат**: ✅ Автоматическая обработка зависших сообщений

### 4. Создан скрипт диагностики Vision пайплайна

**Файл**: `scripts/diagnose_vision_pipeline.py`

**Назначение**: Комплексная диагностика Vision пайплайна для постов с медиа

---

## Результаты проверки

### ✅ Исправлено

1. **Синтаксическая ошибка**: Исправлена, MediaProcessor инициализируется
2. **Pending сообщения**: Обработаны (0 pending)
3. **API Scheduler**: Работает (8 задач)
4. **FloodWait**: Нет активных

### ⚠️ Требуется действие

1. **Существующие посты с медиа**: 6 постов с `has_media=true`, но без медиа в `post_media_map`
   - **Причина**: Посты были созданы до исправления синтаксической ошибки
   - **Решение**: Перепарсить эти посты через `manual_parse_channel` для обработки медиа

2. **Vision анализ**: Нет анализов за последние 24 часа
   - **Причина**: Новые посты с медиа не обрабатывались из-за отсутствия MediaProcessor
   - **Решение**: После исправления новые посты будут обрабатываться автоматически

---

## Checks

### Проверка MediaProcessor

```bash
# Проверка инициализации MediaProcessor
docker compose logs telethon-ingest | grep -E "MediaProcessor initialized|has_media_processor"

# Должно быть:
# MediaProcessor initialized for ChannelParser
# has_media_processor: true
```

### Проверка обработки медиа

```bash
# Проверка постов с медиа
docker compose run --rm -v /opt/telegram-assistant:/opt/telegram-assistant api \
  python3 /opt/telegram-assistant/scripts/diagnose_vision_pipeline.py

# Проверка событий в stream:posts:vision
docker compose exec -T redis redis-cli XREVRANGE stream:posts:vision + - COUNT 5
```

### Перепарсинг существующих постов

Для обработки медиа в существующих постах:

```bash
# Перепарсить канал autopotoknews
docker compose exec -T telethon-ingest python3 -m telethon_ingest.scripts.manual_parse_channel autopotoknews incremental
```

---

## Impact / Rollback

### Безопасность изменений

Все изменения безопасны:
- ✅ Исправление синтаксической ошибки - только добавление обработки исключений
- ✅ Улучшение логики has_media - более точное отражение состояния
- ✅ Обработка pending сообщений - только чтение и ACK

### Rollback

Если потребуется откат:
1. Удалить блок `except Exception` из `_upload_to_s3` (но это вернет синтаксическую ошибку)
2. Вернуть старую логику `has_media` (но это менее точно)

**Рекомендация**: Не откатывать изменения, так как они исправляют критическую ошибку.

---

## Следующие шаги

1. ✅ **Исправлено**: Синтаксическая ошибка исправлена, MediaProcessor инициализируется
2. ⏳ **Требуется**: Перепарсить существующие посты с медиа для обработки медиа
3. ⏳ **Мониторинг**: Следить за новыми постами - медиа должны обрабатываться автоматически

---

## Заключение

✅ **Критическая ошибка исправлена по Context7 best practices**

MediaProcessor теперь инициализируется и будет обрабатывать медиа для новых постов. Существующие посты требуют перепарсинга для обработки медиа.
