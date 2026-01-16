# Требования к серверу для продакшена

## Context

Документ описывает минимальные и рекомендуемые характеристики сервера для развертывания Telegram Assistant на продакшене. Основано на анализе `docker-compose.yml` и конфигурации всех сервисов.

## Минимальные требования

### Процессор (CPU)
- **Минимум**: 4 ядра
- **Рекомендуется**: 8 ядер
- **Обоснование**:
  - `tgstat-parser`: 2 CPU (reservation 1)
  - `crawl4ai`: 1 CPU
  - Остальные сервисы: ~1-2 CPU суммарно
  - Резерв для системы: 1 CPU

### Оперативная память (RAM)
- **Минимум**: 16 GB
- **Рекомендуется**: 32 GB
- **Распределение по сервисам**:

| Сервис | RAM (минимум) | RAM (рекомендуется) |
|--------|---------------|---------------------|
| `tgstat-parser` | 2 GB | 3 GB |
| `crawl4ai` | 2 GB | 2 GB |
| `neo4j` | 2.5 GB (heap 2GB + pagecache 512MB) | 4 GB (heap 3GB + pagecache 1GB) |
| `supabase-db` (PostgreSQL) | 2 GB | 4 GB |
| `qdrant` | 1 GB | 2 GB |
| `redis` | 512 MB | 1 GB |
| `api` | 512 MB | 1 GB |
| `worker` | 512 MB | 1 GB |
| `telethon-ingest` | 512 MB | 1 GB |
| `gpt2giga-proxy` | 512 MB | 1 GB |
| `searxng` | 512 MB | 1 GB |
| Supabase компоненты (Kong, PostgREST, Studio, Meta) | 1 GB | 2 GB |
| `caddy` | 128 MB | 256 MB |
| `prometheus` | 512 MB | 1 GB |
| `grafana` | 512 MB | 1 GB |
| `alertmanager` | 128 MB | 256 MB |
| `neo4j-health` | 128 MB | 256 MB |
| **Система + Docker overhead** | 2 GB | 4 GB |
| **Резерв для пиковых нагрузок** | 1 GB | 2 GB |
| **ИТОГО** | **~16 GB** | **~32 GB** |

### Дисковое пространство
- **Минимум**: 100 GB SSD
- **Рекомендуется**: 200-500 GB SSD (зависит от объема данных)
- **Распределение**:
  - PostgreSQL данные: 20-50 GB (зависит от объема постов)
  - Neo4j данные: 10-30 GB (зависит от размера графа)
  - Qdrant векторы: 10-50 GB (зависит от количества индексированных постов)
  - Redis данные: 1-5 GB
  - Docker volumes: 10-20 GB
  - Логи (Prometheus retention 200h): 5-10 GB
  - Grafana данные: 1-2 GB
  - Telegram сессии (telethon-ingest): 100-500 MB
  - Резерв для роста: 20-50 GB

### Сеть
- **Пропускная способность**: минимум 100 Mbps, рекомендуется 1 Gbps
- **Порты**:
  - 80, 443 (HTTP/HTTPS через Caddy)
  - 9090 (Prometheus, опционально)
  - 9093 (AlertManager, опционально)
  - 8080 (SearXNG, опционально)
  - 8020 (TGStat Parser, опционально)
  - 8011 (Telethon Ingest health, опционально)

## Рекомендуемые характеристики

### Для средних нагрузок (до 1000 каналов, ~10K постов/день)
- **CPU**: 8 ядер
- **RAM**: 32 GB
- **Диск**: 200 GB SSD
- **Сеть**: 1 Gbps

### Для высоких нагрузок (1000+ каналов, 50K+ постов/день)
- **CPU**: 16 ядер
- **RAM**: 64 GB
- **Диск**: 500 GB SSD (или больше, в зависимости от retention политик)
- **Сеть**: 1 Gbps

## Операционная система

- **Рекомендуется**: Ubuntu 22.04 LTS или Debian 12
- **Требования**:
  - Docker Engine 24.0+
  - Docker Compose 2.20+
  - Python 3.11+ (для скриптов)
  - Доступ к интернету для Let's Encrypt (SSL сертификаты)

## Дополнительные требования

### Для внешнего доступа
- **DNS**: Настроенные A-записи для домена
- **Firewall**: Открыты порты 80, 443
- **Email**: Для Let's Encrypt сертификатов (CADDY_TLS_EMAIL)

### Для мониторинга
- **Prometheus**: Retention 200 часов (настраивается)
- **Grafana**: Доступ через поддомен (опционально)
- **AlertManager**: Настроенные каналы уведомлений (Telegram)

### Для хранения данных
- **S3-совместимое хранилище**: Cloud.ru S3 (для Vision summaries, медиа)
- **Backup**: Рекомендуется настроить автоматические бэкапы PostgreSQL, Neo4j, Qdrant

## Масштабирование

### Горизонтальное масштабирование
- **Worker**: Можно запустить несколько инстансов для параллельной обработки
- **API**: Можно запустить несколько инстансов за load balancer

### Вертикальное масштабирование
- Увеличить лимиты памяти для конкретных сервисов в `docker-compose.yml`:
  ```yaml
  deploy:
    resources:
      limits:
        memory: 4G  # Увеличить для neo4j, postgres
        cpus: '2.0'
  ```

## Проверка готовности сервера

### Проверка ресурсов
```bash
# CPU
nproc

# RAM
free -h

# Диск
df -h

# Docker
docker --version
docker compose version
```

### Проверка производительности
```bash
# Тест записи на диск
dd if=/dev/zero of=/tmp/test bs=1M count=1024 conv=fdatasync

# Тест сети
curl -o /dev/null -s -w "%{speed_download}\n" https://speed.cloudflare.com/__down?bytes=100000000
```

## Оптимизация для продакшена

### Настройка PostgreSQL
```sql
-- Увеличить shared_buffers (в postgresql.conf или через переменные окружения)
shared_buffers = 2GB  # Для 16GB RAM
effective_cache_size = 6GB
maintenance_work_mem = 512MB
```

### Настройка Redis
```bash
# В docker-compose.yml можно добавить:
redis:
  command: redis-server --maxmemory 1gb --maxmemory-policy allkeys-lru
```

### Настройка Neo4j
```yaml
# В docker-compose.yml уже настроено:
NEO4J_server_memory_heap_max__size: 2G
NEO4J_server_memory_pagecache_size: 512m
# Для 32GB RAM можно увеличить до 3G heap + 1G pagecache
```

## Мониторинг ресурсов

### Prometheus метрики
- `container_memory_usage_bytes` - использование памяти контейнерами
- `container_cpu_usage_seconds_total` - использование CPU
- `container_fs_usage_bytes` - использование диска

### Grafana Dashboard
Импортировать `grafana/dashboards/system-overview.json` для мониторинга ресурсов.

## Troubleshooting

### Нехватка памяти
```bash
# Проверить использование памяти контейнерами
docker stats

# Проверить использование памяти системой
free -h
```

### Нехватка диска
```bash
# Проверить использование диска
df -h

# Проверить размер Docker volumes
docker system df -v
```

### Высокая нагрузка на CPU
```bash
# Проверить нагрузку на CPU
top
htop

# Проверить использование CPU контейнерами
docker stats
```

## Рекомендации по выбору провайдера

### VPS провайдеры
- **Hetzner**: Хорошее соотношение цена/качество, SSD диски
- **DigitalOcean**: Простота настройки, хорошая документация
- **OVH**: Низкие цены, хорошая производительность
- **Yandex Cloud**: Низкая латентность для российских пользователей

### Dedicated серверы
- Для высоких нагрузок рекомендуется dedicated сервер
- Лучше контроль над ресурсами
- Возможность настройки RAID для надежности

## Итоговые рекомендации

### Минимальная конфигурация (для тестирования/разработки)
- **CPU**: 4 ядра
- **RAM**: 16 GB
- **Диск**: 100 GB SSD
- **Стоимость**: ~$40-60/месяц

### Рекомендуемая конфигурация (для продакшена)
- **CPU**: 8 ядер
- **RAM**: 32 GB
- **Диск**: 200 GB SSD
- **Стоимость**: ~$80-120/месяц

### Высоконагруженная конфигурация
- **CPU**: 16 ядер
- **RAM**: 64 GB
- **Диск**: 500 GB SSD
- **Стоимость**: ~$200-300/месяц

## Дополнительные ресурсы

- [Docker Compose документация](https://docs.docker.com/compose/)
- [PostgreSQL настройка производительности](https://www.postgresql.org/docs/current/performance-tips.html)
- [Neo4j настройка памяти](https://neo4j.com/docs/operations-manual/current/performance/memory-configuration/)
- [Qdrant настройка](https://qdrant.tech/documentation/guides/configuration/)
