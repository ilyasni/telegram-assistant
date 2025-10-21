# 🔄 Альтернативное решение DNS

## Проблема

DNS сервер в локальной сети (192.168.31.127) переопределяет `/etc/hosts`, поэтому поддомены `produman.studio` разрешаются на внешний IP.

## Решение: Локальные домены

Используйте локальные домены `.local` вместо поддоменов:

### 1. Обновите /etc/hosts

```bash
sudo nano /etc/hosts
```

Добавьте:
```
192.168.31.64    supabase.local
192.168.31.64    grafana.local
```

### 2. Проверьте доступность

```bash
# Проверка DNS
nslookup supabase.local
nslookup grafana.local

# Проверка доступности
curl -k https://supabase.local
curl -k https://grafana.local
```

### 3. Доступные сервисы

- **Supabase Studio:** https://supabase.local
- **Grafana Dashboard:** https://grafana.local

## Преимущества

- Локальные домены `.local` не конфликтуют с внешними DNS
- Работают независимо от DNS сервера
- Проще в настройке

## Проверка

После настройки проверьте:

```bash
# DNS разрешение
nslookup supabase.local
nslookup grafana.local

# Должно показывать 192.168.31.64
```
