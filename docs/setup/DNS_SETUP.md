# DNS Setup для produman.studio

## Требуемые DNS записи

Настройте следующие A-записи в DNS провайдере для домена `produman.studio`:

```
supabase.produman.studio   A    <IP_СЕРВЕРА>
grafana.produman.studio    A    <IP_СЕРВЕРА>
```

## Проверка DNS

После настройки DNS проверьте разрешение:

```bash
nslookup supabase.produman.studio
nslookup grafana.produman.studio
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

## SSL сертификаты

Caddy автоматически получит SSL сертификаты от Let's Encrypt для всех поддоменов.
