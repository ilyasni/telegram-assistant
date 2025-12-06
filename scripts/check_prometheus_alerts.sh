#!/bin/bash
# Скрипт проверки алертов Prometheus

echo "=== Проверка алертов Prometheus ==="
echo ""

# Проверка активных алертов
echo "📊 Активные алерты:"
curl -s http://localhost:9090/api/v1/alerts | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    alerts = data.get('data', {}).get('alerts', [])
    print(f'Всего алертов: {len(alerts)}')
    if alerts:
        print('\nАктивные алерты:')
        for a in alerts[:10]:
            labels = a.get('labels', {})
            state = a.get('state', 'unknown')
            alertname = labels.get('alertname', 'unknown')
            severity = labels.get('severity', 'unknown')
            print(f'  - {alertname} ({severity}): {state}')
    else:
        print('Активных алертов нет')
except Exception as e:
    print(f'Ошибка: {e}')
" 2>&1
echo ""

# Проверка правил алертов
echo "📊 Правила алертов:"
curl -s http://localhost:9090/api/v1/rules | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    groups = data.get('data', {}).get('groups', [])
    print(f'Всего групп правил: {len(groups)}')
    total_rules = sum(len(g.get('rules', [])) for g in groups)
    print(f'Всего правил: {total_rules}')
except Exception as e:
    print(f'Ошибка: {e}')
" 2>&1
echo ""

echo "✅ Проверка завершена"
