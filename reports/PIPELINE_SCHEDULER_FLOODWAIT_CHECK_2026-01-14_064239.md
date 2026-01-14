# Комплексная проверка пайплайна, Scheduler и FloodWait

**Дата**: 2026-01-14T06:42:39.106264+00:00

---

## Сводка по этапам

| Этап | Статус | Детали |
|------|--------|--------|
| Telethon-ingest Scheduler | ❌ error | Не запущен |
| API Scheduler | ❌ error | Не запущен |
| FloodWait | ✅ ok | Нет активных FloodWait |
| Парсинг | ✅ ok | 149 постов за 24ч |
| Vision анализ | ⚠️ warning | 0 анализов за 24ч |
| Тегирование | ✅ ok | 143 тегирований за 24ч |
| Обогащение (Crawl4AI) | ✅ ok | 0 обогащений за 24ч |
| Qdrant | ✅ ok | 8 коллекций |
| Neo4j | ✅ ok | 12994 постов, 1863 альбомов |
| Индексация | ✅ ok | 105 проиндексировано за 24ч |

## Проблемы

- **Telethon-ingest Scheduler**: Ошибка проверки: Cannot connect to host localhost:8011 ssl:default [Connect call failed ('127.0.0.1', 8011)]
- **API Scheduler**: Ошибка проверки: Cannot connect to host localhost:8001 ssl:default [Connect call failed ('127.0.0.1', 8001)]
- **FloodWait**: Ошибка проверки Prometheus: Cannot connect to host localhost:9090 ssl:default [Connect call failed ('127.0.0.1', 9090)]
- **Vision анализ**: Нет vision анализов за последние 24 часа
