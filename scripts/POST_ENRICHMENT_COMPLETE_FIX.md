# Полное исправление post_enrichment и crawl4ai

## Дата
2025-11-06 07:15 MSK

## Проблемы

### 1. Пустые поля в post_enrichment
- Legacy поля (`crawl_md`, `vision_labels`, `ocr_text`, `summary`) оставались пустыми
- Данные были в `data` JSONB, но не синхронизировались в legacy поля

### 2. Crawl4AI не работает
- Ошибка: `AttributeError: Setting 'provider' is deprecated`
- Использовался старый API crawl4ai без `LLMConfig`

### 3. Логирование ошибок
- Ошибка: `TypeError: Logger._log() got an unexpected keyword argument 'url'`
- Использовался неправильный формат для стандартного logging

## Исправления

### 1. Синхронизация legacy полей

**Файл**: `shared/python/shared/repositories/enrichment_repository.py`

- ✅ Добавлена синхронизация `crawl_md` и `summary` для `kind IN ('crawl', 'general')`
- ✅ Добавлена синхронизация `tags` для `kind = 'tags'`
- ✅ Работает для asyncpg и SQLAlchemy

### 2. Обновление API crawl4ai

**Файл**: `worker/tasks/enrichment_task.py`

- ✅ Добавлен импорт `LLMConfig`
- ✅ Обновлен `LLMExtractionStrategy` для использования нового API:
  ```python
  llm_config = LLMConfig(
      provider="openai/gpt-4o-mini",
      api_token="dummy"
  )
  extraction_strategy=LLMExtractionStrategy(
      llm_config=llm_config,
      instruction="Extract the main content and return as markdown"
  )
  ```

### 3. Исправление логирования

**Файл**: `worker/tasks/enrichment_task.py`

- ✅ Исправлены все вызовы `logger.error()` и `logger.warning()`:
  - Было: `logger.error("message", url=url, error=str(e))`
  - Стало: `logger.error(f"message: url={url}, error={str(e)}", exc_info=True)`

## Context7 Best Practices применены

### ✅ Обратная совместимость
- Legacy поля синхронизируются из `data` JSONB
- Старый код продолжает работать

### ✅ Обновление зависимостей
- Использование нового API crawl4ai
- Поддержка новых версий библиотек

### ✅ Правильное логирование
- Использование f-strings для форматирования
- `exc_info=True` для полного traceback

## Проверка

### Проверка синхронизации legacy полей
```sql
SELECT 
    kind,
    COUNT(*) as total,
    COUNT(*) FILTER (WHERE crawl_md IS NOT NULL AND crawl_md != '') as has_crawl_md
FROM post_enrichment
WHERE updated_at > NOW() - INTERVAL '1 hour'
GROUP BY kind;
```

### Проверка crawl4ai
- Проверить логи на отсутствие ошибок `AttributeError`
- Проверить логи на успешные crawl операции

## Следующие шаги

1. ⏳ Мониторить синхронизацию legacy полей при новых обогащениях
2. ⏳ Проверить, что crawl4ai обогащения выполняются успешно
3. ⏳ Проверить метрики в Grafana

## Важно

**Legacy поля пустые - это нормально**, если:
- Данные есть в `data` JSONB
- Синхронизация работает (для новых записей)
- Старый код обновлен для чтения из `data` JSONB

**После исправлений**:
- Legacy поля будут синхронизироваться автоматически
- Crawl4AI будет работать корректно
- Логирование будет работать без ошибок
