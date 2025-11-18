#!/usr/bin/env bash
set -euo pipefail

NET=$(docker inspect searxng --format '{{range .NetworkSettings.Networks}}{{.NetworkID}}{{end}}' | xargs docker network inspect --format '{{.Name}}')

echo "Network: $NET"
docker network inspect "$NET" | jq '.[0].IPAM.Config'

CLIENT=${1:-api}
CIP=$(docker inspect "$CLIENT" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')
echo "Client IP ($CLIENT): $CIP"

echo "Curl with headers from $CLIENT → searxng"
docker exec -it "$CLIENT" sh -lc \
  "curl -s -o /dev/null -w '%{http_code}\n' 'http://searxng:8080/search?q=test&format=json' \
   -H 'User-Agent: TelegramAssistant/3.1' \
   -H 'X-Real-IP: $CIP' \
   -H 'X-Forwarded-For: $CIP'"
