# SearXNG - Решение через патчинг кода

**Дата**: 2025-11-05  
**Статус**: ✅ Применено патчинг на лету

## ✅ Примененное решение

### 1. Патчинг на лету (рекомендуется)

**Скрипт:** `scripts/patch_searxng_botdetection.sh`

**Действие:**
- Патчит файл `/usr/local/searxng/searx/__init__.py` внутри контейнера
- Комментирует все вызовы `botdetection`
- Перезапускает контейнер

**Использование:**
```bash
./scripts/patch_searxng_botdetection.sh
```

### 2. Кастомный Dockerfile (альтернатива)

**Файл:** `searxng/Dockerfile`

**Действие:**
- Создает кастомный образ с патчами на уровне кода
- Отключает bot detection на этапе сборки

**Использование:**
1. Раскомментируйте в `docker-compose.yml`:
```yaml
build:
  context: ./searxng
  dockerfile: Dockerfile
```

2. Пересоберите образ:
```bash
docker compose build searxng
docker compose up -d searxng
```

### 3. Минимальная конфигурация settings.yml

**Изменения:**
- ❌ Удален `use_default_settings: true`
- ✅ Явная минимальная конфигурация
- ✅ Нет секции `botdetection`

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

# ВАЖНО: НЕ используем botdetection секцию

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
```

## 🔍 Проверка

### Тесты
1. Прямой доступ: `http://searxng:8080/search?q=test&format=json`
2. Через SearXNG Service: `await service.search('Python programming', user_id='test', lang='ru')`

### Логи
- Проверяем отсутствие ошибок bot detection: `docker compose logs searxng --tail 20 | grep -i botdetection`

## 💡 Откат патча

Если патч вызвал проблемы:
```bash
docker exec searxng cp /usr/local/searxng/searx/__init__.py.backup /usr/local/searxng/searx/__init__.py
docker restart searxng
```

## 🔗 Ссылки

- [SearXNG Documentation](https://docs.searxng.org/)
- [Context7 SearXNG](https://context7.com/searxng/searxng)

