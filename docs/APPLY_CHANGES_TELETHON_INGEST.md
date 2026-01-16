# Применение изменений в telethon-ingest

**Дата**: 2025-12-03  
**Изменение**: Улучшен алгоритм выбора каналов

---

## Нужна ли пересборка?

**НЕТ, пересборка не нужна!**

### Причина

В `docker-compose.yml` код `telethon-ingest` монтируется как volume:

```yaml
volumes:
  - ./telethon-ingest:/app:ro  # Context7: монтируем код для разработки (read-only для безопасности)
```

Это означает, что изменения в коде применяются **автоматически** без пересборки образа.

---

## Что нужно сделать

### Вариант 1: Перезапуск контейнера (рекомендуется)

```bash
docker compose restart telethon-ingest
```

Это перезагрузит Python-модули и применит изменения.

### Вариант 2: Пересоздание контейнера (если перезапуск не помог)

```bash
docker compose up -d --force-recreate telethon-ingest
```

Это пересоздаст контейнер с новым кодом, но без пересборки образа.

---

## Когда нужна пересборка?

Пересборка нужна только если:

1. **Изменения в `shared` пакете**:
   - Shared устанавливается при сборке образа
   - Для изменений shared требуется: `docker compose build telethon-ingest`

2. **Изменения в `requirements.txt`**:
   - Зависимости устанавливаются при сборке
   - Для новых зависимостей требуется: `docker compose build telethon-ingest`

3. **Изменения в `Dockerfile`**:
   - Любые изменения в Dockerfile требуют пересборки

---

## Проверка изменений

После перезапуска проверьте логи:

```bash
docker compose logs telethon-ingest --tail=50 -f
```

Ищите:
- `"Active channels retrieved (limited per tick)"`
- `channels_with_new_posts`
- `total_new_posts`

---

## Текущее изменение

**Файл**: `telethon-ingest/tasks/parse_all_channels_task.py`

**Изменения**:
- Улучшен SQL-запрос с CTE для приоритизации каналов
- Добавлено логирование `channels_with_new_posts` и `total_new_posts`

**Действие**: Достаточно перезапуска контейнера.

---

**Статус**: Готово к применению (перезапуск контейнера)

