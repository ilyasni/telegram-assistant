#!/usr/bin/env bash
# Context7: Простой скрипт для исправления прав доступа к каталогу SearXNG
# Использование: запустите с sudo: sudo ./scripts/fix_searxng_permissions.sh
# Или выполните команды вручную

set -euo pipefail

SEARXNG_DIR="/opt/telegram-assistant/searxng"
CURRENT_USER=$(whoami)

echo "=== Исправление прав доступа для SearXNG ==="
echo ""
echo "Текущий пользователь: ${CURRENT_USER}"
echo "Каталог: ${SEARXNG_DIR}"
echo ""

# Проверяем, запущен ли скрипт с sudo
if [ "$EUID" -ne 0 ]; then 
    echo "❌ Этот скрипт должен быть запущен с sudo"
    echo ""
    echo "Выполните одну из команд:"
    echo "  sudo ./scripts/fix_searxng_permissions.sh"
    echo ""
    echo "Или выполните команды вручную:"
    echo "  sudo chown -R ${CURRENT_USER}:${CURRENT_USER} ${SEARXNG_DIR}"
    echo "  sudo chmod -R 755 ${SEARXNG_DIR}"
    exit 1
fi

echo "1. Установка владельца на текущего пользователя..."
chown -R "${CURRENT_USER}:${CURRENT_USER}" "$SEARXNG_DIR"
chmod -R 755 "$SEARXNG_DIR"

echo ""
echo "2. Установка прав на файлы..."
if [ -f "$SEARXNG_DIR/settings.yml" ]; then
    chmod 644 "$SEARXNG_DIR/settings.yml"
    echo "   ✅ settings.yml"
fi

if [ -f "$SEARXNG_DIR/limiter.toml" ]; then
    chmod 644 "$SEARXNG_DIR/limiter.toml"
    echo "   ✅ limiter.toml"
fi

echo ""
echo "✅ Права доступа исправлены!"
echo ""
echo "📋 Проверка:"
ls -la "$SEARXNG_DIR" | head -5
echo ""
echo "💡 Теперь вы можете редактировать файлы без проблем!"

