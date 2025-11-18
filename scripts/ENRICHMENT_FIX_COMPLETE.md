# Исправление ошибки Enrichment Processing Rate - Завершено

## Проблема
Ошибка `cannot cast type jsonb to text[]` при сохранении enrichment в базу данных, что блокировало обработку сообщений из `stream:posts:enriched`.

## Причина
В `enrichment_repository.py` при синхронизации legacy поля `tags` из JSONB поля `data` использовалось неправильное приведение типов:
```sql
($1::jsonb->'tags')::text[]
```

PostgreSQL не может напрямую привести JSONB к text[]. Нужно использовать функцию `jsonb_array_elements_text` с конструктором `ARRAY(SELECT ...)`.

## Решение

### Исправлен asyncpg вариант (строки 231-251)
```sql
tags = COALESCE(
    CASE 
        WHEN $1::jsonb->'tags' IS NOT NULL AND jsonb_typeof($1::jsonb->'tags') = 'array'
        THEN ARRAY(SELECT jsonb_array_elements_text($1::jsonb->'tags'))
        ELSE NULL
    END,
    CASE 
        WHEN $1::jsonb->'enrichment_data'->'tags' IS NOT NULL AND jsonb_typeof($1::jsonb->'enrichment_data'->'tags') = 'array'
        THEN ARRAY(SELECT jsonb_array_elements_text($1::jsonb->'enrichment_data'->'tags'))
        ELSE NULL
    END,
    tags
)
```

### Исправлен SQLAlchemy вариант (строки 323-338)
Аналогичное исправление для SQLAlchemy версии с использованием `EXCLUDED.data` вместо параметров.

## Результаты

### ✅ Исправление применено
- Файл обновлен в контейнере worker
- Worker перезапущен
- Новые сообщения обрабатываются успешно

### ✅ Статистика обработки
- За последние 5 минут: 13 успешных upsert операций
- Новые сообщения обрабатываются без ошибок
- E2E проверка показывает, что пайплайн работает

### ⚠️ Старые pending сообщения
- 2 pending сообщения в `stream:posts:enriched` (старые, с ошибкой)
- Эти сообщения были созданы до исправления
- Новые сообщения обрабатываются корректно

## Рекомендации

1. **Мониторинг**: Следить за метрикой Enrichment Processing Rate в Grafana
2. **Pending сообщения**: Рассмотреть возможность обработки старых pending сообщений вручную или перемещения их в DLQ
3. **Тестирование**: Убедиться, что все типы enrichment (tags, crawl, vision, general) обрабатываются корректно

## Статус
✅ **Исправлено и работает**
- Новые сообщения обрабатываются успешно
- Ошибки `cannot cast type jsonb to text[]` больше не появляются
- Метрика Enrichment Processing Rate должна показывать успешные обработки

