# SearXNG - Финальное исправление Bot Detection

**Дата**: 2025-02-02

## 🔍 Проблема

SearXNG падает с ошибкой `TypeError: schema of /etc/searxng/limiter.toml is invalid!` из-за:
1. Файл `limiter.toml` существует и принадлежит `root:root`
2. SearXNG процесс (непривилегированный пользователь) не может его прочитать
3. Контейнер входит в crash-loop

## ✅ Правильное решение

### Шаг 1: Убедиться, что в settings.yml `limiter: false`

```yaml
server:
  limiter: false    # <— ВАЖНО: полностью отключает limiter
```

**Текущее состояние**: ✅ Уже настроено (строка 90 в `searxng/settings.yml`)

### Шаг 2: Удалить limiter.toml (обязательно!)

```bash
sudo rm -f /opt/telegram-assistant/searxng/limiter.toml
```

**Важно**: Файл принадлежит root (uid 977), поэтому нужен sudo.

### Шаг 3: Проверить docker-compose.yml

В `docker-compose.yml` уже есть:
```yaml
environment:
  - SEARXNG_LIMITER=false
```

Это правильно - переменная окружения переопределяет значение в settings.yml и гарантирует отключение limiter.

### Шаг 4: Перезапустить SearXNG

```bash
docker compose --profile rag up -d searxng
docker logs -f searxng
```

## 🔍 Проверка

### 1. Проверить, что limiter.toml удален:
```bash
ls -la /opt/telegram-assistant/searxng/limiter.toml
# Должно показать: ls: cannot access 'limiter.toml': No such file or directory
```

### 2. Проверить settings.yml:
```bash
grep -n "limiter:" /opt/telegram-assistant/searxng/settings.yml | head -3
# Должно показать: 90:  limiter: false
```

### 3. Проверить логи SearXNG:
```bash
docker compose logs searxng --tail 30 | grep -iE "limiter|botdetection|error|listening"
# Должно показать: "listening" без ошибок про limiter
```

### 4. Тест из контейнера API:
```bash
docker compose exec api python3 -c "
import asyncio
from services.searxng_service import get_searxng_service

async def test():
    service = get_searxng_service()
    try:
        result = await service.search('test', user_id='test', lang='ru')
        print(f'✅ SearXNG работает! Результатов: {len(result.results)}')
        return True
    except Exception as e:
        print(f'❌ Ошибка: {e}')
        return False

asyncio.run(test())
"
```

### 5. Прямой тест из контейнера:
```bash
docker compose exec api sh -c 'curl -s "http://searxng:8080/search?q=test&format=json" -H "User-Agent: TelegramAssistant/3.1" | head -20'
```

## 📋 Итоговая конфигурация

### settings.yml:
```yaml
server:
  limiter: false          # Отключен limiter
  public_instance: false  # Отключен public_instance
```

### docker-compose.yml:
```yaml
environment:
  - SEARXNG_LIMITER=false      # Переопределяет settings.yml
  - SEARXNG_PUBLIC_INSTANCE=false
```

### limiter.toml:
- ❌ **Файл удален** (не нужен при `limiter: false`)

## ⚠️ Важные замечания

1. **Порядок действий критичен**:
   - Сначала установить `limiter: false` в settings.yml ✅ (уже сделано)
   - Потом удалить `limiter.toml` ✅ (выполнить команду выше)
   - Иначе будет crash-loop

2. **Права доступа**:
   - Если `limiter.toml` существует и принадлежит root, SearXNG не может его прочитать
   - Лучшее решение - удалить файл, т.к. он не нужен при `limiter: false`

3. **Переменные окружения**:
   - `SEARXNG_LIMITER=false` переопределяет значение в settings.yml
   - Это гарантирует отключение limiter даже если settings.yml изменится

## 🎯 Ожидаемый результат

После выполнения всех шагов:
- ✅ SearXNG запускается без ошибок
- ✅ Bot detection отключен
- ✅ Rate limiting отключен
- ✅ RAG Service может успешно выполнять запросы к SearXNG

