#!/usr/bin/env bash
# Context7: Экстренный патч для отключения bot detection в SearXNG
# Основано на гарантированном решении: патчинг кода на лету
# Использование: ./scripts/patch_searxng_botdetection.sh

set -euo pipefail

CONTAINER_NAME="searxng"
BACKUP_DIR="/tmp/searxng_backup_$(date +%Y%m%d_%H%M%S)"

echo "=== Патчинг SearXNG для отключения bot detection ==="
echo ""

# Проверяем, запущен ли контейнер
if ! docker ps | grep -q "$CONTAINER_NAME"; then
    echo "❌ Контейнер $CONTAINER_NAME не запущен"
    exit 1
fi

echo "1. Создание бэкапа оригинальных файлов..."
docker exec "$CONTAINER_NAME" sh -c "
  mkdir -p $BACKUP_DIR
  cp /usr/local/searxng/searx/__init__.py $BACKUP_DIR/__init__.py.backup 2>/dev/null || true
  echo '   Бэкап создан: $BACKUP_DIR'
"

echo ""
echo "2. Патчинг файла __init__.py..."
docker exec "$CONTAINER_NAME" sh -c "
  # Бэкап оригинального файла
  cp /usr/local/searxng/searx/__init__.py /usr/local/searxng/searx/__init__.py.backup
  
  # Патчим файл - комментируем все вызовы botdetection
  sed -i 's/from searx.botdetection import get_botdetector/# from searx.botdetection import get_botdetector/g' /usr/local/searxng/searx/__init__.py
  sed -i 's/botdetector = get_botdetector()/# botdetector = get_botdetector()/g' /usr/local/searxng/searx/__init__.py
  sed -i 's/if botdetector:/if False: # botdetector:/g' /usr/local/searxng/searx/__init__.py
  sed -i 's/return botdetector.redirect_tor()/# return botdetector.redirect_tor()/g' /usr/local/searxng/searx/__init__.py
  sed -i 's/botdetector.is_ok(request)/True # botdetector.is_ok(request)/g' /usr/local/searxng/searx/__init__.py
  
  echo '   ✅ Файл __init__.py запатчен'
"

echo ""
echo "3. Перезапуск контейнера..."
docker restart "$CONTAINER_NAME"

echo ""
echo "4. Ожидание запуска контейнера..."
sleep 15

echo ""
echo "✅ Bot detection запатчен!"
echo ""
echo "📋 Проверка:"
echo "   docker logs $CONTAINER_NAME --tail 20"
echo ""
echo "💡 Откат (если нужно):"
echo "   docker exec $CONTAINER_NAME cp /usr/local/searxng/searx/__init__.py.backup /usr/local/searxng/searx/__init__.py"
echo "   docker restart $CONTAINER_NAME"

