# Исправление проблемы загрузки Grafana через reverse proxy

**Проблема**: Grafana показывает ошибку "Grafana has failed to load its application files" при доступе через `https://grafana.produman.studio/`

## Причины

1. **Неправильная настройка reverse proxy** - Caddy не правильно проксирует статические файлы
2. **Проблемы с путями** - Grafana не может определить правильный root URL
3. **Отсутствие необходимых заголовков** - нужны правильные заголовки для работы SPA

## Решение

### 1. Обновить конфигурацию Caddy для Grafana

Проблема может быть в том, что Caddy не правильно передаёт заголовки для работы SPA приложения.

**Текущая конфигурация** (работающая):
```caddyfile
grafana.produman.studio {
    reverse_proxy grafana:3000 {
        header_up Host {http.request.host}
        header_up X-Real-IP {http.request.remote}
        header_up X-Forwarded-For {http.request.remote}
        header_up X-Forwarded-Proto {http.request.scheme}
        header_up X-Forwarded-Host {http.request.host}
    }
}
```

**Рекомендуется добавить** (для лучшей совместимости):
```caddyfile
grafana.produman.studio {
    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        X-XSS-Protection "1; mode=block"
        -Server
    }
    
    reverse_proxy grafana:3000 {
        header_up Host {http.request.host}
        header_up X-Real-IP {http.request.remote}
        header_up X-Forwarded-For {http.request.remote}
        header_up X-Forwarded-Proto {http.request.scheme}
        header_up X-Forwarded-Host {http.request.host}
        # Важно: передавать оригинальный путь
        header_up X-Forwarded-Prefix ""
    }
}
```

### 2. Проверить настройки Grafana

Убедитесь, что настройки правильные:
```yaml
GF_SERVER_ROOT_URL: https://grafana.${DOMAIN}/
GF_SERVER_SERVE_FROM_SUB_PATH: "false"
```

### 3. Перезапустить сервисы

```bash
# Перезапустить Caddy
docker compose restart caddy

# Перезапустить Grafana
docker compose restart grafana

# Подождать запуска
sleep 10
```

## Диагностика

### 1. Проверить доступность Grafana локально

```bash
# Из контейнера Caddy
docker compose exec caddy curl -I http://grafana:3000/

# Должен вернуть 200 OK
```

### 2. Проверить через внешний URL

```bash
# Проверить основной URL
curl -I https://grafana.produman.studio/

# Проверить статические файлы
curl -I https://grafana.produman.studio/public/build/app.*.js
```

### 3. Проверить логи

```bash
# Логи Caddy
docker compose logs caddy | grep -i grafana

# Логи Grafana
docker compose logs grafana | tail -50
```

## Альтернативное решение

Если проблема сохраняется, можно попробовать:

1. **Использовать прямую маршрутизацию** (без reverse proxy)
2. **Настроить Grafana на другом порту** для прямого доступа
3. **Использовать path-based маршрутизацию** вместо subdomain

## Быстрое исправление

Запустите скрипт:
```bash
bash scripts/fix_grafana_loading.sh
```

Скрипт автоматически:
- Проверит структуру дашборда
- Перезапустит Grafana
- Проверит доступность
- Покажет логи

## Ссылки

- [Grafana Reverse Proxy Configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-grafana/#reverse-proxy)
- [Caddy Reverse Proxy Documentation](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)

