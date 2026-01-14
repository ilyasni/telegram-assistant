#!/bin/bash
# Скрипт для проверки состояния Telegram Bot API

BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-$(docker compose exec -T api printenv TELEGRAM_BOT_TOKEN 2>/dev/null | head -1)}"

if [ -z "$BOT_TOKEN" ]; then
    echo "❌ TELEGRAM_BOT_TOKEN не найден"
    exit 1
fi

API_URL="https://api.telegram.org/bot${BOT_TOKEN}"

echo "🔍 Проверка Telegram Bot API..."
echo ""

# 1. Базовый метод getMe
echo "1. Тест getMe (базовый метод):"
START_TIME=$(date +%s%N)
RESPONSE=$(timeout 5 curl -s "${API_URL}/getMe" 2>&1)
END_TIME=$(date +%s%N)
DURATION=$(( (END_TIME - START_TIME) / 1000000 ))

if echo "$RESPONSE" | grep -q '"ok":true'; then
    echo "   ✅ OK (${DURATION}ms)"
else
    echo "   ❌ ОШИБКА"
    echo "$RESPONSE" | head -3
fi
echo ""

# 2. getWebhookInfo
echo "2. Тест getWebhookInfo:"
START_TIME=$(date +%s%N)
RESPONSE=$(timeout 10 curl -s "${API_URL}/getWebhookInfo" 2>&1)
END_TIME=$(date +%s%N)
DURATION=$(( (END_TIME - START_TIME) / 1000000 ))

if echo "$RESPONSE" | grep -q '"ok":true'; then
    echo "   ✅ OK (${DURATION}ms)"
    LAST_ERROR=$(echo "$RESPONSE" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d.get('result', {}).get('last_error_message', 'нет'))" 2>/dev/null || echo "нет")
    if [ "$LAST_ERROR" != "нет" ] && [ "$LAST_ERROR" != "null" ]; then
        echo "   ⚠️  Последняя ошибка webhook: $LAST_ERROR"
    fi
else
    echo "   ❌ ОШИБКА"
    echo "$RESPONSE" | head -3
fi
echo ""

# 3. getMyCommands
echo "3. Тест getMyCommands:"
START_TIME=$(date +%s%N)
RESPONSE=$(timeout 10 curl -s "${API_URL}/getMyCommands" 2>&1)
END_TIME=$(date +%s%N)
DURATION=$(( (END_TIME - START_TIME) / 1000000 ))

if echo "$RESPONSE" | grep -q '"ok":true'; then
    echo "   ✅ OK (${DURATION}ms)"
else
    echo "   ❌ ОШИБКА"
    echo "$RESPONSE" | head -3
fi
echo ""

# 4. Множественные запросы для проверки стабильности
echo "4. Тест стабильности (5 запросов getMe):"
TIMES=""
for i in {1..5}; do
    START_TIME=$(date +%s%N)
    timeout 5 curl -s "${API_URL}/getMe" >/dev/null 2>&1
    END_TIME=$(date +%s%N)
    DURATION=$(( (END_TIME - START_TIME) / 1000000 ))
    TIMES="${TIMES}${DURATION} "
    echo -n "   Попытка $i: ${DURATION}ms "
    if [ $DURATION -lt 1000 ]; then
        echo "✅"
    elif [ $DURATION -lt 5000 ]; then
        echo "⚠️  (медленно)"
    else
        echo "❌ (таймаут)"
    fi
    sleep 0.5
done

# Вычисляем среднее
AVG=$(echo "$TIMES" | awk '{sum=0; count=0; for(i=1;i<=NF;i++) {sum+=$i; count++}; print sum/count}')
echo "   Среднее время: ${AVG}ms"
echo ""

# 5. Проверка статуса через веб
echo "5. Статус Telegram (status.telegram.org):"
STATUS=$(timeout 5 curl -s "https://status.telegram.org/" 2>&1 | head -1)
if [ ${#STATUS} -gt 10 ]; then
    echo "   ✅ Сайт статуса доступен"
else
    echo "   ⚠️  Не удалось получить статус"
fi
echo ""

# 6. Сводка
echo "📊 Сводка:"
if [ $(echo "$AVG" | cut -d. -f1) -lt 500 ]; then
    echo "   ✅ Telegram API работает нормально (< 500ms)"
elif [ $(echo "$AVG" | cut -d. -f1) -lt 2000 ]; then
    echo "   ⚠️  Telegram API работает медленно (500-2000ms)"
else
    echo "   ❌ Telegram API работает очень медленно (> 2000ms)"
fi
