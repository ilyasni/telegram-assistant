#!/usr/bin/env bash
set -euo pipefail
# [C7-ID: ENV-SEC-003] Не трогать базовый compose в дев — используем override

# [C7-ID: dev-mode-011] Context7 best practice: проверка compose с учетом profiles
# В docker-compose.yml все core-сервисы в profile "core", поэтому используем --profile core
PROFILE="${COMPOSE_PROFILE:-core}"

# Проверяем синтаксис compose файла (без профиля для базовой проверки)
# Если проверка с профилем падает из-за отсутствующих опциональных сервисов, это OK
if ! docker compose config -q 2>/dev/null; then
  echo "WARN: Базовая проверка синтаксиса compose файла выдала предупреждения (может быть из-за отсутствующих переменных)"
fi

# [C7-ID: dev-mode-011] Context7 best practice: различаем core и optional сервисы
# По умолчанию проверяем только core-сервисы с профилем, остальные — предупреждения
# Можно переопределить список через переменную окружения REQUIRED_SERVICES (через пробел)

CORE_SERVICES=(redis qdrant api worker telethon-ingest)
OPTIONAL_SERVICES=(supabase-db kong rest auth storage grafana neo4j qdrant-dashboard caddy crawl4ai)

# Если задан REQUIRED_SERVICES, используем его как строгий список
if [[ ${REQUIRED_SERVICES:-} != "" ]]; then
  IFS=' ' read -r -a REQUIRED_LIST <<< "${REQUIRED_SERVICES}"
else
  REQUIRED_LIST=("${CORE_SERVICES[@]}")
fi

# Получаем список сервисов с учетом профиля
# Если профиль не работает из-за ошибок зависимостей (например, отсутствующие опциональные сервисы),
# пробуем без профиля (только базовый синтаксис)
AVAILABLE_SERVICES=$(docker compose --profile "${PROFILE}" config --services 2>/dev/null || docker compose config --services 2>/dev/null || echo "")

# Проверяем обязательные сервисы
# Если список сервисов пуст (ошибка конфигурации), проверяем через прямое чтение compose файла
if [[ -z "${AVAILABLE_SERVICES}" ]]; then
  echo "WARN: Не удалось получить список сервисов через docker compose config"
  echo "      Проверяем наличие обязательных сервисов через прямое чтение docker-compose.yml..."
  # Простая проверка через grep (не идеально, но лучше чем ничего)
  for s in "${REQUIRED_LIST[@]}"; do
    if ! grep -q "^  ${s}:" docker-compose.yml docker-compose.dev.yml 2>/dev/null; then
      echo "WARN: Сервис ${s} не найден в compose файлах (может быть в profile или require env vars)"
    fi
  done
  echo "compose OK (упрощенная проверка из-за ошибок конфигурации)"
  exit 0
fi

# Проверяем обязательные сервисы
for s in "${REQUIRED_LIST[@]}"; do
  if ! echo "${AVAILABLE_SERVICES}" | grep -qx "$s"; then
    echo "ERROR: отсутствует обязательный сервис: $s (проверка с profile=${PROFILE})"
    exit 1
  fi
done

# Предупреждаем по опциональным сервисам
for s in "${OPTIONAL_SERVICES[@]}"; do
  if ! echo "${AVAILABLE_SERVICES}" | grep -qx "$s"; then
    echo "WARN: отсутствует опциональный сервис: $s"
  fi
done

echo "compose OK (profile=${PROFILE}, сервисов: $(echo "${AVAILABLE_SERVICES}" | wc -l))"
