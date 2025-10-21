# 🎯 Простая настройка с единым доменом

## ✅ Конфигурация

Переключились на **path-based маршрутизацию** под одним доменом `produman.studio`:

### 🏗️ Архитектура

**Единый домен:** `produman.studio`

- **API Gateway:** `https://produman.studio/api/`
- **Supabase Studio:** `https://produman.studio/supabase/`
- **Grafana Dashboard:** `https://produman.studio/grafana/`
- **Neo4j Browser:** `https://produman.studio/neo4j/`
- **Qdrant Dashboard:** `https://produman.studio/qdrant/`
- **RAG Service:** `https://produman.studio/rag/`
- **Health Check:** `https://produman.studio/health`
- **Root:** `https://produman.studio/`

### 🔧 Caddyfile

```caddyfile
# Единый домен с path-based маршрутизацией
produman.studio {
    # API Gateway
    handle_path /api/* {
        reverse_proxy api:8000
    }

    # Supabase Studio
    handle_path /supabase/* {
        reverse_proxy supabase-studio:3000
    }

    # Grafana Dashboard
    handle_path /grafana/* {
        reverse_proxy grafana:3000
    }

    # Neo4j Browser
    handle_path /neo4j/* {
        reverse_proxy neo4j:7474
    }

    # Qdrant Dashboard
    handle_path /qdrant/* {
        reverse_proxy qdrant:6333
    }

    # RAG Service
    handle_path /rag/* {
        reverse_proxy api:8000
    }

    # Health check
    handle /health {
        respond "OK" 200
    }

    # Root endpoint
    handle / {
        respond "Telegram Assistant API Gateway" 200
    }
}
```

### 🎯 Преимущества

- ✅ **Один домен** — не нужны поддомены
- ✅ **Автоматический TLS** — Caddy сам получает сертификат
- ✅ **Простая DNS** — только одна A-запись
- ✅ **WebSockets/SSE** — работают из коробки
- ✅ **Production-ready** — как в прошлом проекте

### 📋 DNS записи

Нужна только **одна A-запись**:

```
produman.studio → 192.168.31.64
```

**Никаких поддоменов не нужно!**

### 🧪 Тестирование

```bash
# Проверка всех endpoints
curl -k https://produman.studio/health
curl -k https://produman.studio/
curl -k https://produman.studio/api/
curl -k https://produman.studio/supabase/
curl -k https://produman.studio/grafana/
curl -k https://produman.studio/neo4j/
curl -k https://produman.studio/qdrant/
curl -k https://produman.studio/rag/
```

### 🚀 Статус

Caddy запущен и пытается получить SSL сертификат для `produman.studio`. После настройки DNS все сервисы будут доступны по HTTPS под одним доменом.

### 🔧 Настройка DNS

Если DNS еще не настроен, добавьте в `/etc/hosts`:

```bash
sudo nano /etc/hosts
# Добавить:
192.168.31.64    produman.studio
```

### 🎉 Готово!

Это самое простое решение — один домен, один сертификат, все сервисы доступны по путям. Никаких поддоменов не нужно!
