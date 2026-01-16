# Исправление проблем с Grafana

**Дата**: 2025-12-03

## Проблема

1. **Дашборды не загружаются** - ошибка "Dashboard title cannot be empty"
2. **Grafana не загружает файлы приложения** - ошибка на странице `/dashboards`

## Решение

### 1. Исправлена структура дашборда

**Проблема**: Дашборд был создан с обёрткой `"dashboard": {...}`, но Grafana provisioning ожидает плоскую структуру.

**Исправление**: Убрана обёртка, теперь структура плоская как в других дашбордах.

```bash
# Дашборд исправлен
jq 'if has("dashboard") then .dashboard else . end' grafana/dashboards/system_stability.json > grafana/dashboards/system_stability.json
```

### 2. Проблема с загрузкой Grafana приложения

**Причина**: Ошибка "Grafana has failed to load its application files" обычно связана с:
- Неправильной настройкой `GF_SERVER_ROOT_URL`
- Проблемами с reverse proxy (Caddy)
- Неправильными путями для статических файлов

**Решение**: 

1. **Проверить настройки Grafana:**
   - `GF_SERVER_ROOT_URL: https://grafana.${DOMAIN}/`
   - `GF_SERVER_SERVE_FROM_SUB_PATH: "false"` (правильно для subdomain)

2. **Проверить настройки Caddy:**
   ```caddyfile
   grafana.produman.studio {
       reverse_proxy grafana:3000 {
           header_up Host {http.request.host}
           header_up X-Forwarded-Host {http.request.host}
           header_up X-Forwarded-Proto {http.request.scheme}
       }
   }
   ```

3. **Перезапустить Grafana:**
   ```bash
   docker compose restart grafana
   ```

## Проверка

### 1. Проверить дашборд

```bash
# Проверить структуру
jq '.title, .uid' grafana/dashboards/system_stability.json

# Проверить логи Grafana
docker compose logs grafana | grep -i "system_stability"
```

### 2. Проверить доступность Grafana

```bash
# Проверить через curl
curl -I https://grafana.produman.studio/

# Проверить статические файлы
curl -I https://grafana.produman.studio/public/build/app.*.js
```

### 3. Проверить дашборды в Grafana

После перезапуска Grafana дашборд должен появиться автоматически через provisioning.

**URL дашборда:**
```
https://grafana.produman.studio/d/system-stability
```

## Дополнительная диагностика

### Проверить логи Grafana

```bash
docker compose logs grafana --tail=100 | grep -i "error\|dashboard\|provisioning"
```

### Проверить, что дашборд загружен

```bash
# Через Grafana API (если доступен)
curl -u admin:${GRAFANA_PASSWORD} \
  https://grafana.produman.studio/api/dashboards/uid/system-stability
```

### Если Grafana всё ещё не загружается

1. **Проверить сеть Docker:**
   ```bash
   docker network inspect telegram-assistant_telegram-network | grep grafana
   ```

2. **Проверить доступность Grafana из контейнера:**
   ```bash
   docker compose exec caddy curl -I http://grafana:3000/
   ```

3. **Проверить настройки Caddy:**
   ```bash
   docker compose logs caddy | grep -i grafana
   ```

## Статус

- ✅ Структура дашборда исправлена
- ⏳ Ожидается перезапуск Grafana для загрузки исправленного дашборда
- ⏳ Требуется проверка загрузки Grafana приложения

