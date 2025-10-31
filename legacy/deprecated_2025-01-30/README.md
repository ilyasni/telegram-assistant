# Deprecated Code - 2025-01-30

[C7-ID: CODE-CLEANUP-024] Карантин deprecated кода с метками для авто-удаления

## Файлы

### backup_scheduler.py
- **Deprecated since:** 2025-01-30
- **Remove by:** 2025-02-13
- **Reason:** Зависит от deprecated UnifiedSessionManager
- **Replacement:** TelegramClientManager + session_storage.py

### miniapp_auth.py
- **Deprecated since:** 2025-01-30
- **Remove by:** 2025-02-13
- **Reason:** Роутер не подключен к main.py, зависит от deprecated UnifiedSessionManager
- **Replacement:** api/routers/tg_auth.py для авторизации через miniapp

### worker/shared/s3_storage.py
- **Deprecated since:** 2025-01-30
- **Remove by:** 2025-02-13
- **Reason:** Точный дубликат api/services/s3_storage.py
- **Replacement:** from api.services.s3_storage import S3StorageService
- **Note:** Все импорты уже используют api.services.s3_storage

### worker/health_check.py
- **Deprecated since:** 2025-01-30
- **Remove by:** 2025-02-13
- **Reason:** Дубликат функциональности worker/health.py (который использует feature flags)
- **Replacement:** from worker.health import check_integrations

### worker/simple_health_server.py
- **Deprecated since:** 2025-01-30
- **Remove by:** 2025-02-13
- **Reason:** Не используется в worker/main.py, используется worker/health_server.py вместо этого
- **Replacement:** worker/health_server.py (уже используется)
- **Status:** ✅ Не используется (проверено через grep)

## Runtime Guard

Все файлы в этом карантине блокируют импорт в production:

```python
if os.getenv("ENV") == "production":
    raise ImportError("...")
```

## Проверка использования

Перед удалением проверить:
```bash
grep -r "from worker.health_check" .
grep -r "from worker.shared.s3_storage" .
grep -r "import worker.health_check" .
grep -r "import worker.shared.s3_storage" .
```
