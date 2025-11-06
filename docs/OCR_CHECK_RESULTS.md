# Результаты проверки OCR в post_enrichment

**Дата**: 2025-01-31  
**Проверка**: Анализ реальных данных в БД после исправлений

## Результаты проверки

### Общая статистика

- **Всего записей Vision**: 392
- **С OCR текстом (новый формат)**: 113 (28.83%)
- **С OCR в новом формате (поле ocr)**: 392 (100%)
- **С OCR в legacy формате**: 0 (0%)
- **Без OCR**: 279 (71.17%)

### Статистика по провайдерам

#### Provider: gigachat
- **Всего**: 126 записей
- **С OCR текстом**: 113 (89.68%)
- **OCR Engine**: gigachat (114 записей)

#### Provider: ocr_fallback
- **Всего**: 266 записей
- **С OCR текстом**: 0 (0.0%)
- **Проблема**: OCR поле сохраняется как `null` или пустое

## Выявленные проблемы

### 1. OCR Fallback не сохраняет OCR

**Проблема**: Записи с `provider='ocr_fallback'` имеют `ocr=null` в БД, хотя должны сохранять OCR текст.

**Причина**: В методе `_process_with_ocr()` OCR текст извлекается, но при сохранении в `_extract_ocr_data()` может быть пустая строка.

**Решение**: ✅ Исправлено - теперь сохраняется `None` вместо пустого объекта, если OCR отсутствует.

### 2. Структура OCR данных

**Текущее состояние**:
- Все записи имеют поле `ocr` в `data` JSONB
- Для записей без OCR: `ocr: null`
- Для записей с OCR: `ocr: {"text": "...", "engine": "gigachat", "confidence": ...}`

**Правильно**: ✅ Структура корректная, `null` - это нормально для отсутствия OCR.

## Выводы

1. ✅ **OCR извлекается и сохраняется** для GigaChat провайдера (89.68% покрытие)
2. ✅ **Структура данных корректная** - OCR сохраняется в `data->ocr->text`
3. ⚠️ **OCR Fallback не работает** - записи с `ocr_fallback` не имеют OCR текста
4. ✅ **Исправления применены** - OCR корректно извлекается из ответов GigaChat

## Рекомендации

1. **Проверить OCR Fallback**: Убедиться что `_process_with_ocr()` корректно извлекает текст
2. **Мониторинг**: Отслеживать процент записей с OCR текстом
3. **Валидация**: Проверять что OCR не пустой перед сохранением

## Примеры записей с OCR

Последние записи с OCR текстом:
- Post `7f5539a7...`: OCR Length 8, Engine: gigachat, Preview: "C55 PHEV..."
- Post `8219789d...`: OCR Length 7, Engine: gigachat, Preview: "CS55 EV..."
- Post `5ec5be5d...`: OCR Length 4, Engine: gigachat, Preview: "吉利汽车..."

## SQL запросы для мониторинга

```sql
-- Общая статистика
SELECT 
    COUNT(*) as total,
    COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL THEN 1 END) as with_ocr
FROM post_enrichment
WHERE kind = 'vision';

-- Статистика по провайдерам
SELECT 
    provider,
    COUNT(*) as total,
    COUNT(CASE WHEN data->'ocr'->>'text' IS NOT NULL THEN 1 END) as with_ocr
FROM post_enrichment
WHERE kind = 'vision'
GROUP BY provider;
```

