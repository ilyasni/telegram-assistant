# Исправление развёртывания Album Assembler Task

**Дата**: 2025-11-03

## Проблема

`album_assembler_task` не запускается в supervisor, хотя зарегистрирован в коде.

**Симптомы**:
- В коде: 9 вызовов `register_task` (включая album_assembler)
- В логах: 8 зарегистрированных tasks
- В логах: "Starting supervisor with 8 tasks"
- `album_assembler` отсутствует в списке запущенных tasks

## Диагностика

### Проверка кода

```bash
# В коде есть регистрация
grep -n "album_assembler" worker/run_all_tasks.py
# Результат: найдено на строках 29, 106-165, 383-390, 248-257

# Количество register_task
grep -c "register_task" worker/run_all_tasks.py
# Результат: 9
```

### Проверка логов

```
2025-11-02 20:13:44,557 [INFO] supervisor: Registered task: tagging
2025-11-02 20:13:44,557 [INFO] supervisor: Registered task: enrichment
...
2025-11-02 20:13:44,558 [INFO] supervisor: Registered task: retagging
# НЕТ: Registered task: album_assembler
```

## Возможные причины

1. **Ошибка при регистрации** — task не был зарегистрирован из-за ошибки
2. **Файл не обновлён** — в контейнере используется старая версия файла
3. **Ошибка импорта** — `create_album_assembler_task` не может быть импортирован

## Решение

1. **Проверить содержимое файла в контейнере**:
   ```bash
   docker exec telegram-assistant-worker-1 cat /app/run_all_tasks.py | grep "album_assembler"
   ```

2. **Перезапустить контейнер** для применения изменений:
   ```bash
   docker restart telegram-assistant-worker-1
   ```

3. **Проверить логи после перезапуска**:
   ```bash
   docker logs telegram-assistant-worker-1 | grep -i "register\|starting.*task\|album"
   ```

4. **Проверить consumer groups**:
   ```bash
   docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:albums:parsed
   ```

## Ожидаемый результат

После исправления:
- В логах: "Registered task: album_assembler"
- В логах: "Starting task: album_assembler"
- Consumer groups для `albums:parsed` созданы
- Альбомы начинают собираться

