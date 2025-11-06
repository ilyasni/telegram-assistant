# Улучшения Fallback механизма Vision Analysis

**Дата**: 2025-11-06  
**Статус**: ✅ Реализовано

## Context

Улучшен fallback механизм для Vision анализа: добавлен автоматический fallback на OpenRouter Vision при ошибках GigaChat API и исправлена проверка quota_exhausted.

## Plan

1. ✅ Исправлена проверка quota_exhausted через budget_gate
2. ✅ Добавлен fallback на OpenRouter Vision при ошибках GigaChat после retries
3. ✅ Добавлен fallback на OpenRouter Vision при исключениях GigaChat
4. ✅ Улучшено логирование для диагностики

## Patch

### 1. Исправлена проверка quota_exhausted

**Файл**: `worker/tasks/vision_analysis_task.py`

**Проблема**: `quota_exhausted=False  # TODO: проверить через budget_gate`

**Решение**: Добавлена реальная проверка через budget_gate перед вызовом policy_engine:

```python
# Сначала проверяем budget gate для определения quota_exhausted
quota_exhausted = False
if self.budget_gate:
    budget_check = await self.budget_gate.check_budget(
        tenant_id=tenant_id,
        estimated_tokens=1792
    )
    quota_exhausted = not budget_check.allowed

policy_result = self.policy_engine.evaluate_media_for_vision(
    media_file={...},
    channel_username=None,
    quota_exhausted=quota_exhausted  # Теперь используется реальное значение
)
```

### 2. Fallback на OpenRouter Vision при ошибках после retries

**Проблема**: При ошибках GigaChat Vision API после всех retries медиа просто пропускалось

**Решение**: Добавлен автоматический fallback на OpenRouter Vision:

```python
if not analysis_result:
    # Fallback на OpenRouter Vision
    if self.ocr_fallback and hasattr(self.ocr_fallback, 'analyze_image'):
        logger.info("GigaChat Vision failed after retries, falling back to OpenRouter Vision")
        fallback_result = await self._process_with_ocr(media_file, tenant_id, post_id, trace_id)
        if fallback_result:
            return fallback_result
```

### 3. Fallback на OpenRouter Vision при исключениях

**Проблема**: При исключениях в методе `_analyze_media` медиа просто пропускалось

**Решение**: Добавлен fallback в блоке `except Exception`:

```python
except Exception as e:
    logger.warning("GigaChat Vision analysis exception, trying OpenRouter Vision fallback")
    
    if self.ocr_fallback and hasattr(self.ocr_fallback, 'analyze_image'):
        try:
            fallback_result = await self._process_with_ocr(media_file, tenant_id, post_id, trace_id)
            if fallback_result:
                logger.info("OpenRouter Vision fallback succeeded after GigaChat exception")
                return fallback_result
        except Exception as fallback_error:
            logger.warning("OpenRouter Vision fallback also failed")
```

## Checks

### Проверка fallback механизма

```bash
# Проверить логи на использование fallback
docker compose logs worker | grep -iE "(fallback|openrouter|gigachat.*failed)" | tail -20

# Должны быть логи:
# GigaChat Vision failed after retries, falling back to OpenRouter Vision
# OpenRouter Vision fallback succeeded
```

### Проверка quota_exhausted

```bash
# Проверить логи на проверку budget gate
docker compose logs worker | grep -iE "(budget|quota_exhausted)" | tail -10

# Должны быть логи с реальными значениями quota_exhausted
```

## Impact / Rollback

### Преимущества

1. **Автоматический fallback**: OpenRouter Vision используется автоматически при ошибках GigaChat
2. **Правильная проверка quota**: quota_exhausted теперь проверяется через budget_gate
3. **Улучшенная надёжность**: Медиа анализируется даже при временных сбоях GigaChat
4. **Лучшая диагностика**: Детальное логирование для понимания причин fallback

### Обратная совместимость

- ✅ Все изменения обратно совместимы
- ✅ Fallback опционален (работает только если ocr_fallback доступен)
- ✅ Стандартное поведение сохранено

### Rollback

Если нужно откатить изменения:
1. Удалить fallback логику из `_analyze_media`
2. Вернуть `quota_exhausted=False` в policy_engine.evaluate_media_for_vision

## Best Practices (Context7)

### 1. Fallback Strategy

- ✅ Автоматический fallback при ошибках primary провайдера
- ✅ Graceful degradation вместо полного отказа
- ✅ Детальное логирование для диагностики

### 2. Quota Management

- ✅ Проверка quota через budget_gate перед анализом
- ✅ Правильная передача quota_exhausted в policy engine
- ✅ Fallback на OpenRouter при quota exhausted

### 3. Error Handling

- ✅ Retry логика для transient ошибок
- ✅ Fallback на альтернативный провайдер при permanent ошибках
- ✅ Детальное логирование для observability

## Выводы

1. ✅ **Quota проверка исправлена**: Используется реальное значение из budget_gate
2. ✅ **Fallback добавлен**: OpenRouter Vision используется автоматически при ошибках GigaChat
3. ✅ **Надёжность улучшена**: Медиа анализируется даже при сбоях primary провайдера
4. ✅ **Логирование улучшено**: Детальные логи для диагностики fallback сценариев

## Следующие шаги

1. Мониторить логи на использование fallback
2. Проверить метрики на количество fallback запросов
3. Настроить алерты на частые fallback (может указывать на проблемы с GigaChat)
4. Рассмотреть использование платных моделей OpenRouter при частых fallback

