# 🔧 Решение проблемы с DNS

## Проблема

DNS сервер в локальной сети (192.168.31.127) имеет приоритет над `/etc/hosts`, поэтому поддомены разрешаются на внешний IP `193.201.88.88` вместо локального `192.168.31.64`.

## Решения

### Решение 1: Настройка DNS сервера (рекомендуется)

Если у вас есть доступ к DNS серверу в локальной сети, добавьте A-записи:

```
supabase.produman.studio   A    192.168.31.64
grafana.produman.studio    A    192.168.31.64
```

### Решение 2: Временное отключение DNS сервера

Временно измените DNS настройки в `/etc/resolv.conf`:

```bash
sudo nano /etc/resolv.conf
# Закомментируйте или удалите строку с nameserver 192.168.31.127
# Добавьте:
nameserver 8.8.8.8
nameserver 1.1.1.1
```

### Решение 3: Использование локальных записей с принудительным разрешением

```bash
# Проверка с принудительным разрешением
curl -k --resolve supabase.produman.studio:443:192.168.31.64 https://supabase.produman.studio
curl -k --resolve grafana.produman.studio:443:192.168.31.64 https://grafana.produman.studio
```

### Решение 4: Настройка nsswitch.conf

```bash
sudo nano /etc/nsswitch.conf
# Убедитесь, что hosts: files dns (files должен быть первым)
```

## Проверка

После применения решения проверьте:

```bash
# Проверка DNS разрешения
nslookup supabase.produman.studio
nslookup grafana.produman.studio

# Должно показывать 192.168.31.64, а не 193.201.88.88
```

## Альтернативное решение: Использование других доменов

Если проблема persists, можно использовать другие домены:

```
192.168.31.64    supabase.local
192.168.31.64    grafana.local
```

И обновить Caddyfile соответственно.
