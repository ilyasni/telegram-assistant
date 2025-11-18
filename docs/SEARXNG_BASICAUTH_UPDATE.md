# SearXNG - BasicAuth Update

**Дата**: 2025-02-02  
**Статус**: ✅ BasicAuth и User-Agent добавлены

## ✅ Изменения

### 1. Config (api/config.py)
Добавлены настройки BasicAuth:
```python
searxng_user: str = ""
searxng_password: str = ""
```

### 2. SearXNG Service (api/services/searxng_service.py)
- ✅ **BasicAuth**: Добавлен `httpx.BasicAuth` если `SEARXNG_USER` и `SEARXNG_PASSWORD` заданы
- ✅ **User-Agent**: Изменен на `"TelegramAssistant/3.1 (RAG Hybrid Search)"`
- ✅ **HTTP Client**: Auth и headers настроены при инициализации

### 3. Environment (env.example)
Добавлены переменные:
```bash
SEARXNG_USER=
SEARXNG_PASSWORD=
```

## 🔍 Использование

### Без BasicAuth (по умолчанию)
Если `SEARXNG_USER` и `SEARXNG_PASSWORD` не заданы, SearXNG работает без аутентификации.

### С BasicAuth
**ВАЖНО**: BasicAuth нужен ТОЛЬКО если используется внешний защищенный SearXNG инстанс (например, `https://searxng.produman.studio`).

**Для локального SearXNG контейнера** (который используется в этом проекте):
- ✅ BasicAuth НЕ требуется
- ✅ Оставьте переменные пустыми: `SEARXNG_USER=` и `SEARXNG_PASSWORD=`
- ✅ Локальный контейнер работает без аутентификации

**Если используете внешний SearXNG с BasicAuth**:
1. Обратитесь к администратору внешнего инстанса
2. Получите учетные данные (username/password)
3. Установите в `.env`:
   ```bash
   SEARXNG_URL=https://searxng.produman.studio
   SEARXNG_USER=your-username
   SEARXNG_PASSWORD=your-password
   ```

## 📝 Context7 Best Practice

**User-Agent**: Уникальный User-Agent `"TelegramAssistant/3.1 (RAG Hybrid Search)"` помогает обойти bot detection.

**BasicAuth**: Опциональная аутентификация для защищенных инстансов SearXNG.

## ✅ Проверка

```bash
# Тест через Python
docker compose exec api python3 -c "
import asyncio
from services.searxng_service import get_searxng_service

async def test():
    service = get_searxng_service()
    result = await service.search('Python', user_id='test')
    print(f'Results: {len(result.results)}')

asyncio.run(test())
"
```

## ✅ Итог

**SearXNG обновлен:**
- ✅ BasicAuth поддержка
- ✅ User-Agent обновлен
- ✅ HTTP Client настроен корректно

