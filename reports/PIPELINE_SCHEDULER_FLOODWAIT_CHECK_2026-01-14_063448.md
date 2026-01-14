# Комплексная проверка пайплайна, Scheduler и FloodWait

**Дата**: 2026-01-14T06:34:48.410545+00:00

---

## Сводка по этапам

| Этап | Статус | Детали |
|------|--------|--------|
| Telethon-ingest Scheduler | ✅ ok | Запущен (status: ok) |
| API Scheduler | ❌ error | Не запущен |
| FloodWait | ✅ ok | Нет активных FloodWait |
| Парсинг | ✅ ok | 149 постов за 24ч |
| Vision анализ | ⚠️ warning | 0 анализов за 24ч |
| Тегирование | ✅ ok | 143 тегирований за 24ч |
| Обогащение (Crawl4AI) | ✅ ok | 0 обогащений за 24ч |
| Qdrant | ✅ ok | 8 коллекций |
| Neo4j | ✅ ok | 12994 постов, 1863 альбомов |
| Индексация | ❌ error | 0 проиндексировано за 24ч |

## Проблемы

- **API Scheduler**: Ошибка проверки: Cannot connect to host api:8001 ssl:default [Connect call failed ('172.18.0.13', 8001)]
- **Vision анализ**: Нет vision анализов за последние 24 часа
- **Vision анализ**: Pending сообщений: 2
- **Индексация**: Ошибка проверки индексации: syntax error at or near "is"
