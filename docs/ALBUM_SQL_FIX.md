# Исправление SQL ошибки в media_group_saver.py

**Дата**: 2025-11-05  
**Context7**: Исправление SQL синтаксической ошибки при сохранении альбомов

---

## Проблема

При сохранении альбомов в `media_groups` возникала SQL синтаксическая ошибка:
```
syntax error at or near ":"
```

**Ошибка в SQL запросе**:
```sql
INSERT INTO media_group_items (...)
VALUES (
    $1, $2, $3, $4,
    $5, $6, $7,
    $8, $9, :meta::jsonb  -- ❌ Смешение форматов параметров
)
```

**Причина**: SQLAlchemy/asyncpg компилирует параметры в формат `$1, $2, ...`, но `:meta::jsonb` оставался в тексте запроса, что вызывало синтаксическую ошибку.

---

## Исправление

**Файл**: `telethon-ingest/services/media_group_saver.py` (строки 279, 294)

**Изменения**:
1. **Изменен формат `meta`**: Передаем `dict` вместо JSON строки (`json.dumps({})`)
   ```python
   "meta": {}  # Context7: Передаем dict напрямую
   ```

2. **Исправлен SQL запрос**: Используем `CAST(:meta AS jsonb)` вместо `:meta::jsonb`
   ```sql
   VALUES (
       :group_id, :post_id, :position, :media_type,
       :media_bytes, :media_sha256, :sha256,
       :media_object_id, :media_kind, CAST(:meta AS jsonb)  -- ✅ Правильный формат
   )
   ```

**Преимущества**:
- Совместимость с SQLAlchemy/asyncpg (компилируется в `$1, $2, ...`)
- Используется тот же подход, что в `enrichment_repository.py` (строка 225)
- PostgreSQL автоматически преобразует dict в JSONB через CAST

---

## Ожидаемый результат

После перезапуска `telethon-ingest`:
1. Альбомы будут сохраняться в `media_groups` без SQL ошибок
2. `media_group_items` будут заполняться корректно
3. События `albums.parsed` будут эмитироваться (если `tenant_id` присутствует)

---

## Документация

- `telethon-ingest/services/media_group_saver.py` - Media Group Saver
- `shared/python/shared/repositories/enrichment_repository.py` - Enrichment Repository (пример использования CAST)
- `docs/ALBUM_SAVING_FIX.md` - Отчет об исправлении получения `user_id`

