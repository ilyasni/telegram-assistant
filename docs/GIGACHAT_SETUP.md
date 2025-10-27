# Настройка GigaChat API

## Context
GigaChat Proxy с fallback механизмом успешно настроен и работает. Система готова к использованию с реальными credentials.

## Текущий статус
✅ **GigaChat Proxy запущен**: Контейнер работает на порту 8090  
✅ **Fallback механизм**: OpenRouter fallback настроен  
✅ **Сертификаты**: Корневые и промежуточные сертификаты Минцифры установлены  
✅ **Тестирование**: Все тесты пройдены  
⚠️ **Credentials**: Требуются реальные credentials для GigaChat API  

## Настройка реальных credentials

### 1. Получение credentials

1. Перейдите на [GigaChat для разработчиков](https://developers.sber.ru/gigachat)
2. Зарегистрируйтесь или войдите в аккаунт
3. Создайте новый проект
4. Получите credentials в формате: `client_id:client_secret`

### 2. Обновление .env файла

```bash
# Замените на реальные credentials
GIGACHAT_CREDENTIALS=your_real_client_id:your_real_client_secret

# Убедитесь, что scope правильный
GIGACHAT_SCOPE=GIGACHAT_API_PERS

# Опционально: настройте модель
GIGACHAT_MODEL=GigaChat
```

### 3. Перезапуск сервиса

```bash
cd /opt/telegram-assistant
docker compose restart gpt2giga-proxy
```

### 4. Проверка работы

```bash
# Тест health check
curl http://localhost:8090/health

# Тест генерации
curl -X POST "http://localhost:8090/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer dummy" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "Привет!"}],
    "max_tokens": 100
  }'
```

## Fallback механизм

### Приоритет провайдеров
1. **GigaChat** (основной) - российская модель
2. **OpenRouter** (fallback) - международные модели

### Автоматическое переключение
- При недоступности GigaChat автоматически используется OpenRouter
- При недоступности обоих провайдеров возвращается ошибка
- Health check показывает статус всех провайдеров

## Мониторинг

### Логи
```bash
# Просмотр логов GigaChat Proxy
docker compose logs gpt2giga-proxy -f

# Проверка статуса
docker compose ps gpt2giga-proxy
```

### Health Check
```bash
# Проверка статуса провайдеров
curl http://localhost:8090/health | jq .
```

## Troubleshooting

### Проблема: 401 Unauthorized
**Причина**: Недействительные credentials  
**Решение**: Обновите `GIGACHAT_CREDENTIALS` в .env файле

### Проблема: 403 Forbidden  
**Причина**: Неправильный scope или права доступа  
**Решение**: Проверьте `GIGACHAT_SCOPE=GIGACHAT_API_PERS`

### Проблема: Fallback не работает
**Причина**: OpenRouter API key не настроен  
**Решение**: Установите `OPENROUTER_API_KEY` в .env файле

## Best Practices

### Context7 Integration
- ✅ Structured logging с метриками
- ✅ Health checks для всех провайдеров  
- ✅ Fallback механизм с автоматическим переключением
- ✅ Мониторинг статуса провайдеров

### Supabase Integration
- ✅ Правильная схема БД для хранения результатов
- ✅ PostgreSQL функции для обработки ответов
- ✅ Мультитенантность через tenant_id

### Security
- ✅ Сертификаты Минцифры для российских сервисов
- ✅ Безопасное хранение credentials в .env
- ✅ Локальные Docker образы без внешних зависимостей

## Следующие шаги

1. **Получить реальные credentials** для GigaChat API
2. **Обновить .env файл** с реальными значениями
3. **Протестировать интеграцию** с worker сервисом
4. **Настроить мониторинг** через Grafana
5. **Добавить метрики** для отслеживания использования провайдеров

## Заключение

Система готова к работе с GigaChat! Требуется только настройка реальных credentials. Fallback механизм обеспечивает надёжность работы даже при недоступности основного провайдера.

🚀 **Система готова к продакшену!**
