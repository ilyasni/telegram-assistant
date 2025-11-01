#!/bin/bash
# Скрипт для обновления provisioned дашбордов Grafana
# Usage: ./scripts/update_grafana_dashboard.sh <dashboard_name> [--no-restart]
# Example: ./scripts/update_grafana_dashboard.sh system_overview

set -e

DASHBOARDS_DIR="/opt/telegram-assistant/grafana/dashboards"
NO_RESTART=false

# Парсинг аргументов
while [[ $# -gt 0 ]]; do
    case $1 in
        --no-restart)
            NO_RESTART=true
            shift
            ;;
        *)
            if [ -z "$DASHBOARD_NAME" ]; then
                DASHBOARD_NAME="$1"
            fi
            shift
            ;;
    esac
done

if [ -z "$DASHBOARD_NAME" ]; then
    echo "Usage: $0 <dashboard_name> [--no-restart]"
    echo ""
    echo "Options:"
    echo "  --no-restart    Don't restart Grafana (wait for auto-refresh 10-30s)"
    echo ""
    echo "Available dashboards:"
    ls -1 "${DASHBOARDS_DIR}"/*.json 2>/dev/null | xargs -n1 basename | grep -v "\.bak$" | sed 's/\.json$//' | sort
    exit 1
fi

DASHBOARD_FILE="${DASHBOARDS_DIR}/${DASHBOARD_NAME}.json"

if [ ! -f "${DASHBOARD_FILE}" ]; then
    echo "Error: Dashboard file not found: ${DASHBOARD_FILE}"
    exit 1
fi

echo "📊 Updating provisioned dashboard: ${DASHBOARD_NAME}"
echo "   File: ${DASHBOARD_FILE}"
echo ""

# Проверяем, запущен ли Grafana
if ! docker compose ps grafana 2>/dev/null | grep -q "Up"; then
    echo "⚠️  Warning: Grafana container is not running"
    echo "   Dashboard will be updated when Grafana starts"
    exit 0
fi

if [ "$NO_RESTART" = true ]; then
    echo "⏳ Waiting for Grafana auto-refresh (10-30 seconds)..."
    echo "   Grafana automatically scans provisioned dashboards every 10-30 seconds"
    echo "   Changes will be applied automatically"
else
    echo "🔄 Restarting Grafana to apply changes..."
    if docker compose restart grafana > /dev/null 2>&1; then
        echo "✅ Grafana restarted successfully"
        echo "   Dashboard should be updated now"
        
        # Ждем немного и проверяем статус
        sleep 2
        if docker compose ps grafana 2>/dev/null | grep -q "Up"; then
            echo "✅ Grafana is running"
        else
            echo "⚠️  Warning: Grafana container may not be fully started yet"
        fi
    else
        echo "❌ Failed to restart Grafana"
        echo "   Trying with sudo..."
        if sudo docker compose restart grafana > /dev/null 2>&1; then
            echo "✅ Grafana restarted successfully (with sudo)"
        else
            echo "❌ Failed to restart Grafana even with sudo"
            echo "   You can manually restart: docker compose restart grafana"
            exit 1
        fi
    fi
fi

echo ""
echo "💡 Note: Changes made in Grafana UI will be overwritten by the file"
echo "   when Grafana rescans the provisioned dashboards."

