# GigaChat Vision Models Configuration

## Context7: Поддержка динамического выбора моделей

Система поддерживает динамический выбор модели GigaChat для Vision анализа.

## Поддерживаемые модели

| Модель | Описание | Рекомендации |
|--------|----------|--------------|
| `GigaChat-Pro` | По умолчанию, оптимальный баланс | Для большинства задач |
| `GigaChat-Max` | Максимальная точность | Для сложных изображений |
| `GigaChat` | Базовая модель | Для простых задач |
| `GigaChat-Multi` | Мультимодальная модель | Для сложных мультимодальных задач |

## Способы изменения модели

### 1. Через переменную окружения (рекомендуется)

```bash
# В .env файле
GIGACHAT_VISION_MODEL=GigaChat-Max
```

### 2. Через переменную GIGACHAT_MODEL (общая для всех запросов)

```bash
GIGACHAT_MODEL=GigaChat-Max
```

### 3. Через код при инициализации

```python
from ai_adapters.gigachat_vision import GigaChatVisionAdapter

adapter = GigaChatVisionAdapter(
    credentials=credentials,
    model="GigaChat-Max"
)
```

## Приоритет выбора модели

1. Параметр `model` при инициализации `GigaChatVisionAdapter`
2. Переменная окружения `GIGACHAT_VISION_MODEL`
3. Переменная окружения `GIGACHAT_MODEL`
4. Значение по умолчанию: `GigaChat-Pro`

## gpt2giga Proxy

Если используется gpt2giga proxy, модель можно указать:

```bash
# Запуск proxy с моделью
gpt2giga --gigachat-model GigaChat-Max

# Или через переменную
GIGACHAT_MODEL=GigaChat-Max gpt2giga
```

## Передача модели в запросе (gpt2giga)

При использовании `--proxy-pass-model` можно передавать модель в каждом запросе:

```bash
gpt2giga --proxy-pass-model
```

Затем в запросе указывать модель:

```json
{
  "model": "GigaChat-Max",
  "messages": [...]
}
```

## Проверка текущей модели

Модель логируется при инициализации `GigaChatVisionAdapter`:

```
[info] GigaChatVisionAdapter initialized model=GigaChat-Max ...
```

## Исправление upload_file()

GigaChat API требует правильное расширение файла для определения MIME типа.

### Проблема

Ранее файлы загружались без расширения, что приводило к ошибке:
```
File format application/octet-stream is not supported
```

### Решение

Метод `upload_file()` теперь:
1. Принимает `mime_type` параметр
2. Автоматически генерирует `filename` с правильным расширением
3. Использует маппинг MIME → расширение через `_get_extension_from_mime()`

### Пример

```python
file_id = await adapter.upload_file(
    file_content=image_bytes,
    mime_type="image/png"  # Автоматически создаст "file.png"
)
```

## Модели для анализа изображений

Все перечисленные модели поддерживают Vision анализ:
- Распознавание объектов
- OCR (извлечение текста)
- Классификация контента
- Определение мемов
- Анализ контекста

Выбор модели зависит от:
- Требований к точности
- Доступных квот
- Сложности задачи
