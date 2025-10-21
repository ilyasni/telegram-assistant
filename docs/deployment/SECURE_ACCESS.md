# 🔒 Безопасный доступ к сервисам

## 🌐 Внешний доступ (только для пользователей)

**Доступны только через `https://produman.studio/`:**

- ✅ **Supabase Studio:** `https://produman.studio/supabase/`
- ✅ **Grafana Dashboard:** `https://produman.studio/grafana/`
- ✅ **Health Check:** `https://produman.studio/health`
- ✅ **Root:** `https://produman.studio/`

## 🔒 Внутренний доступ (только для Docker сети)

**Скрыты от внешнего доступа:**

- 🔒 **API Gateway:** `api:8000` (только внутри Docker)
- 🔒 **Neo4j Browser:** `neo4j:7474` (только внутри Docker)
- 🔒 **Qdrant Dashboard:** `qdrant:6333` (только внутри Docker)
- 🔒 **RAG Service:** `rag:8000` (только внутри Docker)

## 🔧 Конфигурация Caddy

```caddyfile
# Внешний доступ
produman.studio {
    handle_path /supabase/* {
        reverse_proxy supabase-studio:3000
    }
    handle_path /grafana/* {
        reverse_proxy grafana:3000
    }
    handle /health {
        respond "OK" 200
    }
    handle / {
        respond "Telegram Assistant API Gateway" 200
    }
}

# Внутренние сервисы (только Docker сеть)
api:8000 {
    reverse_proxy api:8000
}
neo4j:7474 {
    reverse_proxy neo4j:7474
}
qdrant:6333 {
    reverse_proxy qdrant:6333
}
rag:8000 {
    reverse_proxy api:8000
}
```

## 🎯 Преимущества

- ✅ **Безопасность** — внутренние сервисы недоступны извне
- ✅ **Простота** — только нужные сервисы доступны пользователям
- ✅ **Контроль** — четкое разделение внешнего и внутреннего доступа
- ✅ **Производительность** — меньше нагрузки на внешние порты

## 🚀 Статус

- ✅ **Caddy настроен** — только внешние сервисы доступны
- ✅ **Внутренние сервисы скрыты** — недоступны извне
- ⚠️ **SSL сертификат** — не может быть получен (порты не открыты)

## 📋 Что нужно для полной работы

1. **Открыть порты 80/443** на сервере
2. **Настроить файрвол** для доступа к серверу
3. **Дождаться получения** SSL сертификата
4. **Протестировать** внешние сервисы

**После этого будет доступно:**
- `https://produman.studio/supabase/` — Supabase Studio
- `https://produman.studio/grafana/` — Grafana Dashboard

**Внутренние сервисы останутся скрытыми!** 🔒
