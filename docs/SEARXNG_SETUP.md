# Настройка SearXNG для внешнего поиска

**Дата**: 2025-02-02  
**Статус**: ✅ SearXNG интегрирован и настроен

## Обзор

SearXNG интегрирован в систему как внешний поисковый движок для RAG Service. Используется как fallback, когда по внутренним каналам не найдено результатов.

## Конфигурация

### Docker Compose
- **Профиль**: `rag`
- **Порт**: `8080`
- **URL**: `http://searxng:8080`
- **Volume**: `./searxng:/etc/searxng` (rw для создания settings.yml)

### Переменные окружения
```bash
SEARXNG_URL=http://searxng:8080
SEARXNG_ENABLED=true
SEARXNG_CACHE_TTL=3600
SEARXNG_MAX_RESULTS=5
SEARXNG_RATE_LIMIT_PER_USER=10
```

## Запуск

```bash
# Запуск SearXNG с профилем rag
docker compose --profile rag up -d searxng

# Проверка статуса
docker compose ps searxng

# Проверка health
curl http://localhost:8080/healthz

# Тест поиска
curl "http://localhost:8080/search?q=test&format=json"
```

## Интеграция в RAG Service

SearXNG используется в `api/services/rag_service.py`:

1. **Fallback механизм**: Если не найдено результатов в каналах (Qdrant + PostgreSQL FTS + Neo4j)
2. **External search grounding**: Дополнение ответов внешними источниками
3. **Rate limiting**: 10 запросов в минуту на пользователя
4. **Кэширование**: Redis с TTL 3600 секунд

## Безопасность

- ✅ Чёрный список доменов (torrent, adult, gambling, phishing и т.д.)
- ✅ Sanitization URL (удаление tracking параметров)
- ✅ Валидация через Pydantic
- ✅ Rate limiting на пользователя
- ✅ Ограничение категорий: `general`, `news`, `wikipedia`
- ✅ Блокировка движков: `torrent`, `files`, `images`, `videos`, `music`

## Troubleshooting

### Проблема: SearXNG не запускается

**Ошибка**: `Read-only file system` или `/etc/searxng/settings.yml` не создаётся

**Решение**: 
1. Убедитесь, что volume монтируется без `:ro` флага
2. Проверьте права на директорию `./searxng`
3. Пересоздайте контейнер: `docker compose rm -f searxng && docker compose --profile rag up -d searxng`

### Проблема: SearXNG недоступен из API контейнера

**Решение**: 
1. Проверьте, что оба контейнера в одной сети `telegram-network`
2. Проверьте health: `curl http://searxng:8080/healthz` из API контейнера
3. Проверьте переменные окружения: `SEARXNG_URL=http://searxng:8080`

### Проблема: SearXNG возвращает пустые результаты

**Решение**:
1. Проверьте настройки SearXNG в `./searxng/settings.yml`
2. Проверьте доступность поисковых движков
3. Проверьте логи: `docker compose logs searxng`

## Мониторинг

```bash
# Логи SearXNG
docker compose logs -f searxng

# Health check
curl http://localhost:8080/healthz

# Статистика использования в RAG Service
docker compose logs api | grep -i searxng
```

## Best Practices (Context7)

1. ✅ **Кэширование**: Redis для уменьшения нагрузки на SearXNG
2. ✅ **Rate limiting**: Защита от злоупотреблений
3. ✅ **Фильтрация**: Чёрный список доменов и блокировка ненужных движков
4. ✅ **Fallback**: Используется только когда нет результатов в каналах
5. ✅ **Валидация**: Pydantic модели для безопасности данных
6. ✅ **Health checks**: Автоматическая проверка работоспособности

