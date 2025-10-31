# GigaChat Credentials для Vision API

**Версия**: 1.0 | **Дата**: 2025-01-28

## Контекст

Vision API использует те же credentials, что и `gpt2giga-proxy`, обеспечивая единую точку конфигурации для всех GigaChat интеграций.

## Способ 1: GIGACHAT_CREDENTIALS (Рекомендуется)

Используется тот же формат, что и для `gpt2giga-proxy`:

```bash
# Формат: base64(client_id:client_secret)
GIGACHAT_CREDENTIALS=dGVzdF9jbGllbnRfaWQ6dGVzdF9jbGllbnRfc2VjcmV0
GIGACHAT_SCOPE=GIGACHAT_API_PERS
```

### Как получить credentials:

1. Зарегистрируйтесь на https://developers.sber.ru/gigachat
2. Создайте приложение и получите Client ID и Client Secret
3. Создайте base64 строку: `base64(client_id:client_secret)`

**Пример:**
```bash
# Если client_id = "test_client_id", client_secret = "test_client_secret"
# Тогда: base64("test_client_id:test_client_secret")
echo -n "test_client_id:test_client_secret" | base64
# Результат: dGVzdF9jbGllbnRfaWQ6dGVzdF9jbGllbnRfc2VjcmV0
```

## Способ 2: GIGACHAT_CLIENT_ID / GIGACHAT_CLIENT_SECRET (Fallback)

Если `GIGACHAT_CREDENTIALS` не установлен, система автоматически сконвертирует `client_id` и `client_secret` в нужный формат:

```bash
GIGACHAT_CLIENT_ID=your_client_id_here
GIGACHAT_CLIENT_SECRET=your_client_secret_here
GIGACHAT_SCOPE=GIGACHAT_API_PERS
```

**Важно:** Предпочтительно использовать `GIGACHAT_CREDENTIALS`, так как это единый формат для всех сервисов.

## Интеграция с gpt2giga-proxy

Оба сервиса используют одинаковые credentials:

- **gpt2giga-proxy**: Использует `GIGACHAT_CREDENTIALS` для OpenAI-compatible API (chat, embeddings)
- **Vision API**: Использует `GIGACHAT_CREDENTIALS` для прямой работы с GigaChat Vision API

### Архитектура:

```
┌─────────────────────────────────────────┐
│         .env файл                       │
│  GIGACHAT_CREDENTIALS=base64(...)      │
└────────────┬────────────────────────────┘
             │
             ├──────────────────────┬──────────────────────┐
             │                      │                      │
             ▼                      ▼                      ▼
    ┌──────────────┐    ┌──────────────────┐    ┌──────────────────┐
    │ gpt2giga-    │    │ Vision API       │    │ Other GigaChat   │
    │ proxy        │    │ (direct SDK)     │    │ integrations     │
    │              │    │                  │    │                  │
    │ • Chat API   │    │ • File upload    │    │ • Direct API     │
    │ • Embeddings │    │ • Vision analysis│    │   calls          │
    └──────────────┘    └──────────────────┘    └──────────────────┘
```

## Проверка credentials

### В контейнере worker:

```bash
docker exec worker python3 << 'EOF'
import os
import base64

credentials = os.getenv("GIGACHAT_CREDENTIALS", "")
if credentials:
    try:
        decoded = base64.b64decode(credentials).decode('utf-8')
        print(f"✅ Credentials configured: {decoded.split(':')[0]}")
    except:
        print("❌ Invalid credentials format")
else:
    print("⚠️  GIGACHAT_CREDENTIALS not set")
EOF
```

### Проверка в логах:

```bash
docker compose logs worker | grep -i "GigaChatVisionAdapter initialized"
# Должна быть строка с credentials_set=True
```

## Миграция с client_id/secret на credentials

Если у вас уже есть `GIGACHAT_CLIENT_ID` и `GIGACHAT_CLIENT_SECRET`, можно сгенерировать `GIGACHAT_CREDENTIALS`:

```bash
# В контейнере worker или локально
python3 << 'EOF'
import base64
import os

client_id = os.getenv("GIGACHAT_CLIENT_ID", "")
client_secret = os.getenv("GIGACHAT_CLIENT_SECRET", "")

if client_id and client_secret:
    creds_str = f"{client_id}:{client_secret}"
    credentials = base64.b64encode(creds_str.encode()).decode()
    print(f"GIGACHAT_CREDENTIALS={credentials}")
else:
    print("GIGACHAT_CLIENT_ID and GIGACHAT_CLIENT_SECRET required")
EOF
```

Затем замените в `.env`:
```bash
# Старое (работает, но не рекомендуется)
# GIGACHAT_CLIENT_ID=...
# GIGACHAT_CLIENT_SECRET=...

# Новое (рекомендуется)
GIGACHAT_CREDENTIALS=dGVzdF9jbGllbnRfaWQ6dGVzdF9jbGllbnRfc2VjcmV0
```

## Ошибки и диагностика

### Ошибка: "GigaChat Vision credentials not configured"

**Причина:** Не установлены `GIGACHAT_CREDENTIALS` или `GIGACHAT_CLIENT_ID/SECRET`

**Решение:**
1. Проверьте `.env` файл
2. Убедитесь, что переменные передаются в docker-compose.yml
3. Перезапустите worker: `docker compose restart worker`

### Ошибка: "Invalid credentials format"

**Причина:** `GIGACHAT_CREDENTIALS` не в формате base64

**Решение:**
1. Убедитесь, что credentials в формате base64
2. Проверьте декодирование: `echo "YOUR_CREDENTIALS" | base64 -d`

### Ошибка: "Authentication failed"

**Причина:** Неверные client_id или client_secret

**Решение:**
1. Проверьте credentials на https://developers.sber.ru/gigachat
2. Убедитесь, что scope правильный (`GIGACHAT_API_PERS`)
3. Проверьте, что приложение имеет доступ к Vision API

## Best Practices

1. **Используйте `GIGACHAT_CREDENTIALS`** вместо client_id/secret для единообразия
2. **Не логируйте credentials** в открытом виде
3. **Проверяйте credentials** после изменения через диагностические команды
4. **Используйте один scope** для всех интеграций (`GIGACHAT_API_PERS`)

---

**Documentation Version**: 1.0  
**Last Updated**: 2025-01-28

