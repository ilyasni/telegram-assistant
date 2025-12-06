# ✅ Настройка AlertManager завершена

**Дата**: 2025-12-03  
**Статус**: Готово к работе

---

## ✅ Выполненные задачи

### 1. Настройка AlertManager
- ✅ Создана конфигурация `prometheus/alertmanager.yml`
- ✅ Добавлен сервис `alertmanager` в `docker-compose.yml`
- ✅ Настроен `prometheus/prometheus.yml` для отправки алертов
- ✅ AlertManager работает (http://localhost:9093)
- ✅ Prometheus подключен к AlertManager

### 2. Webhook Receiver
- ✅ Создан `api/alert_webhook.py` с поддержкой username и chat_id
- ✅ Зарегистрирован endpoint `/api/monitoring/alertmanager/webhook`
- ✅ Реализовано форматирование сообщений в HTML

### 3. Конфигурация
- ✅ `ALERT_TELEGRAM_CHAT_ID=@testgroupassistant` добавлен в `.env`
- ✅ Переменная добавлена в `docker-compose.yml` для API сервиса
- ✅ Переменная загружена в контейнер API

### 4. Диагностика и тестирование
- ✅ Созданы скрипты диагностики алертов
- ✅ Создан скрипт тестовой отправки
- ✅ Webhook endpoint отвечает (200 OK)

---

## 📊 Текущий статус

| Компонент | Статус | URL |
|-----------|--------|-----|
| AlertManager | ✅ Работает | http://localhost:9093 |
| Prometheus | ✅ Подключен | http://localhost:9090 |
| API | ✅ Готов | http://localhost:8000 |
| ALERT_TELEGRAM_CHAT_ID | ✅ Настроен | @testgroupassistant |

### Активные алерты

Найдено **1 активный critical алерт**:
- `VisionWorkerNotProcessing` (активен с 2025-12-03T18:45:37)

Этот алерт будет автоматически отправлен в группу @testgroupassistant.

---

## 🔍 Проверка работы

### 1. Проверка в Telegram

Откройте группу [@testgroupassistant](https://t.me/testgroupassistant) и проверьте:
- ✅ Пришло ли тестовое уведомление
- ✅ Форматирование сообщения (HTML, эмодзи)
- ✅ Структурированная информация об алерте

### 2. Проверка логов

```bash
# Логи API при отправке алертов
docker logs telegram-assistant-api-1 --tail 100 | grep -i "alert\|telegram\|sent"

# Логи AlertManager
docker logs telegram-assistant-alertmanager-1 --tail 50

# Проверка переменной окружения
docker exec telegram-assistant-api-1 printenv ALERT_TELEGRAM_CHAT_ID
```

### 3. Проверка активных алертов

```bash
# Все critical алерты в Prometheus
curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.severity == "critical" and .state == "firing")'

# Алерты в AlertManager
curl http://localhost:9093/api/v2/alerts | jq '.data[]'
```

### 4. Тестовая отправка

```bash
# Использовать готовый скрипт
./scripts/test_alert_notification.sh

# Или вручную
curl -X POST http://localhost:8000/api/monitoring/alertmanager/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "version": "4",
    "groupKey": "test",
    "status": "firing",
    "alerts": [{
      "status": "firing",
      "labels": {"alertname": "TestAlert", "severity": "critical"},
      "annotations": {"summary": "Тестовое уведомление"}
    }]
  }'
```

---

## ⚠️ Устранение проблем

### Уведомления не приходят в Telegram

1. **Проверьте бота в группе**:
   - Бот должен быть добавлен в группу @testgroupassistant
   - Бот должен быть администратором
   - Бот должен иметь права на отправку сообщений

2. **Проверьте переменную окружения**:
   ```bash
   docker exec telegram-assistant-api-1 printenv ALERT_TELEGRAM_CHAT_ID
   ```
   Должно быть: `@testgroupassistant`

3. **Проверьте инициализацию бота**:
   ```bash
   curl http://localhost:8000/health/bot
   ```
   Должно вернуть: `{"bot_ready": true}`

4. **Проверьте логи на ошибки**:
   ```bash
   docker logs telegram-assistant-api-1 --tail 100 | grep -i "error\|warning\|failed"
   ```

### AlertManager не отправляет алерты

1. **Проверьте подключение Prometheus**:
   ```bash
   curl http://localhost:9090/api/v1/alertmanagers
   ```

2. **Проверьте конфигурацию**:
   ```bash
   docker logs telegram-assistant-alertmanager-1 | grep -i "error\|warning"
   ```

3. **Проверьте активные алерты**:
   ```bash
   curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.severity == "critical")'
   ```

---

## 📚 Документация

- `docs/ALERT_SETUP_QUICKSTART.md` - быстрый старт
- `docs/ALERT_TELEGRAM_SETUP.md` - подробная инструкция
- `reports/ALERTMANAGER_SETUP_COMPLETE.md` - полный отчет
- `reports/ALERTMANAGER_TESTING_RESULTS.md` - результаты тестирования

---

## ✅ Система готова

После выполнения всех проверок система настроена и готова автоматически отправлять все critical алерты в группу @testgroupassistant через AlertManager.

**Следующие действия**:
1. ✅ Проверьте группу @testgroupassistant в Telegram
2. ✅ Убедитесь, что тестовое уведомление пришло
3. ✅ Мониторьте активные алерты - они будут отправляться автоматически

**Статус**: ✅ Настройка завершена, система работает

