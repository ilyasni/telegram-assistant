#!/bin/bash
# Утилита для просмотра Redis Streams (Vision Analysis)
# Context7: Красивый вывод с форматированием JSON и фильтрацией

set -euo pipefail

# Конфигурация
STREAM_VISION="${STREAM_VISION:-stream:posts:vision}"
STREAM_ANALYZED="${STREAM_ANALYZED:-stream:posts:vision:analyzed}"
STREAM_DLQ="${STREAM_DLQ:-stream:posts:vision:dlq}"
REDIS_URL="${REDIS_URL:-redis://redis:6379/0}"
DEFAULT_COUNT=5
DEFAULT_FORMAT="json"

# Парсинг аргументов
STREAM_ARG="all"
COUNT=$DEFAULT_COUNT
FORMAT=$DEFAULT_FORMAT
FILTER_FIELD=""
FILTER_VALUE=""
REDIS_HOST="${REDIS_HOST:-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --stream)
            STREAM_ARG="$2"
            shift 2
            ;;
        --count)
            COUNT="$2"
            shift 2
            ;;
        --format)
            FORMAT="$2"
            shift 2
            ;;
        --filter)
            FILTER="$2"
            IFS='=' read -r FILTER_FIELD FILTER_VALUE <<< "$FILTER"
            shift 2
            ;;
        --redis-url)
            REDIS_URL="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            echo "Usage: $0 [--stream vision|analyzed|dlq|all] [--count N] [--format json|table|compact] [--filter FIELD=VALUE] [--redis-url URL]"
            exit 1
            ;;
    esac
done

# Извлечение host и port из REDIS_URL
if [[ "$REDIS_URL" =~ redis://([^:]+):([0-9]+) ]]; then
    REDIS_HOST="${BASH_REMATCH[1]}"
    REDIS_PORT="${BASH_REMATCH[2]}"
fi

REDIS_CLI_CMD="redis-cli -h $REDIS_HOST -p $REDIS_PORT"

# Проверка redis-cli
if ! command -v redis-cli &> /dev/null; then
    echo "ERROR: redis-cli not found. Please install redis-cli or run inside Docker container."
    exit 1
fi

# Функция форматирования JSON
format_json() {
    local json_str="$1"
    if command -v jq &> /dev/null; then
        echo "$json_str" | jq .
    else
        python3 -m json.tool <<< "$json_str" 2>/dev/null || echo "$json_str"
    fi
}

# Функция извлечения данных из Redis Stream
get_stream_data() {
    local stream_name="$1"
    local count="$2"
    
    # XRANGE stream - + COUNT count
    local raw_data=$($REDIS_CLI_CMD XRANGE "$stream_name" - + COUNT "$count" 2>/dev/null)
    
    if [ -z "$raw_data" ] || [ "$raw_data" = "(empty array)" ]; then
        return 1
    fi
    
    # Парсинг вывода redis-cli (формат: 1) "message_id" 2) "field1" 3) "value1" ...)
    echo "$raw_data"
}

# Функция парсинга и форматирования события
parse_event() {
    local raw_line="$1"
    local stream_name="$2"
    
    # Простой парсинг через awk/sed для извлечения message_id и полей
    # Формат redis-cli: 1) "message_id" 2) "field1" 3) "value1" 4) "field2" 5) "value2" ...
    
    local message_id=$(echo "$raw_line" | grep -oP '^\s*\d+\)\s+"\K[^"]+' | head -1 || echo "")
    if [ -z "$message_id" ]; then
        return 1
    fi
    
    # Извлечение полей (упрощенный вариант - используем Python для надежности)
    python3 <<EOF
import sys
import json
import re

raw_line = '''$raw_line'''

# Извлечение message_id
msg_id_match = re.search(r'^\s*\d+\)\s+"([^"]+)"', raw_line, re.MULTILINE)
if not msg_id_match:
    sys.exit(1)

message_id = msg_id_match.group(1)

# Извлечение полей (находим все пары "field" "value")
fields = {}
matches = re.findall(r'"([^"]+)"', raw_line)
if len(matches) >= 2:
    # Пропускаем первый match (message_id)
    for i in range(1, len(matches), 2):
        if i + 1 < len(matches):
            field = matches[i]
            value = matches[i + 1]
            fields[field] = value

# Парсинг data если это JSON
if 'data' in fields:
    try:
        data_json = json.loads(fields['data'])
        fields['data'] = data_json
    except:
        pass

result = {
    'message_id': message_id,
    'stream': '$stream_name',
    'fields': fields
}

print(json.dumps(result, indent=2, default=str))
EOF
}

# Функция фильтрации события
filter_event() {
    local event_json="$1"
    if [ -z "$FILTER_FIELD" ] || [ -z "$FILTER_VALUE" ]; then
        echo "$event_json"
        return 0
    fi
    
    # Проверка через Python
    python3 <<EOF
import json
import sys

event = json.load(sys.stdin)
fields = event.get('fields', {})

# Проверка в fields или в data
field_value = fields.get('$FILTER_FIELD', '')
if isinstance(fields.get('data'), dict):
    field_value = fields['data'].get('$FILTER_FIELD', field_value)

if str(field_value) == '$FILTER_VALUE':
    print(json.dumps(event, indent=2, default=str))
EOF
}

# Функция вывода в табличном формате
format_table() {
    local events_json="$1"
    python3 <<EOF
import json
import sys

try:
    events = json.load(sys.stdin) if isinstance(sys.stdin, str) else json.load(sys.stdin)
    if not isinstance(events, list):
        events = [events]
    
    if not events:
        print("No events found")
        return
    
    # Заголовок таблицы
    print(f"{'Stream':<30} {'Message ID':<20} {'Event':<25} {'Post ID':<15} {'Trace ID':<15} {'Skipped':<10}")
    print("=" * 120)
    
    for event in events:
        stream = event.get('stream', 'N/A')
        msg_id = event.get('message_id', 'N/A')[:18] + '...' if len(event.get('message_id', '')) > 20 else event.get('message_id', 'N/A')
        fields = event.get('fields', {})
        event_type = fields.get('event', 'N/A')
        skipped = fields.get('skipped', 'false')
        
        post_id = 'N/A'
        trace_id = 'N/A'
        
        # Извлечение из data если это dict
        data = fields.get('data', {})
        if isinstance(data, dict):
            post_id = data.get('post_id', 'N/A')[:13] + '...' if len(str(data.get('post_id', ''))) > 15 else data.get('post_id', 'N/A')
            trace_id = data.get('trace_id', 'N/A')[:13] + '...' if len(str(data.get('trace_id', ''))) > 15 else data.get('trace_id', 'N/A')
        elif isinstance(data, str):
            try:
                import json
                data_parsed = json.loads(data)
                post_id = data_parsed.get('post_id', 'N/A')[:13] + '...' if len(str(data_parsed.get('post_id', ''))) > 15 else data_parsed.get('post_id', 'N/A')
                trace_id = data_parsed.get('trace_id', 'N/A')[:13] + '...' if len(str(data_parsed.get('trace_id', ''))) > 15 else data_parsed.get('trace_id', 'N/A')
            except:
                pass
        
        print(f"{stream:<30} {msg_id:<20} {event_type:<25} {post_id:<15} {trace_id:<15} {skipped:<10}")
        
except Exception as e:
    print(f"Error formatting table: {e}", file=sys.stderr)
EOF
}

# Основная логика
STREAMS=()
case "$STREAM_ARG" in
    vision)
        STREAMS=("$STREAM_VISION")
        ;;
    analyzed)
        STREAMS=("$STREAM_ANALYZED")
        ;;
    dlq)
        STREAMS=("$STREAM_DLQ")
        ;;
    all)
        STREAMS=("$STREAM_VISION" "$STREAM_ANALYZED" "$STREAM_DLQ")
        ;;
    *)
        echo "ERROR: Invalid stream name: $STREAM_ARG (must be vision|analyzed|dlq|all)"
        exit 1
        ;;
esac

# Сбор событий
ALL_EVENTS=()

for stream in "${STREAMS[@]}"; do
    stream_length=$($REDIS_CLI_CMD XLEN "$stream" 2>/dev/null || echo "0")
    
    if [ "$stream_length" = "0" ] || [ -z "$stream_length" ]; then
        continue
    fi
    
    # Получение данных из стрима
    raw_data=$(get_stream_data "$stream" "$COUNT" || echo "")
    
    if [ -n "$raw_data" ]; then
        # Парсинг каждого события (упрощенный вариант - используем Python)
        events=$(python3 <<EOF
import re
import json

raw_data = '''$raw_data'''

# Разбиение на сообщения (каждое сообщение начинается с числа)
messages = re.split(r'^\s*\d+\)\s+', raw_data, flags=re.MULTILINE)
messages = [m for m in messages if m.strip()]

parsed_events = []
for msg in messages:
    if not msg.strip():
        continue
    
    # Извлечение message_id (первая строка в кавычках)
    msg_id_match = re.search(r'"([^"]+)"', msg)
    if not msg_id_match:
        continue
    
    message_id = msg_id_match.group(1)
    
    # Извлечение полей
    fields = {}
    field_matches = re.findall(r'"([^"]+)"', msg)
    
    # Пропускаем первый match (message_id), затем пары field-value
    for i in range(1, len(field_matches), 2):
        if i + 1 < len(field_matches):
            field = field_matches[i]
            value = field_matches[i + 1]
            fields[field] = value
    
    # Парсинг data если это JSON
    if 'data' in fields:
        try:
            data_json = json.loads(fields['data'])
            fields['data'] = data_json
        except:
            pass
    
    parsed_events.append({
        'message_id': message_id,
        'stream': '$stream',
        'fields': fields
    })

print(json.dumps(parsed_events, indent=2, default=str))
EOF
)
        
        # Фильтрация и добавление
        if [ -n "$FILTER_FIELD" ] && [ -n "$FILTER_VALUE" ]; then
            filtered=$(echo "$events" | python3 -c "
import json
import sys
events = json.load(sys.stdin)
for event in events:
    fields = event.get('fields', {})
    field_value = fields.get('$FILTER_FIELD', '')
    if isinstance(fields.get('data'), dict):
        field_value = fields['data'].get('$FILTER_FIELD', field_value)
    if str(field_value) == '$FILTER_VALUE':
        print(json.dumps(event, default=str))
")
            if [ -n "$filtered" ]; then
                ALL_EVENTS+=("$filtered")
            fi
        else:
            ALL_EVENTS+=("$events")
        fi
    fi
done

# Вывод результатов
if [ ${#ALL_EVENTS[@]} -eq 0 ]; then
    echo "No events found in streams: ${STREAMS[*]}"
    exit 0
fi

# Объединение всех событий
COMBINED=$(printf '%s\n' "${ALL_EVENTS[@]}" | python3 -c "
import json
import sys

all_events = []
for line in sys.stdin:
    if line.strip():
        try:
            events = json.loads(line)
            if isinstance(events, list):
                all_events.extend(events)
            else:
                all_events.append(events)
        except:
            pass

print(json.dumps(all_events, indent=2, default=str))
")

# Форматирование вывода
case "$FORMAT" in
    json)
        format_json "$COMBINED"
        ;;
    table)
        echo "$COMBINED" | format_table
        ;;
    compact)
        echo "$COMBINED" | python3 -c "
import json
import sys
events = json.load(sys.stdin)
for event in events:
    msg_id = event.get('message_id', 'N/A')[:16]
    stream = event.get('stream', 'N/A').split(':')[-1]
    fields = event.get('fields', {})
    event_type = fields.get('event', 'N/A')
    print(f'{stream}:{msg_id} {event_type}')
"
        ;;
    *)
        echo "ERROR: Invalid format: $FORMAT (must be json|table|compact)"
        exit 1
        ;;
esac

