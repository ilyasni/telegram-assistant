#!/usr/bin/env bash
# Context7: Скрипт для настройки прав доступа к каталогу SearXNG
# Основано на best practices из n8n-installer: https://github.com/kossakovsky/n8n-installer
# Использование: ./scripts/setup_searxng_permissions.sh

set -euo pipefail

SEARXNG_DIR="/opt/telegram-assistant/searxng"

echo "=== Настройка прав доступа для SearXNG ==="
echo ""

# Проверяем, запущен ли контейнер
if ! docker compose ps searxng 2>/dev/null | grep -q "Up"; then
    echo "⚠️  Контейнер SearXNG не запущен. Запускаем..."
    docker compose --profile rag up -d searxng 2>/dev/null || true
    sleep 5
fi

# Получаем uid/gid из контейнера SearXNG
echo "1. Получение uid/gid из контейнера SearXNG..."
SEARXNG_UID=$(docker compose exec -T searxng id -u 2>/dev/null | tr -d '[:space:]' || echo "977")
SEARXNG_GID=$(docker compose exec -T searxng id -g 2>/dev/null | tr -d '[:space:]' || echo "977")

echo "   SearXNG работает под uid:gid = ${SEARXNG_UID}:${SEARXNG_GID}"
echo ""

# Проверяем существование каталога
if [ ! -d "$SEARXNG_DIR" ]; then
    echo "   Создание каталога $SEARXNG_DIR..."
    mkdir -p "$SEARXNG_DIR"
fi

# Устанавливаем владельца и права на каталог
echo "2. Установка прав на каталог..."
CURRENT_USER=$(whoami)
CURRENT_UID=$(id -u)
CURRENT_GID=$(id -g)

# Context7: Устанавливаем владельца на текущего пользователя для возможности редактирования
# Затем добавляем SearXNG в группу или используем ACL
sudo chown -R "${CURRENT_USER}:${CURRENT_USER}" "$SEARXNG_DIR"
sudo chmod -R 755 "$SEARXNG_DIR"

# Устанавливаем права на файлы (если они существуют)
if [ -f "$SEARXNG_DIR/settings.yml" ]; then
    echo "   Установка прав на settings.yml..."
    sudo chmod 644 "$SEARXNG_DIR/settings.yml"
fi

if [ -f "$SEARXNG_DIR/limiter.toml" ]; then
    echo "   Установка прав на limiter.toml..."
    sudo chmod 644 "$SEARXNG_DIR/limiter.toml"
fi

# Context7: Настраиваем права так, чтобы контейнер мог читать, а пользователь - редактировать
echo ""
echo "3. Настройка прав для контейнера и пользователя..."
CURRENT_USER=$(whoami)

# Вариант 1: ACL (если поддерживается)
if command -v setfacl >/dev/null 2>&1; then
    # Добавляем права для контейнера SearXNG через ACL
    sudo setfacl -R -m "u:${SEARXNG_UID}:r-X" "$SEARXNG_DIR" 2>/dev/null && {
        echo "   ✅ ACL настроен для контейнера SearXNG (uid: ${SEARXNG_UID})"
        echo "   ✅ Текущий пользователь ${CURRENT_USER} имеет права на запись"
    } || {
        echo "   ⚠️  ACL не поддерживается, используем альтернативный подход..."
        # Альтернатива: добавляем SearXNG в группу пользователя
        sudo groupadd -g "${SEARXNG_GID}" searxng_group 2>/dev/null || true
        sudo usermod -a -G searxng_group "${CURRENT_USER}" 2>/dev/null || true
        sudo chgrp -R "${CURRENT_USER}" "$SEARXNG_DIR"
        sudo chmod -R g+w "$SEARXNG_DIR"
        echo "   ✅ Права на запись для группы настроены"
    }
else
    echo "   ⚠️  setfacl не установлен, используем группу..."
    # Добавляем SearXNG в группу пользователя
    sudo chgrp -R "${CURRENT_USER}" "$SEARXNG_DIR"
    sudo chmod -R g+w "$SEARXNG_DIR"
    echo "   ✅ Права на запись для группы настроены"
fi

echo ""
echo "✅ Права доступа настроены!"
echo ""
echo "📋 Проверка:"
ls -la "$SEARXNG_DIR" | head -5
echo ""
echo "💡 Теперь:"
echo "   - Контейнер SearXNG может читать/записывать файлы"
echo "   - Вы можете редактировать файлы без sudo"
echo "   - Не нужно выполнять chown каждый раз"
echo ""
echo "🔄 Если нужно обновить права в будущем, запустите скрипт снова:"
echo "   ./scripts/setup_searxng_permissions.sh"
