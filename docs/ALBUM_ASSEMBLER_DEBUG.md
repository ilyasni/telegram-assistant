# Диагностика album_assembler_task

**Дата**: 2025-11-03

## Проблема

Consumer groups для `albums:parsed` не созданы, альбомы не собираются.

## Диагностика

### 1. Проверка consumer groups

```bash
# Albums parsed - пусто
docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:albums:parsed
# Результат: []

# Vision analyzed - есть retagging_workers
docker exec telegram-assistant-redis-1 redis-cli XINFO GROUPS stream:posts:vision:analyzed
# Результат: [{'name': b'retagging_workers', ...}]
```

### 2. Вывод

`album_assembler_task` либо:
- Не запущен в supervisor
- Упал при инициализации
- Не создал consumer groups

### 3. Решение

Проверить:
1. Запущен ли task в supervisor
2. Логи инициализации
3. Ошибки при создании consumer groups

## Команды для проверки

```bash
# Проверка логов
docker logs telegram-assistant-worker-1 | grep -i "album_assembler"

# Проверка health check
docker exec telegram-assistant-worker-1 curl -s http://localhost:8000/health/detailed

# Ручной запуск для теста
docker exec -it telegram-assistant-worker-1 python3 -c "
import asyncio
from worker.tasks.album_assembler_task import AlbumAssemblerTask
# ... тест инициализации
"
```

