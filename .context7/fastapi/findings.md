# Context7 Findings: FastAPI Best Practices

## Исследованные темы

### 1. CORS Configuration
**Проблема**: Неправильная настройка CORS может привести к проблемам безопасности.

**Context7 Query**: "FastAPI CORS security best practices whitelist origins"

**Найденные практики**:
- Строгий whitelist разрешённых origins
- Запрет wildcard (*) для credentials
- Настройка через переменные окружения
- Валидация origins на уровне middleware

**Источник**: Context7 FastAPI security documentation, OWASP guidelines

### 2. Security Headers
**Проблема**: Отсутствие security headers делает приложение уязвимым.

**Context7 Query**: "FastAPI security headers CSP HSTS X-Frame-Options"

**Найденные практики**:
- `Strict-Transport-Security` для HTTPS
- `Content-Security-Policy` для XSS защиты
- `X-Frame-Options: DENY` против clickjacking
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: no-referrer`

**Источник**: Context7 FastAPI security documentation, OWASP security headers

### 3. Rate Limiting
**Проблема**: Отсутствие rate limiting может привести к DDoS атакам.

**Context7 Query**: "FastAPI rate limiting Redis middleware best practices"

**Найденные практики**:
- Redis-based rate limiting с sliding window
- Разные лимиты для разных endpoints
- IP-based и user-based лимиты
- Graceful degradation при превышении лимитов

**Источник**: Context7 FastAPI rate limiting documentation, Redis patterns

### 4. JWT Security
**Проблема**: Неправильная обработка JWT токенов может привести к уязвимостям.

**Context7 Query**: "FastAPI JWT security best practices token validation"

**Найденные практики**:
- Валидация audience (aud) claims
- Проверка expiration (exp) claims
- Использование HMAC для подписи
- Короткое время жизни токенов
- Refresh token механизм

**Источник**: Context7 FastAPI JWT documentation, OAuth2 security guidelines

## Приоритеты внедрения

1. **Критично**: Security headers (защита от атак)
2. **Высокий**: CORS whitelist (предотвращение CSRF)
3. **Высокий**: Rate limiting (защита от DDoS)
4. **Средний**: JWT security (улучшение аутентификации)

## Метрики для отслеживания

- `http_requests_total{method, endpoint, status}` - HTTP запросы
- `rate_limit_hits_total{endpoint, ip}` - rate limit срабатывания
- `jwt_validation_failures_total{reason}` - JWT ошибки
- `security_header_violations_total{header}` - нарушения security headers
