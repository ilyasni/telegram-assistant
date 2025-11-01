#!/bin/bash
# Скрипт для проверки DNS доступности s3.cloud.ru

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 ДИАГНОСТИКА DNS: s3.cloud.ru"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo ""
echo "📋 1. Проверка DNS с хоста:"
if command -v host &> /dev/null; then
    host s3.cloud.ru || echo "⚠️  host: домен не найден"
elif command -v nslookup &> /dev/null; then
    nslookup s3.cloud.ru || echo "⚠️  nslookup: домен не найден"
else
    echo "⚠️  host/nslookup не установлены"
fi

echo ""
echo "📋 2. Проверка доступности HTTPS:"
if command -v curl &> /dev/null; then
    timeout 5 curl -I https://s3.cloud.ru 2>&1 | head -3 || echo "⚠️  curl: недоступно"
else
    echo "⚠️  curl не установлен"
fi

echo ""
echo "📋 3. Проверка DNS из контейнера worker:"
docker compose exec -T worker python3 -c "
import socket
try:
    ip = socket.gethostbyname('s3.cloud.ru')
    print(f'   ✅ IP адрес: {ip}')
except socket.gaierror as e:
    print(f'   ❌ DNS ошибка: {e}')
    print(f'   💡 Решение: настроить DNS в docker-compose.yml')
"

echo ""
echo "📋 4. Проверка /etc/resolv.conf в контейнере:"
docker compose exec -T worker cat /etc/resolv.conf 2>&1 | head -5

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "💡 РЕШЕНИЕ: Если DNS не работает, добавьте в docker-compose.yml:"
echo "   dns:"
echo "     - 8.8.8.8"
echo "     - 1.1.1.1"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
