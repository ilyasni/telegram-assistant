# 🌐 **Настройка внешнего доступа через produman.studio**

## ❌ **Текущая проблема:**

**`https://produman.studio/supabase/` не доступен** потому что:

1. **DNS настроен** — `produman.studio` → `193.201.88.88` ✅
2. **Caddy работает** — но только на `localhost:8080` ❌
3. **Порты не открыты** — 80/443 не проброшены на хост ❌

## 🔧 **Что нужно сделать:**

### 1. Открыть порты на сервере:

```bash
# Открыть порты 80 и 443
sudo ufw allow 80
sudo ufw allow 443

# Или через iptables
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
```

### 2. Настроить Caddy для работы с доменом:

```caddyfile
# В caddy/Caddyfile изменить:
:8080 {
    # ... текущая конфигурация
}

# На:
produman.studio {
    # ... та же конфигурация
}
```

### 3. Изменить порты в docker-compose.yml:

```yaml
# В docker-compose.yml изменить:
ports:
  - "8080:80"
  - "8443:443"

# На:
ports:
  - "80:80"
  - "443:443"
```

### 4. Перезапустить Caddy:

```bash
docker compose restart caddy
```

## 🧪 **Проверка:**

```bash
# Проверить, что порты открыты
sudo netstat -tlnp | grep -E ":80|:443"

# Проверить доступность с внешней стороны
curl -I http://produman.studio
curl -I https://produman.studio
```

## 🎯 **Ожидаемый результат:**

- ✅ **HTTP:** `http://produman.studio/supabase/` → Supabase Studio
- ✅ **HTTPS:** `https://produman.studio/supabase/` → Supabase Studio (с SSL)
- ✅ **API:** `https://produman.studio/api/` → API Gateway
- ✅ **Grafana:** `https://produman.studio/grafana/` → Grafana Dashboard

## ⚠️ **Важно:**

1. **Порты 80/443** должны быть открыты на сервере
2. **Файрвол** не должен блокировать доступ
3. **NAT** должен пробрасывать порты на сервер
4. **DNS** должен резолвиться на правильный IP

## 🚀 **После настройки:**

**Caddy автоматически:**
1. Получит SSL сертификат от Let's Encrypt
2. Настроит HTTPS редирект
3. Все сервисы будут доступны по HTTPS

**Система будет полностью доступна через `https://produman.studio/`!** 🎉
