# Настройка DNS для локальной сети

## IP адрес сервера
**192.168.31.64** - IP адрес VM в локальной сети Proxmox

## DNS записи для настройки

### Вариант 1: Настройка в /etc/hosts (рекомендуется для тестирования)

Добавьте следующие записи в файл `/etc/hosts` на клиентских машинах:

```
192.168.31.64    supabase.produman.studio
192.168.31.64    grafana.produman.studio
```

### Вариант 2: Настройка DNS сервера (для постоянного использования)

Если у вас есть DNS сервер в локальной сети, добавьте A-записи:

```
supabase.produman.studio   A    192.168.31.64
grafana.produman.studio    A    192.168.31.64
```

## Проверка DNS

После настройки проверьте разрешение:

```bash
nslookup supabase.produman.studio
nslookup grafana.produman.studio
# или
ping supabase.produman.studio
ping grafana.produman.studio
```

## Активация продакшн конфигурации

После настройки DNS активируйте продакшн конфигурацию:

1. Раскомментируйте строки 52-60 в `caddy/Caddyfile`
2. Закомментируйте строки 11-48 (localhost конфигурация)
3. Перезапустите Caddy:

```bash
docker compose restart caddy
```

## Доступные сервисы после настройки

- **Supabase Studio:** https://supabase.produman.studio
- **Grafana Dashboard:** https://grafana.produman.studio

## Внутренние сервисы (без внешнего доступа)

- **API Gateway:** `api:8000` (только для внутренних сервисов)
- **Neo4j Browser:** `neo4j:7474` (только для внутренних сервисов)
- **Qdrant Dashboard:** `qdrant:6333` (только для внутренних сервисов)
- **RAG Service:** `api:8000` (только для внутренних сервисов)
