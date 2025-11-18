# Ошибка crawl_trigger: Logger._log() got an unexpected keyword argument 'path'

**Дата**: 2025-11-17  
**Статус**: ⚠️ В процессе исправления

## Проблема

`crawl_trigger` падает с ошибкой:
```
Logger._log() got an unexpected keyword argument 'path'
```

Задача перезапускается каждые 30 секунд supervisor'ом.

## Диагностика

### Исправленные файлы

1. ✅ `api/worker/check_pipeline_e2e.py` (4 места)
2. ✅ `api/worker/scripts/export_vision_baseline.py` (1 место)
3. ✅ `api/worker/tasks/group_digest_agent.py` (1 место)
4. ✅ `api/worker/services/vision_policy_engine.py` (1 место)

### Проблема сохраняется

Ошибка все еще возникает при запуске `crawl_trigger`, что означает, что есть еще одно место, где используется `path=` как keyword argument в логировании.

## Возможные причины

1. **Импорт модуля при загрузке**: При импорте `CrawlTriggerTask` через `_import_task` может выполняться код на уровне модуля, который использует `path=`.
2. **Косвенный импорт**: Модуль, импортируемый `crawl_trigger_task.py`, может использовать `path=` в логировании.
3. **Кэшированный байт-код**: Старый `.pyc` файл может содержать старый код.

## Следующие шаги

1. **Очистить кэш Python**: Удалить все `.pyc` файлы и `__pycache__` директории
2. **Проверить косвенные импорты**: Проверить все модули, импортируемые `crawl_trigger_task.py`
3. **Добавить детальное логирование**: Добавить логирование в начало `create_crawl_trigger_task` для определения точного места ошибки
4. **Проверить импорт yaml**: Возможно, проблема в импорте `yaml` или `Path` в `run_all_tasks.py`

## Команды для диагностики

```bash
# Очистка кэша Python
find . -type d -name __pycache__ -exec rm -r {} + 2>/dev/null
find . -name "*.pyc" -delete

# Перезапуск worker
docker compose restart worker

# Проверка логов
docker compose logs -f worker | grep -iE "(crawl_trigger|path|Logger)"
```

