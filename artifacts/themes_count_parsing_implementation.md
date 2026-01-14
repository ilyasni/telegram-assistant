# Парсинг общего количества подборок из текста страницы

**Дата**: 2026-01-13  
**Задача**: Определять общее количество подборок по тексту "каналов и чатов" на странице

## Реализация

### Изменения в `tgstat-parser/parser/tgstat_parser.py`

**Метод**: `parse_themes_list()`

Добавлен парсинг общего количества подборок из текста страницы перед извлечением списка тем:

```python
# Context7: Парсинг общего количества подборок из текста страницы
# Ищем паттерны типа "369 каналов и чатов" для понимания общего количества
page_text = soup.get_text()
total_themes_on_page = None
count_patterns = [
    r'(\d+)\s+каналов?\s+и\s+чатов?',
    r'(\d+)\s+подборок?',
    r'(\d+)\s+тем',
    r'всего\s+(\d+)',
    r'(\d+)\s+тегов?',
]
for pattern in count_patterns:
    matches = re.findall(pattern, page_text, re.IGNORECASE)
    if matches:
        # Берем наибольшее число (обычно это общее количество)
        numbers = [int(m) for m in matches]
        total_themes_on_page = max(numbers)
        logger.info(
            "Total themes count found on page",
            total=total_themes_on_page,
            pattern=pattern
        )
        break
```

### Логирование и валидация

После парсинга тем добавлено сравнение ожидаемого и фактического количества:

```python
# Context7: Логирование результатов с сравнением ожидаемого и фактического количества
parsed_count = len(themes)
logger.info(
    "Themes list parsed",
    themes_count=parsed_count,
    total_themes_on_page=total_themes_on_page,
    url=url
)

# Context7: Предупреждение, если найдено меньше тем, чем указано на странице
if total_themes_on_page and parsed_count < total_themes_on_page:
    missing_count = total_themes_on_page - parsed_count
    logger.warning(
        "Not all themes were parsed",
        expected=total_themes_on_page,
        found=parsed_count,
        missing=missing_count,
        missing_pct=round(missing_count / total_themes_on_page * 100, 1)
    )
```

## Преимущества

1. **Диагностика**: Понимание, сколько тем должно быть на странице
2. **Валидация**: Автоматическое обнаружение проблем парсинга
3. **Мониторинг**: Логирование расхождений между ожидаемым и фактическим количеством

## Текущая ситуация

- **В БД**: 96 тем
- **Ожидается на сайте**: 369 тем (по информации пользователя)
- **Проблема**: Cloudflare блокирует requests fallback (403 Forbidden)
- **Решение**: Нужно исправить Selenium/Xvfb для обхода Cloudflare

## Следующие шаги

1. Исправить проблему с Selenium/Xvfb
2. Запустить полную синхронизацию всех 369 тем
3. Проверить, что все темы сохранены в БД
