#!/bin/bash
# Скрипт для тестирования fallback на OpenRouter

set -e

API_KEY="${OPENROUTER_API_KEY:-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}"
MODEL="${OPENROUTER_MODEL:-qwen/qwen-2.5-72b-instruct:free}"

echo "Тестирование OpenRouter API..."
echo "Модель: $MODEL"
echo ""

# Тест 1: Проверка доступности модели
echo "1. Проверка доступности модели..."
response=$(curl -s -w "\n%{http_code}" \
  -X POST "https://openrouter.ai/api/v1/chat/completions" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"$MODEL\",
    \"messages\": [
      {\"role\": \"user\", \"content\": \"Привет, это тест.\"}
    ],
    \"max_tokens\": 10
  }")

http_code=$(echo "$response" | tail -n1)
body=$(echo "$response" | head -n-1)

if [ "$http_code" = "200" ]; then
  echo "✅ Модель доступна (HTTP $http_code)"
  echo "$body" | python3 -m json.tool 2>/dev/null | head -20 || echo "$body"
else
  echo "❌ Модель недоступна (HTTP $http_code)"
  echo "$body" | python3 -m json.tool 2>/dev/null || echo "$body"
  
  echo ""
  echo "Попытка найти альтернативные модели..."
  curl -s 'https://openrouter.ai/api/v1/models' \
    -H "Authorization: Bearer $API_KEY" | \
    python3 -c "
import sys, json
data = json.load(sys.stdin)
free_models = [m['id'] for m in data.get('data', []) 
               if m.get('pricing', {}).get('prompt', '0') == '0' 
               and ('instruct' in m.get('id', '').lower() or 'chat' in m.get('id', '').lower())]
print('Доступные бесплатные модели для генерации:')
for m in sorted(free_models)[:10]:
    print(f'  - {m}')
"
fi
