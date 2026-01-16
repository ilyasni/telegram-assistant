#!/bin/bash
# Скрипт для исправления проблем с загрузкой Grafana

echo "=== Исправление проблем с Grafana ==="
echo ""

# 1. Проверить, что дашборд правильно структурирован
echo "1. Проверка структуры дашборда..."
if jq -e '.title, .uid' grafana/dashboards/system_stability.json > /dev/null 2>&1; then
    echo "   ✅ Дашборд структурирован правильно"
else
    echo "   ❌ Ошибка в структуре дашборда"
    exit 1
fi

# 2. Перезапустить Grafana
echo ""
echo "2. Перезапуск Grafana..."
docker compose restart grafana

# 3. Подождать запуска
echo ""
echo "3. Ожидание запуска Grafana (10 секунд)..."
sleep 10

# 4. Проверить логи
echo ""
echo "4. Проверка логов Grafana (последние 20 строк)..."
docker compose logs grafana --tail=20 | grep -i "system_stability\|error" || echo "   Логи проверены"

# 5. Проверить доступность Grafana
echo ""
echo "5. Проверка доступности Grafana..."
if curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/api/health 2>/dev/null | grep -q "200"; then
    echo "   ✅ Grafana доступна локально"
else
    echo "   ⚠️  Grafana недоступна локально (возможно, не запущена или на другом порту)"
fi

# 6. Проверить через Caddy (если доступен)
echo ""
echo "6. Проверка через Caddy..."
if curl -s -o /dev/null -w "%{http_code}" https://grafana.produman.studio/api/health 2>/dev/null | grep -q "200\|401\|302"; then
    echo "   ✅ Grafana доступна через Caddy"
else
    echo "   ⚠️  Grafana недоступна через Caddy (проверьте настройки reverse proxy)"
fi

echo ""
echo "=== Проверка завершена ==="
echo ""
echo "Следующие шаги:"
echo "1. Откройте https://grafana.produman.studio/"
echo "2. Перейдите в Dashboards → System Stability Monitoring"
echo "3. Если дашборд не виден, проверьте логи: docker compose logs grafana | grep -i error"

