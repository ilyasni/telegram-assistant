# SearXNG - Финальное решение (применено)

**Дата**: 2025-11-05  
**Статус**: ✅ Применено гарантированное решение

## ✅ Примененное решение

### 1. Минимальная конфигурация settings.yml

**Изменения:**
- ❌ Удален `use_default_settings: true` (может включать bot detection по умолчанию)
- ✅ Явная минимальная конфигурация без секции `botdetection`
- ✅ `limiter: false` и `public_instance: false`
- ✅ Минимальный набор engines (duckduckgo, google)

**Файл:** `searxng/settings.yml`

### 2. Удален limiter.toml

**Действие:** Файл `searxng/limiter.toml` полностью удален

**Причина:** Вызывал ошибки парсинга и конфликтовал с settings.yml

### 3. Docker Compose конфигурация

**Переменные окружения:**
- `SEARXNG_LIMITER=false`
- `SEARXNG_PUBLIC_INSTANCE=false`
- `SEARXNG_BASE_URL=https://searxng.produman.studio/`

**Volumes:**
- `./searxng:/etc/searxng:rw` - монтируется каталог целиком

### 4. API конфигурация

**Изменения:**
- `searxng_url: str = "http://searxng:8080"` - прямой доступ через Docker network
- Убраны заголовки X-Forwarded-For/X-Real-IP из кода

## 📋 Текущая конфигурация

### settings.yml
```yaml
general:
  debug: false
  instance_name: "SearXNG"
  enable_metrics: false

server:
  limiter: false
  public_instance: false
  image_proxy: true
  method: "GET"

# ВАЖНО: НЕ используем botdetection секцию вообще

search:
  safe_search: 0
  autocomplete: ""

engines:
  - name: duckduckgo
    engine: duckduckgo
    disabled: false
  - name: google
    engine: google
    disabled: false
```

### docker-compose.yml
```yaml
environment:
  - SEARXNG_LIMITER=false
  - SEARXNG_PUBLIC_INSTANCE=false
  - SEARXNG_BASE_URL=https://searxng.produman.studio/
volumes:
  - ./searxng:/etc/searxng:rw
```

## 🔍 Проверка

### Тесты
1. Прямой доступ: `http://searxng:8080/search?q=test&format=json`
2. Через SearXNG Service: `await service.search('Python programming', user_id='test', lang='ru')`

### Логи
- Проверяем отсутствие ошибок: `docker compose logs searxng --tail 30`
- Проверяем отсутствие ошибок bot detection: `grep -iE "botdetection|403"`

## 💡 Если проблема сохраняется

### Вариант 1: Кастомный Dockerfile (если текущее решение не работает)
Создать `searxng/Dockerfile` с патчами кода (см. исходное решение)

### Вариант 2: Патчинг на лету
Создать скрипт `scripts/patch_searxng_botdetection.sh` для патчинга кода внутри контейнера

### Вариант 3: Альтернативные решения
- **Whoogle**: `benbusby/whoogle-search`
- **Searx**: `searx/searx` (старая версия без bot detection)

## 📝 Context7 Best Practices

1. ✅ Минимальная конфигурация без `use_default_settings`
2. ✅ Явное отключение limiter и public_instance
3. ✅ Прямой доступ через Docker network (минуя Caddy для внутренних запросов)
4. ✅ Автоматическая настройка прав через `scripts/setup_searxng_permissions.sh`

## 🔗 Ссылки

- [SearXNG Documentation](https://docs.searxng.org/)
- [Context7 SearXNG](https://context7.com/searxng/searxng)
- [n8n-installer Best Practices](https://github.com/kossakovsky/n8n-installer)

