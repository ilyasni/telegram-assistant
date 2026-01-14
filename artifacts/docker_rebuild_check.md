# Проверка необходимости пересборки контейнеров

**Дата**: 2026-01-13  
**Изменения**: Добавлены команды бота для управления подборками

## Анализ изменений

### Измененные файлы:

1. **API сервис** (`api`):
   - `api/bot/handlers/themes_handlers.py` (новый файл)
   - `api/bot/handlers/base.py` (изменен)
   - `api/bot/webhook.py` (изменен)

2. **Webapp** (`caddy`):
   - `webapp/js/channels.js` (изменен)
   - `webapp/channels.html` (изменен)
   - `webapp/js/themes.js` (новый файл)
   - `webapp/themes.html` (новый файл)

3. **TGStat Parser** (`tgstat-parser`):
   - `tgstat-parser/scheduler/tasks.py` (изменен)

## Результаты проверки

### ✅ API сервис — перезапуск достаточен

**Статус**: Volume mount есть  
**Конфигурация**:
```yaml
volumes:
  - ./api:/app:ro
```

**Действие**: 
```bash
docker compose restart api
```

**Примечание**: FastAPI/uvicorn автоматически перезагрузит модули при перезапуске. Новый файл `themes_handlers.py` будет загружен автоматически.

### ✅ Webapp (Caddy) — изменения применяются сразу

**Статус**: Volume mount есть  
**Конфигурация**:
```yaml
volumes:
  - ./webapp:/var/www/webapp:ro
```

**Действие**: Ничего не требуется — Caddy отдает статические файлы, изменения видны сразу.

**Примечание**: Если файлы не обновляются, проверьте кэш браузера или перезапустите Caddy:
```bash
docker compose restart caddy
```

### ⚠️ TGStat Parser — НУЖНА ПЕРЕСБОРКА

**Статус**: Volume mount НЕТ  
**Конфигурация**:
```yaml
volumes:
  - /etc/localtime:/etc/localtime:ro
  - /etc/timezone:/etc/timezone:ro
  # НЕТ монтирования кода!
```

**Действие**: 
```bash
docker compose build tgstat-parser
docker compose up -d tgstat-parser
```

**Причина**: Код копируется в образ при сборке через `COPY` в Dockerfile, поэтому изменения требуют пересборки.

## Рекомендуемые действия

### Минимальный набор (только API и Webapp):

```bash
# Перезапуск API для загрузки новых handlers
docker compose restart api

# Webapp не требует действий, но можно перезапустить для уверенности
docker compose restart caddy
```

### Полный набор (включая TGStat Parser):

```bash
# 1. Пересборка TGStat Parser
docker compose build tgstat-parser

# 2. Перезапуск всех затронутых сервисов
docker compose up -d tgstat-parser api caddy
```

## Проверка после применения

### Проверка API:

```bash
# Проверка, что новый handler загружен
docker compose exec api python -c "from bot.handlers.themes_handlers import router; print('OK')"
```

### Проверка Webapp:

```bash
# Проверка наличия новых файлов
docker compose exec caddy ls -la /var/www/webapp/js/themes.js
docker compose exec caddy ls -la /var/www/webapp/themes.html
```

### Проверка TGStat Parser:

```bash
# Проверка изменений в tasks.py
docker compose exec tgstat-parser grep -A 5 "trigger_sync" /app/scheduler/tasks.py
```

## Итоговая рекомендация

**Для быстрого применения изменений** (без TGStat Parser):
```bash
docker compose restart api caddy
```

**Для полного применения всех изменений**:
```bash
docker compose build tgstat-parser
docker compose up -d tgstat-parser api caddy
```

**Примечание**: Изменения в TGStat Parser не критичны для работы команд бота — они влияют только на автоматическую синхронизацию при изменении подборок администратором. Можно применить позже.
