#!/usr/bin/env bash
set -euo pipefail
# [C7-ID: ENV-SEC-003] Не трогать базовый compose в дев — используем override

docker compose config -q

# [C7-ID: dev-mode-011] Context7 best practice: различать core и optional сервисы
# По умолчанию проверяем только core-сервисы, остальные — предупреждения
# Можно переопределить список через переменную окружения REQUIRED_SERVICES (через пробел)

CORE_SERVICES=(redis qdrant api worker telethon-ingest)
OPTIONAL_SERVICES=(supabase-db kong rest auth storage grafana neo4j qdrant-dashboard caddy)

# Если задан REQUIRED_SERVICES, используем его как строгий список
if [[ ${REQUIRED_SERVICES:-} != "" ]]; then
  IFS=' ' read -r -a REQUIRED_LIST <<< "${REQUIRED_SERVICES}"
else
  REQUIRED_LIST=("${CORE_SERVICES[@]}")
fi

# Проверяем обязательные
for s in "${REQUIRED_LIST[@]}"; do
  if ! docker compose config --services | grep -qx "$s"; then
    echo "ERROR: отсутствует обязательный сервис: $s"
    exit 1
  fi
done

# Предупреждаем по опциональным
for s in "${OPTIONAL_SERVICES[@]}"; do
  if ! docker compose config --services | grep -qx "$s"; then
    echo "WARN: отсутствует опциональный сервис: $s"
  fi
done

echo "compose OK"
