# Исправление fallback на OpenRouter

**Дата:** 2025-12-19  
**Проблема:** Fallback на OpenRouter не работает из-за несуществующей модели

## Проблема

Fallback на OpenRouter не работает, потому что:
1. Модель `qwen/qwen-2.5-72b-instruct:free` не существует в OpenRouter (404)
2. Все 12 попыток fallback завершились ошибкой (`digest_openrouter_fallback_failed_total = 12`)
3. В результате пользователи получают фильтрованный контент от Gigachat

## Решение

### 1. Обновлена модель по умолчанию

Изменена модель с `qwen/qwen-2.5-72b-instruct:free` на `meta-llama/llama-3.3-70b-instruct:free`

**Файлы:**
- `api/services/digest_service.py` - обновлена модель по умолчанию
- `env.example` - обновлен пример конфигурации

### 2. Улучшена обработка ошибок

Добавлена специальная обработка для ошибки 404 (модель не найдена):
- Детальное логирование ошибки
- Подсказка о необходимости обновить переменную окружения

### 3. Проверка фильтра в ответе от OpenRouter

Добавлена проверка фильтра в ответе от OpenRouter (уже было реализовано ранее)

## Действия для применения

### Обновление .env файла

Обновите переменную `OPENROUTER_MODEL` в файле `.env`:

```bash
# Старое значение (не работает):
OPENROUTER_MODEL=qwen/qwen-2.5-72b-instruct:free

# Новое значение:
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct:free
```

### Перезапуск worker

После обновления `.env` перезапустите worker контейнер:

```bash
docker-compose restart worker
```

## Альтернативные модели

Если `meta-llama/llama-3.3-70b-instruct:free` не подходит, можно использовать:

- `mistralai/mistral-small-3.1-24b-instruct:free` - меньший размер, быстрее
- `meta-llama/llama-3.1-405b-instruct:free` - больший размер, лучше качество
- `qwen/qwen-2.5-vl-7b-instruct:free` - если нужна поддержка vision

## Проверка работы

После применения изменений проверьте:

1. **Метрики Prometheus:**
   ```bash
   curl 'http://localhost:9090/api/v1/query?query=digest_openrouter_fallback_total'
   ```

2. **Логи worker:**
   ```bash
   docker logs telegram-assistant-worker-1 | grep -i "openrouter\|fallback"
   ```

3. **Тест fallback:**
   ```bash
   ./scripts/test_openrouter_fallback.sh
   ```

## Статус

- ✅ Код обновлен
- ⚠️ Требуется обновление `.env` файла
- ⚠️ Требуется перезапуск worker
