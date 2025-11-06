# Инструкция по настройке новых функций: RAG, Digest, Trends, Voice

## Обзор

Реализованы следующие компоненты:
- **RAG Service** - интеллектуальный поиск и ответы на вопросы
- **Digest Service** - генерация дайджестов по пользовательским темам
- **Trend Detection** - обнаружение трендов через multi-agent систему
- **Voice Transcription** - транскрибация голосовых сообщений через SaluteSpeech

## Шаг 1: Применение миграций БД

```bash
cd api
alembic upgrade head
```

Это создаст:
- `engagement_score` в таблице `posts` (computed column)
- `album_size`, `vision_labels_agg`, `ocr_present` в `post_enrichment`
- Новые таблицы:
  - `digest_settings` - настройки дайджестов пользователей
  - `digest_history` - история сгенерированных дайджестов
  - `rag_query_history` - история RAG запросов
  - `trends_detection` - обнаруженные тренды
  - `trend_alerts` - уведомления пользователей о трендах

## Шаг 2: Проверка переменных окружения

Убедитесь, что в `.env` заполнены:

### GigaChat (для RAG, Intent, Digest, Trends)
```bash
GIGACHAT_CREDENTIALS=your_credentials_here
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_PROXY_URL=http://gpt2giga-proxy:8090
OPENAI_API_BASE=http://gpt2giga-proxy:8090/v1
OPENAI_API_KEY=dummy
```

### SaluteSpeech (для транскрибации)
```bash
SALUTESPEECH_CLIENT_ID=your_client_id
SALUTESPEECH_CLIENT_SECRET=your_client_secret
SALUTESPEECH_SCOPE=SALUTE_SPEECH_PERS
SALUTESPEECH_URL=https://smartspeech.sber.ru/rest/v1
```

### SearXNG (для внешнего поиска)
```bash
SEARXNG_URL=http://searxng:8080
SEARXNG_ENABLED=true
```

### Qdrant (для векторного поиска)
```bash
QDRANT_URL=http://qdrant:6333
```

## Шаг 3: Запуск сервисов

```bash
# Основные сервисы
docker-compose --profile core up -d

# С SearXNG для внешнего поиска
docker-compose --profile rag up -d
```

## Шаг 4: Тестирование

### 4.1. Тестирование RAG через бота

1. Отправьте текстовое сообщение боту (например: "Что нового в AI?")
2. Бот автоматически определит намерение и обработает через RAG
3. Проверьте ответ с источниками

### 4.2. Тестирование голосовых сообщений

1. Отправьте голосовое сообщение боту
2. Бот транскрибирует через SaluteSpeech
3. Обработает транскрибированный текст через RAG

### 4.3. Тестирование API

#### RAG запрос
```bash
curl -X POST http://localhost:8000/api/rag/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Что нового в искусственном интеллекте?",
    "user_id": "your-user-id"
  }'
```

#### Настройка дайджеста
```bash
curl -X PUT http://localhost:8000/api/digest/settings/{user_id} \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "topics": ["AI", "машинное обучение"],
    "frequency": "daily",
    "schedule_time": "09:00",
    "schedule_tz": "Europe/Moscow",
    "max_items_per_digest": 10
  }'
```

#### Генерация дайджеста
```bash
curl -X POST http://localhost:8000/api/digest/generate/{user_id}
```

#### Обнаружение трендов
```bash
curl -X POST http://localhost:8000/api/trends/detect?days=7
```

#### Получение трендов
```bash
curl "http://localhost:8000/api/trends/?min_frequency=10&min_engagement=5.0"
```

## Шаг 5: Проверка работы Scheduled Tasks

### Дайджесты
- Запускаются каждые 15 минут
- Проверяют расписание пользователей по их локальному времени
- Генерируют дайджесты только для пользователей с `enabled=true` и `topics` не пустым

### Тренды
- Запускаются ежедневно в 00:00 UTC
- Анализируют все посты за последние 7 дней
- Сохраняют тренды в таблицу `trends_detection`

## Логи и мониторинг

```bash
# Логи API
docker-compose logs -f api

# Логи Scheduler
docker-compose logs api | grep -i scheduler

# Проверка health
curl http://localhost:8000/api/health
```

## Troubleshooting

### Проблема: Миграции не применяются
```bash
# Проверьте подключение к БД
docker-compose exec postgres psql -U telegram_user -d postgres -c "SELECT 1;"

# Проверьте текущую версию миграций
cd api && alembic current

# Примените миграции вручную
cd api && alembic upgrade head
```

### Проблема: RAG не работает
- Проверьте, что Qdrant запущен: `docker-compose ps qdrant`
- Проверьте, что есть индексированные посты в Qdrant
- Проверьте логи: `docker-compose logs api | grep -i rag`

### Проблема: Голосовые сообщения не транскрибируются
- Проверьте `SALUTESPEECH_CLIENT_ID` и `SALUTESPEECH_CLIENT_SECRET`
- Проверьте `VOICE_TRANSCRIPTION_ENABLED=true`
- Проверьте логи: `docker-compose logs api | grep -i voice`

### Проблема: Дайджесты не генерируются
- Убедитесь, что у пользователя настроены `topics` (обязательно для `enabled=true`)
- Проверьте расписание: должно быть в пределах ±5 минут от текущего времени
- Проверьте логи: `docker-compose logs api | grep -i digest`

## Дополнительная информация

- Все сервисы используют GigaChat через `gpt2giga-proxy` (без локальных моделей)
- Кэширование реализовано через Redis
- Scheduler запускается автоматически при старте API сервиса
- Все переменные окружения описаны в `env.example`

