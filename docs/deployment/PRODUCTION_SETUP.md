# Настройка продакшена для produman.studio

## Текущий статус

✅ **Готово:**
- Caddy настроен для поддоменов
- Все сервисы работают локально
- API доступен через localhost
- Supabase Studio доступен через localhost

## Настройка DNS

### 1. DNS записи для поддоменов

Настройте следующие A-записи в DNS провайдере для домена `produman.studio`:

```
supabase.produman.studio   A    <IP_СЕРВЕРА>
grafana.produman.studio    A    <IP_СЕРВЕРА>
```

### 2. Проверка DNS

После настройки DNS проверьте разрешение:

```bash
nslookup supabase.produman.studio
nslookup grafana.produman.studio
```

## Переключение на продакшн

### 1. Активация продакшн конфигурации

После настройки DNS раскомментируйте продакшн конфигурацию в `caddy/Caddyfile`:

```bash
# Раскомментировать строки 52-60 в caddy/Caddyfile (только Supabase и Grafana)
# Закомментировать строки 11-48 (localhost конфигурация)
```

### 2. Перезапуск сервисов

```bash
docker compose restart caddy
```

### 3. Проверка HTTPS

После настройки DNS и перезапуска Caddy автоматически получит SSL сертификаты:

```bash
curl -s https://api.produman.studio/health
curl -s https://supabase.produman.studio
```

## Доступные сервисы

После настройки DNS будут доступны:

- **Supabase Studio:** https://supabase.produman.studio
- **Grafana Dashboard:** https://grafana.produman.studio

## Внутренние сервисы (без внешнего доступа)

Следующие сервисы доступны только внутри Docker сети:

- **API Gateway** — `api:8000` (только для внутренних сервисов)
- **Neo4j Browser** — `neo4j:7474` (только для внутренних сервисов)
- **Qdrant Dashboard** — `qdrant:6333` (только для внутренних сервисов)
- **RAG Service** — `api:8000` (только для внутренних сервисов)

## Локальная разработка

Пока DNS не настроен, используйте localhost:

- **API Gateway:** http://localhost/api/
- **Supabase Studio:** http://localhost/studio/
- **Health Check:** http://localhost/health

## SSL сертификаты

Caddy автоматически получит SSL сертификаты от Let's Encrypt для всех поддоменов после настройки DNS.

## Troubleshooting

### Проблема: "Error getting validation data"

**Причина:** DNS записи не настроены или не распространились.

**Решение:** 
1. Проверьте DNS записи: `nslookup api.produman.studio`
2. Дождитесь распространения DNS (до 24 часов)
3. Используйте localhost конфигурацию для разработки

### Проблема: "Connection refused"

**Причина:** Сервисы не запущены.

**Решение:**
```bash
docker compose ps
docker compose up -d
```
