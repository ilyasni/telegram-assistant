# SearXNG Health Check Report

**Дата**: 2025-02-02  
**Версия**: SearXNG 2025.11.3-52ffc4c7f

## ✅ Health Check Results

### 1. Container Status
- **Status**: Up (health: starting → healthy)
- **Health Check**: Docker healthcheck активен
- **Restart Policy**: `unless-stopped` (Context7 best practice)

### 2. Health Endpoint
- **URL**: `http://localhost:8080/healthz`
- **Response**: `OK` (HTTP 200)
- **Accessibility**: ✅ Доступен из host и из API контейнера

### 3. Service Status
- **Listening**: `http://:::8080` ✅
- **Workers**: Started (worker-1, runtime-1) ✅
- **Configuration**: `settings.yml` создан автоматически ✅

### 4. Integration Check
- ✅ **SearXNG Service** инициализирован в API
- ✅ **RAG Service** использует SearXNG как fallback
- ✅ **Network connectivity**: API контейнер может подключиться к SearXNG
- ✅ **Search functionality**: Поиск работает корректно

### 5. Configuration
- **Base URL**: `http://searxng:8080`
- **Enabled**: `True`
- **Cache TTL**: 3600 секунд
- **Max Results**: 5
- **Rate Limit**: 10 запросов/мин на пользователя

## ⚠️ Предупреждения (не критично)

1. **Missing engines**: Некоторые движки не найдены (ahmia, torch, yacy images) - это нормально, SearXNG работает с доступными движками
2. **Missing limiter.toml**: Файл конфигурации rate limiter отсутствует - используется встроенный механизм
3. **X-Forwarded-For header**: Предупреждение при обращении без proxy - не критично для внутреннего использования

## 🔍 Проверка Health

### Ручная проверка
```bash
# Health endpoint
curl http://localhost:8080/healthz

# Из API контейнера
docker compose exec api curl http://searxng:8080/healthz

# Статус контейнера
docker compose ps searxng

# Docker health status
docker compose inspect searxng --format '{{.State.Health.Status}}'
```

### Тест поиска
```bash
# Простой поиск
curl "http://localhost:8080/search?q=test&format=json&engines=duckduckgo"

# Через API контейнер
docker compose exec api curl "http://searxng:8080/search?q=test&format=json"
```

### Программная проверка
```python
from services.searxng_service import get_searxng_service
import asyncio

async def test():
    service = get_searxng_service()
    result = await service.search('test', user_id='test', lang='ru')
    print(f"Results: {len(result.results)}")

asyncio.run(test())
```

## 📊 Мониторинг

### Логи
```bash
# Последние логи
docker compose logs searxng --tail 50

# Логи с фильтром
docker compose logs searxng | grep -iE "error|warning|listening"
```

### Метрики
- **Health check interval**: 30s
- **Health check timeout**: 10s
- **Health check retries**: 3
- **Start period**: 10s

## ✅ Best Practices (Context7)

1. ✅ **Health checks**: Docker healthcheck настроен
2. ✅ **Restart policy**: `unless-stopped` для автоматического перезапуска
3. ✅ **Network isolation**: Контейнер в сети `telegram-network`
4. ✅ **Configuration management**: Volume для `settings.yml` (rw)
5. ✅ **Security**: Rate limiting, domain blacklist, URL sanitization
6. ✅ **Caching**: Redis кэширование для уменьшения нагрузки
7. ✅ **Fallback mechanism**: Используется только когда нет результатов в каналах

## 🎯 Итог

**SearXNG полностью работоспособен:**
- ✅ Health endpoint отвечает
- ✅ Поиск работает
- ✅ Интеграция с RAG Service активна
- ✅ Доступность из API контейнера подтверждена
- ✅ Все health checks проходят

**Система готова к использованию SearXNG для внешнего поиска.**

