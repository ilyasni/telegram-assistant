#!/bin/bash
# Скрипт для удаления provisioned дашбордов Grafana
# Usage: ./scripts/remove_grafana_dashboard.sh <dashboard_name>
# Example: ./scripts/remove_grafana_dashboard.sh system_overview

set -e

DASHBOARDS_DIR="/opt/telegram-assistant/grafana/dashboards"
ARCHIVE_DIR="${DASHBOARDS_DIR}/_removed"

# Создаем папку для архива, если её нет
mkdir -p "${ARCHIVE_DIR}"

if [ $# -eq 0 ]; then
    echo "Usage: $0 <dashboard_name>"
    echo ""
    echo "Available dashboards:"
    ls -1 "${DASHBOARDS_DIR}"/*.json 2>/dev/null | xargs -n1 basename | grep -v "\.bak$" | sed 's/\.json$//' | sort
    exit 1
fi

DASHBOARD_NAME="$1"
DASHBOARD_FILE="${DASHBOARDS_DIR}/${DASHBOARD_NAME}.json"

if [ ! -f "${DASHBOARD_FILE}" ]; then
    echo "Error: Dashboard file not found: ${DASHBOARD_FILE}"
    exit 1
fi

# Перемещаем в архив с timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ARCHIVE_FILE="${ARCHIVE_DIR}/${DASHBOARD_NAME}_${TIMESTAMP}.json"

echo "Moving dashboard to archive..."
mv "${DASHBOARD_FILE}" "${ARCHIVE_FILE}"

# Также удаляем .bak файлы, если есть
if [ -f "${DASHBOARDS_DIR}/${DASHBOARD_NAME}.json.bak" ]; then
    mv "${DASHBOARDS_DIR}/${DASHBOARD_NAME}.json.bak" "${ARCHIVE_DIR}/${DASHBOARD_NAME}_${TIMESTAMP}.json.bak"
fi

echo "✓ Dashboard '${DASHBOARD_NAME}' moved to archive: ${ARCHIVE_FILE}"
echo ""
echo "To apply changes, restart Grafana:"
echo "  docker compose restart grafana"
echo ""
echo "To restore the dashboard, move it back:"
echo "  mv '${ARCHIVE_FILE}' '${DASHBOARD_FILE}'"
echo "  docker compose restart grafana"

