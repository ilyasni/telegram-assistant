# Troubleshooting Vision API Integration

**Версия**: 1.0 | **Дата**: 2025-01-28

## Проблема: Vision Task не запускается

### Симптомы

1. В логах supervisor: "Starting supervisor with 5 tasks" (вместо 6, включая vision)
2. Нет сообщений "Vision Analysis task registered with supervisor"
3. `GIGACHAT_CREDENTIALS` не виден в worker контейнере

### Решение

**Шаг 1: Проверка .env файла**

```bash
# Проверьте наличие GIGACHAT_CREDENTIALS
grep GIGACHAT_CREDENTIALS .env
```

Должна быть строка:
```bash
GIGACHAT_CREDENTIALS=base64_encoded_credentials_here
```

**Шаг 2: Проверка передачи в docker-compose**

```bash
docker compose config | grep GIGACHAT_CREDENTIALS
```

Должна быть строка в worker service:
```yaml
GIGACHAT_CREDENTIALS: ${GIGACHAT_CREDENTIALS}
```

**Шаг 3: Перезапуск worker с очисткой**

```bash
docker compose down worker
docker compose up -d worker
docker compose logs -f worker | grep -E "(vision|supervisor|Registered)"
```

**Шаг 4: Проверка в контейнере**

```bash
docker exec worker env | grep GIGACHAT
```

Должны быть:
- `GIGACHAT_CREDENTIALS=...`
- `GIGACHAT_SCOPE=GIGACHAT_API_PERS`
- `FEATURE_VISION_ENABLED=true`

---

## Проблема: "Neither GIGACHAT_CREDENTIALS nor GIGACHAT_CLIENT_ID/SECRET configured"

### Симптомы

В логах worker:
```
WARNING: Neither GIGACHAT_CREDENTIALS nor GIGACHAT_CLIENT_ID/SECRET configured, skipping Vision Analysis task
```

### Решение

**Вариант 1: Использовать GIGACHAT_CREDENTIALS (рекомендуется)**

```bash
# В .env файле
GIGACHAT_CREDENTIALS=base64(client_id:client_secret)
```

Генерация:
```bash
echo -n "your_client_id:your_client_secret" | base64
```

**Вариант 2: Использовать CLIENT_ID/SECRET**

```bash
# В .env файле
GIGACHAT_CLIENT_ID=your_client_id
GIGACHAT_CLIENT_SECRET=your_client_secret
```

**Проверка:**

```bash
docker compose restart worker
docker compose logs worker | grep "Vision Analysis task"
```

---

## Проблема: "GigaChat Vision credentials not configured"

### Симптомы

В логах:
```
GigaChat Vision credentials not configured, skipping Vision Analysis task
```

### Решение

1. Проверьте формат credentials:
```bash
docker exec worker python3 << 'EOF'
import os
import base64

credentials = os.getenv("GIGACHAT_CREDENTIALS", "")
if credentials:
    try:
        decoded = base64.b64decode(credentials).decode('utf-8')
        print(f"✅ Format OK: {decoded.split(':')[0]}")
    except Exception as e:
        print(f"❌ Format error: {e}")
else:
    print("❌ Not set")
EOF
```

2. Убедитесь, что credentials в правильном формате:
   - Base64 encoded
   - Формат: `base64(client_id:client_secret)`
   - Без пробелов и переносов строк

---

## Проблема: Vision Task запускается, но не обрабатывает события

### Симптомы

1. В логах: "Vision Analysis task registered with supervisor"
2. События публикуются в `stream:posts:vision:uploaded`
3. Но не обрабатываются (stream length растёт)

### Диагностика

**1. Проверка consumer group:**

```bash
docker exec redis redis-cli XINFO GROUPS stream:posts:vision:uploaded
```

Если группа не создана, worker не запущен или не подключился к stream.

**2. Проверка логов worker:**

```bash
docker compose logs worker | grep -iE "(vision|error|exception)" | tail -50
```

**3. Проверка feature flag:**

```bash
docker exec worker python3 -c "from feature_flags import feature_flags; print(feature_flags.vision_enabled)"
```

Должно быть `True`.

**4. Проверка инициализации VisionAdapter:**

```bash
docker compose logs worker | grep "GigaChatVisionAdapter initialized"
```

Должна быть строка с `credentials_set=True`.

---

## Проблема: S3 credentials not configured

### Симптомы

В логах:
```
S3 credentials not configured, skipping Vision Analysis task
```

### Решение

Проверьте в .env:
```bash
S3_ACCESS_KEY_ID=your_access_key
S3_SECRET_ACCESS_KEY=your_secret_key
S3_BUCKET_NAME=test-467940
```

Проверка:
```bash
docker exec worker env | grep S3_ACCESS_KEY_ID
```

---

## Общая диагностика

### Скрипт полной проверки

```bash
docker exec worker python3 << 'EOF'
import sys
sys.path.insert(0, '/app')
import os
import base64

print("=" * 70)
print("VISION CONFIGURATION DIAGNOSTIC")
print("=" * 70)

# 1. Credentials
credentials = os.getenv("GIGACHAT_CREDENTIALS", "")
print(f"\n1. GIGACHAT_CREDENTIALS:")
if credentials:
    try:
        decoded = base64.b64decode(credentials).decode('utf-8')
        print(f"   ✅ Configured ({len(decoded.split(':'))} parts)")
    except:
        print(f"   ❌ Format error")
else:
    print(f"   ❌ Not set")

# 2. Feature flags
try:
    from feature_flags import feature_flags
    print(f"\n2. Feature Flags:")
    print(f"   vision_enabled: {feature_flags.vision_enabled}")
except Exception as e:
    print(f"\n2. Feature Flags: ❌ {e}")

# 3. S3
s3_key = os.getenv("S3_ACCESS_KEY_ID", "")
print(f"\n3. S3:")
print(f"   S3_ACCESS_KEY_ID: {'✅' if s3_key else '❌'}")

# 4. Vision config
print(f"\n4. Vision Config:")
print(f"   GIGACHAT_SCOPE: {os.getenv('GIGACHAT_SCOPE', 'not set')}")
print(f"   FEATURE_VISION_ENABLED: {os.getenv('FEATURE_VISION_ENABLED', 'not set')}")

print("\n" + "=" * 70)
EOF
```

---

## Checklist для настройки

- [ ] `GIGACHAT_CREDENTIALS` установлен в `.env`
- [ ] `FEATURE_VISION_ENABLED=true` в `.env` (или по умолчанию)
- [ ] `S3_ACCESS_KEY_ID` и `S3_SECRET_ACCESS_KEY` установлены
- [ ] Worker перезапущен: `docker compose restart worker`
- [ ] В логах: "Vision Analysis task registered with supervisor"
- [ ] Consumer group создан в Redis
- [ ] Vision API готов к использованию

---

**Documentation Version**: 1.0  
**Last Updated**: 2025-01-28



