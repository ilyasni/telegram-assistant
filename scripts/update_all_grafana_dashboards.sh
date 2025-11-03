#!/bin/bash
# Context7: Скрипт для обновления всех дашбордов Grafana
# Обновляет дашборды, исправляет datasource и запускает проверку

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DASHBOARDS_DIR="$PROJECT_ROOT/grafana/dashboards"

echo "📊 Обновление всех Grafana дашбордов"
echo "========================================"

# 1. Исправление дашбордов (datasource, uid)
echo ""
echo "1️⃣ Исправление дашбордов (datasource, uid)..."
if [ -f "$PROJECT_ROOT/grafana/fix_dashboards.py" ]; then
    python3 "$PROJECT_ROOT/grafana/fix_dashboards.py"
    echo "  ✅ Дашборды исправлены"
else
    echo "  ⚠️  fix_dashboards.py не найден, пропускаем"
fi

# 2. Добавление uid к дашбордам без uid
echo ""
echo "2️⃣ Проверка uid дашбордов..."
for dashboard_file in "$DASHBOARDS_DIR"/*.json; do
    if [ ! -f "$dashboard_file" ]; then
        continue
    fi
    
    dashboard_name=$(basename "$dashboard_file" .json)
    
    # Проверяем наличие uid в dashboard
    if ! grep -q '"uid"' "$dashboard_file"; then
        echo "  ⚠️  Дашборд $dashboard_name не имеет uid, добавляем..."
        
        # Генерируем uid на основе имени файла (kebab-case)
        uid=$(echo "$dashboard_name" | tr '_' '-' | tr '[:upper:]' '[:lower:]')
        
        # Используем jq для добавления uid, если доступен
        if command -v jq &> /dev/null; then
            jq ".dashboard.uid = \"$uid\"" "$dashboard_file" > "${dashboard_file}.tmp" && mv "${dashboard_file}.tmp" "$dashboard_file"
            echo "    ✅ Добавлен uid: $uid"
        else
            echo "    ⚠️  jq не установлен, требуется ручное добавление uid"
        fi
    fi
done

# 3. Добавление datasource к панелям без datasource
echo ""
echo "3️⃣ Проверка datasource в панелях..."
for dashboard_file in "$DASHBOARDS_DIR"/*.json; do
    if [ ! -f "$dashboard_file" ]; then
        continue
    fi
    
    dashboard_name=$(basename "$dashboard_file" .json)
    
    # Проверяем наличие datasource в панелях
    if ! grep -q '"datasource"' "$dashboard_file"; then
        echo "  ⚠️  Дашборд $dashboard_name имеет панели без datasource..."
        
        # Используем jq для добавления datasource
        if command -v jq &> /dev/null; then
            jq '(.dashboard.panels[] | select(.targets != null) | .targets[] | select(.datasource == null)) |= {"type": "prometheus", "uid": "prometheus"} | (.dashboard.panels[] | select(.targets == null or (.targets | length) == 0) | .datasource) |= {"type": "prometheus", "uid": "prometheus"}' "$dashboard_file" > "${dashboard_file}.tmp" && mv "${dashboard_file}.tmp" "$dashboard_file"
            echo "    ✅ Добавлен datasource для панелей"
        else
            echo "    ⚠️  jq не установлен, требуется ручное добавление datasource"
        fi
    fi
done

# 4. Обновление дашбордов через скрипт обновления
echo ""
echo "4️⃣ Обновление дашбордов в Grafana..."
for dashboard_file in "$DASHBOARDS_DIR"/*.json; do
    if [ ! -f "$dashboard_file" ]; then
        continue
    fi
    
    dashboard_name=$(basename "$dashboard_file" .json)
    
    echo "  📊 Обновление $dashboard_name..."
    if [ -f "$SCRIPT_DIR/update_grafana_dashboard.sh" ]; then
        bash "$SCRIPT_DIR/update_grafana_dashboard.sh" "$dashboard_name" --no-restart 2>&1 | grep -E "^📊|^⏳|^✅|^❌|^⚠️" || true
    else
        echo "    ⚠️  update_grafana_dashboard.sh не найден"
    fi
done

echo ""
echo "✅ Обновление всех дашбордов завершено"
echo ""
echo "💡 Grafana автоматически обновит дашборды в течение 10-30 секунд"
echo "💡 Проверьте дашборды в Grafana UI: https://grafana.produman.studio"

