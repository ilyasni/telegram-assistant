#!/bin/bash
# Скрипт для обновления tg_channel_id для каналов про пиво
# Использование: 
#   ./update_beer_channels_manual.sh <beer_for_all_id> <beer_by_id> <prostopropivo_id>
#   Пример: ./update_beer_channels_manual.sh -1001234567890 -1001234567891 -1001234567892

if [ $# -ne 3 ]; then
    echo "❌ Ошибка: Неверное количество аргументов"
    echo ""
    echo "Использование: $0 <beer_for_all_id> <beer_by_id> <prostopropivo_id>"
    echo ""
    echo "Пример:"
    echo "  $0 -1001234567890 -1001234567891 -1001234567892"
    echo ""
    echo "Где <beer_for_all_id>, <beer_by_id>, <prostopropivo_id> - это отрицательные числа (telegram_id)"
    echo ""
    echo "Для получения telegram_id используйте:"
    echo "  1. Бот @userinfobot - отправьте ссылку на канал"
    echo "  2. Бот @getidsbot - отправьте ссылку на канал"
    echo "  3. Telegram Desktop - View -> Statistics -> Channel ID"
    echo ""
    echo "Ссылки на каналы:"
    echo "  - https://t.me/beer_for_all"
    echo "  - https://t.me/beer_by"
    echo "  - https://t.me/prostopropivo"
    exit 1
fi

BEER_FOR_ALL_ID=$1
BEER_BY_ID=$2
PROSTOPROPIVO_ID=$3

# Проверка, что ID отрицательные числа
if ! [[ "$BEER_FOR_ALL_ID" =~ ^-?[0-9]+$ ]] || [ "$BEER_FOR_ALL_ID" -ge 0 ]; then
    echo "❌ Ошибка: beer_for_all_id должен быть отрицательным числом"
    echo "   Получено: $BEER_FOR_ALL_ID"
    exit 1
fi

if ! [[ "$BEER_BY_ID" =~ ^-?[0-9]+$ ]] || [ "$BEER_BY_ID" -ge 0 ]; then
    echo "❌ Ошибка: beer_by_id должен быть отрицательным числом"
    echo "   Получено: $BEER_BY_ID"
    exit 1
fi

if ! [[ "$PROSTOPROPIVO_ID" =~ ^-?[0-9]+$ ]] || [ "$PROSTOPROPIVO_ID" -ge 0 ]; then
    echo "❌ Ошибка: prostopropivo_id должен быть отрицательным числом"
    echo "   Получено: $PROSTOPROPIVO_ID"
    exit 1
fi

echo "🔄 Обновление tg_channel_id для каналов про пиво..."
echo "   beer_for_all: $BEER_FOR_ALL_ID"
echo "   beer_by: $BEER_BY_ID"
echo "   prostopropivo: $PROSTOPROPIVO_ID"
echo ""

docker exec telegram-assistant-supabase-db-1 psql -U postgres -d postgres <<EOF
-- Обновление beer_for_all
UPDATE channels SET tg_channel_id = $BEER_FOR_ALL_ID WHERE username = 'beer_for_all';

-- Обновление beer_by
UPDATE channels SET tg_channel_id = $BEER_BY_ID WHERE username = 'beer_by';

-- Обновление prostopropivo
UPDATE channels SET tg_channel_id = $PROSTOPROPIVO_ID WHERE username = 'prostopropivo';

-- Проверка результатов
SELECT id, username, title, tg_channel_id 
FROM channels 
WHERE username IN ('beer_for_all', 'beer_by', 'prostopropivo')
ORDER BY username;
EOF

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ Обновление завершено успешно!"
else
    echo ""
    echo "❌ Ошибка при обновлении"
    exit 1
fi

