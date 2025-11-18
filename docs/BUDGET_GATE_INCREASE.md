# Увеличение дневного лимита Budget Gate

**Дата**: 2025-11-06  
**Context7**: Увеличение дневного лимита Vision Analysis в 5 раз и сброс счетчика

---

## Изменения

### 1. Увеличение дневного лимита в 5 раз

**Файл**: `worker/run_all_tasks_vision_helper.py`

**Изменение**:
```python
# Было:
"max_daily_tokens": int(os.getenv("GIGACHAT_MAX_DAILY_TOKENS", "250000")),

# Стало:
"max_daily_tokens": int(os.getenv("GIGACHAT_MAX_DAILY_TOKENS", "1250000")),  # Context7: Увеличен в 5 раз (250000 * 5)
```

**Новый лимит**: 1,250,000 токенов в день (было 250,000)

### 2. Сброс счетчика в Redis

**Выполнено**: Все ключи `vision_budget:*` удалены из Redis

**Команда**:
```bash
docker compose exec redis redis-cli KEYS "vision_budget:*" | xargs -I {} docker compose exec redis redis-cli DEL {}
```

---

## Конфигурация

### Переменная окружения

Для переопределения лимита через переменную окружения:

```bash
GIGACHAT_MAX_DAILY_TOKENS=1250000
```

**Примечание**: Если переменная не установлена, используется новое значение по умолчанию (1,250,000).

---

## Проверка

### Проверить текущий лимит:

```bash
# Проверить значение в коде
grep "max_daily_tokens" worker/run_all_tasks_vision_helper.py

# Проверить переменную окружения
docker compose exec worker env | grep GIGACHAT_MAX_DAILY_TOKENS
```

### Проверить счетчик в Redis:

```bash
# Проверить все ключи budget
docker compose exec redis redis-cli KEYS "vision_budget:*"

# Проверить значение для конкретного tenant
docker compose exec redis redis-cli GET "vision_budget:tenant:{tenant_id}:day:2025-11-06"
```

### Проверить логи Budget Gate:

```bash
docker compose logs worker | grep -i "budget\|daily_limit" | tail -20
```

---

## Влияние

1. **Увеличенный лимит**: Теперь можно обработать в 5 раз больше Vision Analysis запросов в день
2. **Сброшенный счетчик**: Все текущие счетчики обнулены, обработка возобновится немедленно
3. **Обратная совместимость**: Переменная окружения `GIGACHAT_MAX_DAILY_TOKENS` по-прежнему работает

---

## Следующие шаги

1. ✅ Увеличен лимит в 5 раз (250000 → 1250000)
2. ✅ Сброшен счетчик в Redis
3. ✅ Добавлен volume mount для `run_all_tasks_vision_helper.py` в docker-compose.yml
4. ✅ Worker перезапущен и использует новый лимит

**Проверка**:
```bash
# Проверить значение в конфигурации
docker compose exec worker python -c "
from run_all_tasks_vision_helper import get_vision_config_from_env
print('max_daily_tokens:', get_vision_config_from_env().get('max_daily_tokens'))
"
# Должно вывести: max_daily_tokens: 1250000
```

**Статус**: ✅ Все изменения применены и работают

