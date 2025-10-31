#!/bin/bash
# Import Grafana dashboards (Context7 best practices)

set -e

GRAFANA_URL="http://localhost:3000"
GRAFANA_USER="admin"
GRAFANA_PASSWORD="${GRAFANA_PASSWORD:-admin}"

# Wait for Grafana to be ready
echo "Waiting for Grafana to be ready..."
until curl -s -f "${GRAFANA_URL}/api/health" > /dev/null; do
  echo "Waiting for Grafana..."
  sleep 2
done

# Get API key
echo "Getting Grafana API key..."
API_KEY=$(curl -s -X POST \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"telegram-assistant\",\"role\":\"Admin\"}" \
  "${GRAFANA_URL}/api/auth/keys" \
  -u "${GRAFANA_USER}:${GRAFANA_PASSWORD}" | jq -r '.key')

if [ "$API_KEY" = "null" ] || [ -z "$API_KEY" ]; then
  echo "Failed to get API key. Using basic auth."
  AUTH_HEADER="Authorization: Basic $(echo -n ${GRAFANA_USER}:${GRAFANA_PASSWORD} | base64)"
else
  AUTH_HEADER="Authorization: Bearer ${API_KEY}"
fi

# Import dashboards
echo "Importing Vision & S3 Dashboard..."
curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "$AUTH_HEADER" \
  -d @grafana/dashboards/vision-s3-dashboard.json \
  "${GRAFANA_URL}/api/dashboards/db"

echo "Importing Storage Quota Dashboard..."
curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "$AUTH_HEADER" \
  -d @grafana/dashboards/storage-quota-dashboard.json \
  "${GRAFANA_URL}/api/dashboards/db"

echo "Dashboards imported successfully!"
