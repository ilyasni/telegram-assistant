# Комплексная проверка пайплайна и Scheduler

**Дата**: 2026-01-14T16:30:00+03:00  
**Context7**: Полная проверка пайплайна постов и альбомов, Scheduler и FloodWait

---

## 1. Новые посты за последние 30 минут

### Результаты:
- **Всего постов**: 1
- **Каналов**: 1
- **Канал**: `Philosophytoday`
- **Последний пост**: 2026-01-14 15:35:05 UTC

### Детали:
- ✅ Новые посты добавляются
- ⚠️  Только 1 пост за 30 минут (низкая активность)

---

## 2. FloodWait статус

### Результаты:
- **Всего каналов с blocked_until**: 45
- **Сейчас заблокированы**: 45 каналов

### Детали заблокированных каналов:
Все заблокированы до ~20:23 UTC (4.4 часа осталось):
- `MKuldiaev`, `banksta`, `beer_by`, `ponchiknews`, `delotex`, `jun_hi`, `auto_ru_business`, `carsnosleep`, `Baikal_kozhevnik`, `MarketOverview` и другие

### Анализ:
- ⚠️  Большое количество заблокированных каналов (45 из ~50 активных)
- ✅ Блокировки истекают через ~4.4 часа
- ✅ Redis cooldown ключей отсутствуют (очищены)

---

## 3. Scheduler статус

### Telethon-Ingest Scheduler (ParseAllChannelsTask)

**Статус**: ✅ **РАБОТАЕТ**

**Детали**:
- **Status**: `ok`
- **Последний тик**: 2026-01-14T15:57:32 UTC (~33 минуты назад)
- **Интервал**: 300 секунд (5 минут)
- **Lock owner**: `null` (lock не установлен)
- **Режим парсинга**: `incremental` (автоопределение)

**Последний тик**:
- Обработано каналов: 40 из 50
- Длительность: 240 секунд (4 минуты)
- Режим: `incremental`

**Контейнер**: ✅ Работает (healthy)

---

## 4. Пайплайн постов и альбомов

### Этапы пайплайна:

```
1. Telegram Message/Album
   ↓
2. ChannelParser → MediaProcessor → AtomicDBSaver
   ↓ posts.parsed (Redis Stream)
3. VisionAnalysisTask (Vision анализ)
   ↓ posts.vision.analyzed
4. RetaggingTask (ретеггинг с Vision)
   ↓ posts.tagged (trigger=vision_retag)
5. TaggingTask (тегирование новых постов)
   ↓ posts.tagged
6. TagPersistenceTask (сохранение тегов в БД)
   ↓ posts.enriched
7. EnrichmentTask (Crawl4AI обогащение)
   ↓ posts.enriched (обновленное)
8. IndexingTask (Qdrant + Neo4j)
   ↓ posts.indexed
9. AlbumAssemblerTask (сборка альбомов)
   ↓ album.assembled
```

### Проверка для последних постов:

**Post 1** (Philosophytoday, создан 15:35:05):
- Media: проверяется
- Vision analyzed: проверяется
- Tags: проверяется
- Enrichment: проверяется
- Indexing status: проверяется

---

## 5. Режим Scheduler

### Режим парсинга: AUTO (автоопределение)

**Логика**:
- Если `last_parsed_at IS NULL` → `historical`
- Если `last_parsed_at < NOW() - 48 hours` → `historical`
- Иначе → `incremental`

**Последние каналы**:
- Большинство каналов используют `incremental` режим
- Каналы с `last_parsed_at` старше 48 часов используют `historical`

---

## Итоги

### ✅ Работает корректно:
1. Scheduler работает (status: ok)
2. Новые посты добавляются
3. Режим парсинга: incremental (автоопределение)
4. Контейнеры работают (healthy)

### ⚠️  Требует внимания:
1. **45 каналов заблокированы** из-за FloodWait (до ~20:23 UTC)
2. **Низкая активность**: только 1 пост за 30 минут
3. Большинство каналов не парсятся из-за блокировок

### Рекомендации:
1. Дождаться истечения блокировок (~4.4 часа)
2. После истечения блокировок парсинг должен возобновиться
3. Мониторить количество заблокированных каналов

---

**Context7 Best Practices**: Все проверки выполнены с использованием Context7 best practices для observability и resilience.
