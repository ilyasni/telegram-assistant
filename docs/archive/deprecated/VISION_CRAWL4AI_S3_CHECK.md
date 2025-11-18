# Проверка Vision и Crawl4ai после восстановления S3

**Дата**: 2025-11-17  
**Статус**: ⚠️ Обнаружены проблемы

## Контекст

После восстановления S3 пайплайна необходимо проверить работу Vision и Crawl4ai, которые зависят от S3 для получения и сохранения данных.

## Результаты проверки

### ✅ Vision: Интеграция с S3

**Код интеграции**:
- `VisionAnalysisTask` использует `S3StorageService.get_object()` для получения медиа файлов (строка 1259)
- `VisionAnalysisTask` использует `S3StorageService.head_object()` для проверки существования (строка 1055)
- Обработка ошибок S3 (404, NoSuchKey) реализована корректно

**Метрики Vision**:
- `vision_events_total`: пусто (нет данных)
- `vision_parse_errors_total`: пусто (нет данных)

**Проблема**: Нет событий в stream `posts:vision` - MediaProcessor не эмитит события `VisionUploadedEventV1`

### ⚠️ Vision: Отсутствие событий

**Диагностика**:
1. Stream `posts:vision` пуст (нет событий)
2. Последние посты с медиа не имеют `media_objects_count = 0` и `s3_media_count = 0`
3. Нет логов эмиссии событий `VisionUploadedEventV1` в `telethon-ingest`

**Возможные причины**:
1. MediaProcessor не вызывается для новых постов
2. MediaProcessor не сохраняет медиа в `media_objects` через `AtomicDBSaver`
3. MediaProcessor не эмитит события `VisionUploadedEventV1` (ошибка в `emit_vision_uploaded_event`)
4. `media_files` не передаются в `post_data` в `ChannelParser._process_message_batch`

**Код эмиссии**:
- `MediaProcessor.emit_vision_uploaded_event()` (строка 776-837)
- Вызывается в `ChannelParser._process_message_batch()` (строка 2480)
- Условие: `if self.media_processor and post_data.get('media_files')`

### ✅ Crawl4ai: EnrichmentTask работает

**Метрики**:
- `enrichment_requests_total{success="True"}`: 6 (последнее обогащение успешно)

**Статус**: EnrichmentTask обрабатывает события и выполняет обогащение

### ⚠️ Crawl4ai: crawl_trigger перезапускается

**Проблема**: `crawl_trigger` перезапускается каждые 30 секунд
- Логи: `[WARNING] supervisor: Task crawl_trigger completed unexpectedly, will be restarted`

**Причина**: Задача завершается без явного исключения (уже диагностирована ранее)

**Статус**: Проблема известна, требуется дополнительная диагностика

### ⚠️ Crawl4ai: Нет событий в stream `posts:crawl`

**Диагностика**:
- Stream `posts:crawl` существует, но пуст
- `crawl_trigger` должен публиковать события в `posts:crawl` при обнаружении trigger tags

**Возможные причины**:
1. Нет постов с trigger tags
2. `crawl_trigger` не обрабатывает события из-за перезапусков
3. События не публикуются из-за ошибок

## Проблемы

### 1. Vision: MediaProcessor не эмитит события

**Симптомы**:
- Нет событий в stream `posts:vision`
- Последние посты с медиа не имеют `media_objects_count = 0`
- Нет логов эмиссии событий

**Требуется проверка**:
1. Вызывается ли `MediaProcessor.process_message_media()` для новых постов?
2. Сохраняются ли медиа в `media_objects` через `AtomicDBSaver`?
3. Передаются ли `media_files` в `post_data` в `ChannelParser._process_message_batch()`?
4. Эмитируются ли события `VisionUploadedEventV1` в `emit_vision_uploaded_event()`?

### 2. Crawl4ai: crawl_trigger перезапускается

**Симптомы**:
- Задача перезапускается каждые 30 секунд
- Нет событий в stream `posts:crawl`

**Требуется проверка**:
1. Почему задача завершается без явного исключения?
2. Обрабатываются ли события из `posts.tagged`?
3. Публикуются ли события в `posts:crawl`?

## Рекомендации

### Немедленные действия

1. **Проверить логи MediaProcessor**:
   ```bash
   docker compose logs --tail=1000 telethon-ingest | grep -iE "(media.*process|vision.*upload|emit.*vision)"
   ```

2. **Проверить вызов MediaProcessor**:
   - Убедиться, что `MediaProcessor.process_message_media()` вызывается для новых постов
   - Проверить, что `media_files` передаются в `post_data`

3. **Проверить сохранение медиа**:
   - Убедиться, что медиа сохраняются в `media_objects` через `AtomicDBSaver`
   - Проверить, что `s3_key` устанавливается корректно

4. **Проверить эмиссию событий**:
   - Убедиться, что `emit_vision_uploaded_event()` вызывается
   - Проверить, что `redis_client` доступен в `MediaProcessor`
   - Проверить, что события публикуются в правильный stream (`stream:posts:vision`)

5. **Проверить crawl_trigger**:
   - Диагностировать причину перезапуска
   - Проверить обработку событий из `posts.tagged`

### Долгосрочные улучшения

1. **Мониторинг Vision**:
   - Добавить метрики для отслеживания эмиссии событий
   - Добавить алерты на отсутствие событий в stream `posts:vision`

2. **Мониторинг Crawl4ai**:
   - Добавить метрики для отслеживания публикации событий в `posts:crawl`
   - Добавить алерты на перезапуски `crawl_trigger`

## Следующие шаги

1. ⚠️ Диагностировать отсутствие событий `VisionUploadedEventV1`
2. ⚠️ Проверить вызов `MediaProcessor.process_message_media()` для новых постов
3. ⚠️ Проверить сохранение медиа в `media_objects`
4. ⚠️ Исправить перезапуск `crawl_trigger`
5. ⚠️ Проверить публикацию событий в `posts:crawl`

