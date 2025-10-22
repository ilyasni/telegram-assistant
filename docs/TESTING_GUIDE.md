# Руководство по тестированию Telegram Assistant

## Обзор

Данное руководство описывает стратегию тестирования Telegram Assistant, включая E2E сценарии, нагрузочное тестирование и chaos engineering.

## Типы тестирования

### 1. E2E (End-to-End) тестирование
### 2. Нагрузочное тестирование
### 3. Chaos Engineering
### 4. Безопасность тестирование

---

## E2E тестирование

### Сценарии QR-авторизации

#### Сценарий 1: Успешная авторизация с валидным инвайтом
```bash
# Предусловия
- Создан валидный инвайт-код
- Telegram бот запущен
- API сервисы работают

# Шаги
1. Отправить команду /login VALID_INVITE_CODE
2. Проверить, что бот отвечает "Инвайт-код принят"
3. Нажать кнопку "Открыть Mini App"
4. Сканировать QR-код в Telegram
5. Подтвердить авторизацию

# Ожидаемый результат
- Статус сессии: "authorized"
- Пользователь создан в БД
- Сессия сохранена в зашифрованном виде
- Событие user_authorized опубликовано
```

#### Сценарий 2: Невалидный инвайт-код
```bash
# Шаги
1. Отправить команду /login INVALID_CODE
2. Проверить ответ бота

# Ожидаемый результат
- Бот отвечает "Неверный инвайт-код"
- QR-сессия НЕ создается
- Нет запросов к API
```

#### Сценарий 3: Просроченный инвайт-код
```bash
# Предусловия
- Создан инвайт-код с истекшим сроком

# Шаги
1. Отправить команду /login EXPIRED_CODE
2. Проверить ответ бота

# Ожидаемый результат
- Бот отвечает "Инвайт-код истёк"
- QR-сессия НЕ создается
```

#### Сценарий 4: QR timeout (>600s)
```bash
# Шаги
1. Создать QR-сессию
2. НЕ сканировать QR-код в течение 10+ минут
3. Проверить статус сессии

# Ожидаемый результат
- Статус сессии: "expired"
- Сессия удалена из Redis
- Нет утечек ресурсов
```

#### Сценарий 5: Повторный логин (идемпотентность)
```bash
# Шаги
1. Выполнить успешную авторизацию
2. Повторно отправить /login VALID_CODE
3. Проверить поведение

# Ожидаемый результат
- Возвращается существующая сессия
- НЕ создается новая QR-сессия
- Идемпотентность соблюдена
```

#### Сценарий 6: Прерванный flow
```bash
# Шаги
1. Создать QR-сессию
2. Закрыть Mini App без сканирования
3. Проверить cleanup

# Ожидаемый результат
- Сессия корректно очищена
- Нет зависших процессов
- Ресурсы освобождены
```

### Сценарии API

#### Сценарий 7: Rate limiting
```bash
# Шаги
1. Отправить 10+ запросов /tg/qr/start в минуту
2. Проверить ответы

# Ожидаемый результат
- Первые 5 запросов: 200 OK
- Остальные: 429 Too Many Requests
- Rate limit headers присутствуют
```

#### Сценарий 8: Security headers
```bash
# Шаги
1. Отправить запрос к любому endpoint
2. Проверить заголовки ответа

# Ожидаемый результат
- Strict-Transport-Security присутствует
- Content-Security-Policy присутствует
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
```

---

## Нагрузочное тестирование

### Инструменты
- **Artillery**: для HTTP нагрузочного тестирования
- **Locust**: для более сложных сценариев
- **k6**: для CI/CD интеграции

### Сценарии нагрузки

#### Сценарий 1: Параллельные QR-сессии
```yaml
# artillery-config.yml
config:
  target: 'https://your-domain.com'
  phases:
    - duration: 60
      arrivalRate: 10
      name: "Ramp up"
    - duration: 300
      arrivalRate: 20
      name: "Sustained load"
scenarios:
  - name: "QR Auth Flow"
    weight: 100
    flow:
      - post:
          url: "/api/tg/qr/start"
          json:
            telegram_user_id: "{{ $randomInt(100000000, 999999999) }}"
            invite_code: "{{ $randomString() }}"
      - loop:
          - get:
              url: "/api/tg/qr/status/{{ sessionToken }}"
          - think: 5
        count: 12
```

**SLA требования:**
- P95 response time < 300ms
- Success rate > 95%
- No memory leaks
- CPU usage < 80%

#### Сценарий 2: RAG запросы под нагрузкой
```yaml
config:
  target: 'https://your-domain.com'
  phases:
    - duration: 120
      arrivalRate: 5
scenarios:
  - name: "RAG Query"
    weight: 100
    flow:
      - post:
          url: "/api/rag/query"
          headers:
            Authorization: "Bearer {{ jwt_token }}"
          json:
            query: "{{ $randomString() }}"
            user_id: "{{ $randomUUID() }}"
```

#### Сценарий 3: Смешанная нагрузка
```yaml
config:
  target: 'https://your-domain.com'
  phases:
    - duration: 600
      arrivalRate: 15
scenarios:
  - name: "QR Auth"
    weight: 40
    # ... QR flow
  - name: "RAG Query"
    weight: 30
    # ... RAG flow
  - name: "Channel Management"
    weight: 20
    # ... Channel operations
  - name: "User Management"
    weight: 10
    # ... User operations
```

### Метрики для мониторинга

#### Системные метрики
- **CPU usage**: < 80%
- **Memory usage**: < 85%
- **Disk I/O**: < 1000 IOPS
- **Network I/O**: < 100 Mbps

#### Прикладные метрики
- **Response time P95**: < 300ms
- **Response time P99**: < 1000ms
- **Error rate**: < 1%
- **Throughput**: > 100 req/s

#### Бизнес метрики
- **QR success rate**: > 90%
- **Session cleanup time**: < 3s
- **FloodWait rate**: < 5%

---

## Chaos Engineering

### Сценарии хаоса

#### Сценарий 1: Redis restart
```bash
# Шаги
1. Запустить активные QR-сессии
2. Перезапустить Redis: docker compose restart redis
3. Проверить восстановление

# Ожидаемый результат
- Новые сессии создаются корректно
- Старые сессии очищаются
- Нет зависших процессов
- Метрики восстанавливаются
```

#### Сценарий 2: Qdrant restart
```bash
# Шаги
1. Выполнить RAG запросы
2. Перезапустить Qdrant: docker compose restart qdrant
3. Проверить RAG функциональность

# Ожидаемый результат
- RAG запросы восстанавливаются
- Векторные данные не теряются
- Нет ошибок в логах
```

#### Сценарий 3: API restart
```bash
# Шаги
1. Выполнить активные запросы
2. Перезапустить API: docker compose restart api
3. Проверить обработку запросов

# Ожидаемый результат
- Активные запросы завершаются корректно
- Новые запросы обрабатываются
- Health checks проходят
```

#### Сценарий 4: Telethon restart
```bash
# Шаги
1. Запустить QR-сессии
2. Перезапустить telethon-ingest
3. Проверить QR функциональность

# Ожидаемый результат
- QR-сессии восстанавливаются
- Telethon клиенты переподключаются
- Нет утечек сессий
```

#### Сценарий 5: Network partition
```bash
# Шаги
1. Заблокировать сеть между сервисами
2. Проверить graceful degradation
3. Восстановить сеть
4. Проверить восстановление

# Ожидаемый результат
- Сервисы переходят в degraded mode
- Health checks показывают проблемы
- Восстановление происходит автоматически
```

### Инструменты для хаоса
- **Chaos Monkey**: для случайных сбоев
- **Network Chaos**: для сетевых проблем
- **Resource Chaos**: для ограничения ресурсов

---

## Безопасность тестирование

### Сценарии безопасности

#### Сценарий 1: SQL Injection
```bash
# Шаги
1. Отправить запросы с SQL injection payloads
2. Проверить ответы и логи

# Ожидаемый результат
- Запросы отклоняются
- Нет SQL ошибок в логах
- Параметры экранированы
```

#### Сценарий 2: XSS атаки
```bash
# Шаги
1. Отправить запросы с XSS payloads
2. Проверить ответы

# Ожидаемый результат
- Payloads экранированы
- CSP заголовки блокируют выполнение
- Нет XSS в ответах
```

#### Сценарий 3: CSRF атаки
```bash
# Шаги
1. Попытаться выполнить CSRF атаку
2. Проверить защиту

# Ожидаемый результат
- CSRF токены проверяются
- CORS настроен корректно
- SameSite cookies используются
```

#### Сценарий 4: Rate limiting bypass
```bash
# Шаги
1. Попытаться обойти rate limiting
2. Проверить защиту

# Ожидаемый результат
- Rate limiting работает
- IP блокируется при превышении
- Логируются попытки обхода
```

#### Сценарий 5: JWT атаки
```bash
# Шаги
1. Попытаться подделать JWT
2. Проверить валидацию

# Ожидаемый результат
- JWT подписи проверяются
- Audience валидируется
- Expired токены отклоняются
```

---

## Автоматизация тестирования

### CI/CD Pipeline

#### GitHub Actions
```yaml
name: Testing Pipeline
on: [push, pull_request]

jobs:
  e2e-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - name: Setup Docker
        run: docker compose up -d
      - name: Wait for services
        run: sleep 30
      - name: Run E2E tests
        run: npm run test:e2e
      - name: Run load tests
        run: npm run test:load
      - name: Run chaos tests
        run: npm run test:chaos
```

#### Локальное тестирование
```bash
# Запуск всех тестов
make test

# Запуск E2E тестов
make test-e2e

# Запуск нагрузочных тестов
make test-load

# Запуск chaos тестов
make test-chaos
```

### Мониторинг тестов

#### Метрики тестирования
- **Test success rate**: > 95%
- **Test execution time**: < 10 minutes
- **Flaky test rate**: < 5%
- **Coverage**: > 80%

#### Алерты на тесты
- Test failures
- Performance degradation
- Security violations
- Infrastructure issues

---

## Критерии приёмки

### Функциональные требования
- ✅ Все E2E сценарии проходят
- ✅ API контракты соблюдены
- ✅ Безопасность обеспечена
- ✅ Производительность соответствует SLA

### Нефункциональные требования
- ✅ Нагрузочное тестирование пройдено
- ✅ Chaos engineering успешен
- ✅ Мониторинг настроен
- ✅ Документация актуальна

### Метрики качества
- **Availability**: > 99.9%
- **Performance**: P95 < 300ms
- **Security**: 0 критических уязвимостей
- **Reliability**: < 1% error rate

---

## Troubleshooting

### Частые проблемы

#### Проблема: QR-сессии не создаются
```bash
# Диагностика
1. Проверить логи telethon-ingest
2. Проверить Redis подключение
3. Проверить Telegram API credentials
4. Проверить rate limiting

# Решение
- Убедиться, что все сервисы запущены
- Проверить .env переменные
- Очистить Redis кэш
```

#### Проблема: Высокий FloodWait rate
```bash
# Диагностика
1. Проверить метрики FloodWait
2. Проверить параллелизм Telethon
3. Проверить rate limiting

# Решение
- Уменьшить параллелизм
- Увеличить backoff delays
- Проверить Telegram API limits
```

#### Проблема: Медленные ответы API
```bash
# Диагностика
1. Проверить метрики response time
2. Проверить нагрузку на БД
3. Проверить Redis производительность

# Решение
- Оптимизировать запросы к БД
- Увеличить Redis memory
- Проверить индексы БД
```

### Логи для анализа

#### Критические логи
```bash
# API ошибки
grep "ERROR" api/logs/app.log

# Telethon ошибки
grep "ERROR" telethon-ingest/logs/app.log

# Security нарушения
grep "SECURITY" api/logs/security.log
```

#### Метрики для анализа
```bash
# Prometheus метрики
curl http://localhost:8000/metrics

# Grafana дашборды
http://localhost:3000/d/qr-auth-funnel
```

---

## Заключение

Данное руководство обеспечивает комплексное тестирование Telegram Assistant на всех уровнях - от функционального до chaos engineering. Регулярное выполнение всех типов тестов гарантирует высокое качество и надёжность системы.
