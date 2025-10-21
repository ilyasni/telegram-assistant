# 🌐 Доступ через produman.studio

## 🔧 Текущая конфигурация

**Caddy настроен для работы с доменом `produman.studio`:**

```caddyfile
produman.studio {
    handle_path /api/* {
        reverse_proxy api:8000
    }
    handle_path /supabase/* {
        reverse_proxy supabase-studio:3000
    }
    handle_path /grafana/* {
        reverse_proxy grafana:3000
    }
    # ... остальные сервисы
}
```

## 🌐 URL для доступа

**После настройки DNS и файрвола:**

- **Supabase Studio:** `https://produman.studio/supabase/`
- **API Gateway:** `https://produman.studio/api/`
- **Grafana Dashboard:** `https://produman.studio/grafana/`
- **Neo4j Browser:** `https://produman.studio/neo4j/`
- **Qdrant Dashboard:** `https://produman.studio/qdrant/`
- **RAG Service:** `https://produman.studio/rag/`

## ⚠️ Текущая проблема

**Caddy не может получить SSL сертификат**, потому что:

1. **Сервер недоступен с внешней стороны** — порты 80/443 не открыты
2. **Файрвол блокирует** доступ к серверу
3. **NAT не настроен** для проброса портов

## 🔧 Что нужно сделать

### 1. Открыть порты на сервере

```bash
# Открыть порты 80 и 443
sudo ufw allow 80
sudo ufw allow 443

# Или через iptables
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
```

### 2. Настроить файрвол

```bash
# Проверить статус файрвола
sudo ufw status

# Если файрвол активен, открыть порты
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
```

### 3. Проверить доступность

```bash
# Проверить, что порты открыты
sudo netstat -tlnp | grep :80
sudo netstat -tlnp | grep :443

# Проверить с внешней стороны
curl -I http://produman.studio
```

## 🚀 После настройки

**Caddy автоматически:**
1. Получит SSL сертификат от Let's Encrypt
2. Настроит HTTPS редирект
3. Все сервисы будут доступны по HTTPS

## 📋 Статус

- ✅ **Caddy настроен** — готов к работе с доменом
- ✅ **DNS настроен** — `produman.studio` → `193.201.88.88`
- ⚠️ **Порты не открыты** — нужно настроить файрвол
- ⚠️ **SSL сертификат** — не может быть получен

## 🎯 Следующие шаги

1. **Открыть порты 80/443** на сервере
2. **Проверить доступность** с внешней стороны
3. **Дождаться получения** SSL сертификата
4. **Протестировать** все сервисы по HTTPS

**После этого все будет работать через `https://produman.studio/`!** 🚀
