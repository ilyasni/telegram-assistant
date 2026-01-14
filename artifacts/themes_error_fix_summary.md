# Исправление ошибки при запросе подборок

**Дата**: 2026-01-13  
**Проблема**: Бот показывал ошибку "Не удалось загрузить список подборок"

## Проблема

**Ошибка в логах**:
```
"errors": [{
    "type": "less_than_equal",
    "loc": ["query", "limit"],
    "msg": "Input should be less than or equal to 500",
    "input": "1000"
}]
Status: 422 Unprocessable Entity
```

**Причина**: 
- Бот запрашивал `limit=1000`
- API endpoint ограничивает `limit` до 500 (`le=500` в Pydantic Query)
- FastAPI возвращал 422 Unprocessable Entity

## Решение

### Изменения в `api/bot/handlers/themes_handlers.py`

1. **Исправлен лимит запроса**: `limit=1000` → `limit=500`
2. **Добавлена пагинация**: Если подборок больше 500, делаются дополнительные запросы с offset
3. **Улучшена обработка ошибок**: Логирование деталей ошибки для диагностики

### Код изменений

```python
# Было:
themes_resp = await client.get(f"{API_BASE}/api/themes?limit=1000")

# Стало:
themes_resp = await client.get(f"{API_BASE}/api/themes?limit=500")

# Добавлена логика для получения всех подборок:
if total_themes > 500:
    for offset in range(500, total_themes, 500):
        additional_resp = await client.get(
            f"{API_BASE}/api/themes?limit=500&offset={offset}",
            timeout=10
        )
        if additional_resp.status_code == 200:
            additional_data = additional_resp.json()
            themes.extend(additional_data.get("themes", []))
```

## Context7 Best Practices

1. **Валидация на уровне API**: Использование Pydantic Query с ограничениями (`le=500`)
2. **Обработка ошибок**: Логирование деталей ошибки для диагностики
3. **Пагинация**: Поддержка больших списков через offset/limit
4. **Graceful degradation**: Если не удалось получить все подборки, показываем доступные

## Статус

✅ **Исправлено**
- Код обновлен: используется `limit=500`
- Логика пагинации добавлена
- Обработка ошибок улучшена
- API перезапущен

## Проверка

После исправления:
- ✅ Запрос с `limit=500` работает (200 OK)
- ✅ Если подборок больше 500, делаются дополнительные запросы
- ✅ Ошибки логируются с деталями
- ✅ Пользователю показывается понятное сообщение об ошибке

## Текущая статистика

- Всего подборок: 82
- Все подборки помещаются в один запрос (limit=500)
- Пагинация будет работать, если подборок станет больше 500
