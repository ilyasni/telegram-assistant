#!/bin/bash

# Скрипт для настройки DNS записей в локальной сети
# Запускать с правами root: sudo ./setup-dns.sh

echo "Настройка DNS записей для produman.studio..."

# Добавляем записи в /etc/hosts
echo "192.168.31.64    supabase.produman.studio" >> /etc/hosts
echo "192.168.31.64    grafana.produman.studio" >> /etc/hosts

echo "DNS записи добавлены в /etc/hosts"
echo ""
echo "Проверка DNS:"
nslookup supabase.produman.studio
nslookup grafana.produman.studio

echo ""
echo "Теперь можно активировать продакшн конфигурацию:"
echo "1. Раскомментируйте строки 52-60 в caddy/Caddyfile"
echo "2. Закомментируйте строки 11-48 (localhost конфигурация)"
echo "3. Перезапустите Caddy: docker compose restart caddy"
