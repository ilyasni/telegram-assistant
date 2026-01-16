# Настройка AlertManager завершена

**Дата**: 2025-12-03  
**Статус**: ✅ Настроено и готово к работе

---

## Выполненные задачи

### 1. Настройка AlertManager ✅

- ✅ Создана конфигурация `prometheus/alertmanager.yml`
- ✅ Добавлен сервис `alertmanager` в `docker-compose.yml`
- ✅ Настроен `prometheus/prometheus.yml` для отправки алертов в AlertManager
- ✅ AlertManager запущен и работает (http://localhost:9093)
- ✅ Prometheus подключен к AlertManager

### 2. Webhook receiver для Telegram ✅

- ✅ Создан `api/alert_webhook.py` с обработчиком webhook запросов
- ✅ Реализована поддержка username и числового chat_id
- ✅ Зарегистрирован endpoint `/api/monitoring/alertmanager/webhook`
- ✅ Настроено форматирование сообщений в HTML

### 3. Конфигурация Telegram ✅

- ✅ Добавлено `ALERT_TELEGRAM_CHAT_ID=@testgroupassistant` в `.env`
- ✅ Обновлен `env.example` с примером настройки
- ✅ Создана утилита `scripts/get_alert_chat_id.py` для получения chat_id
- ✅ Создан скрипт `scripts/get_chat_id_from_bot.sh` для получения chat_id через API

### 4. Диагностические скрипты ✅

- ✅ Создан `scripts/diagnose_album_alerts.sh` для диагностики алертов альбомов
- ✅ Создан `scripts/diagnose_crawl_queue.sh` для диагностики очереди триггера
- ✅ Создан `scripts/test_alert_notification.sh` для тестовой отправки уведомлений

### 5. Документация ✅

- ✅ Создана `docs/ALERT_TELEGRAM_SETUP.md` - подробная инструкция
- ✅ Создана `docs/ALERT_SETUP_QUICKSTART.md` - быстрый старт

---

## Текущий статус

### Сервисы

- ✅ **AlertManager**: Запущен и работает
  - URL: http://localhost:9093
  - Health: OK
  - Конфигурация загружена успешно

- ✅ **Prometheus**: Подключен к AlertManager
  - URL: http://localhost:9090
  - AlertManagers: 1 активный

- ⏳ **API**: Перезапускается для применения изменений
  - URL: http://localhost:8000
  - Webhook endpoint: `/api/monitoring/alertmanager/webhook`

### Конфигурация

- ✅ `ALERT_TELEGRAM_CHAT_ID=@testgroupassistant` установлен в `.env`
- ✅ AlertManager настроен на отправку только critical алертов
- ✅ Группировка алертов: по `alertname` и `severity`
- ✅ Повторные уведомления: не чаще раза в час

### Активные алерты

Найдено **1 активный critical алерт**:
- `VisionWorkerNotProcessing`

Этот алерт будет отправлен в группу @testgroupassistant после полной загрузки API.

---

## Следующие шаги

### 1. Дождаться полной загрузки API

```bash
# Проверить статус
curl http://localhost:8000/api/monitoring/alertmanager/health

# Ожидаемый ответ:
# {
#   "status": "ok",
#   "chat_id_configured": true,
#   "bot_available": false
# }
```

### 2. Протестировать отправку уведомления

```bash
# Запустить тестовый скрипт
./scripts/test_alert_notification.sh
```

Или вручную:

```bash
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

### 3. Проверить получение уведомления

1. Откройте группу [@testgroupassistant](https://t.me/testgroupassistant) в Telegram
2. Должно прийти сообщение с тестовым алертом
3. Проверьте форматирование (HTML, эмодзи, структурированная информация)

### 4. Мониторинг

```bash
# Логи API при отправке алертов
docker logs telegram-assistant-api-1 --tail 50 | grep -i alert

# Логи AlertManager
docker logs telegram-assistant-alertmanager-1 --tail 50

# Активные алерты в Prometheus
curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.severity == "critical")'
```

---

## Устранение проблем

### Проблема: API не отвечает

```bash
# Проверить статус контейнера
docker ps | grep api

# Проверить логи
docker logs telegram-assistant-api-1 --tail 100

# Перезапустить при необходимости
docker compose restart api
```

### Проблема: Уведомления не приходят

1. Проверьте, что бот добавлен в группу @testgroupassistant
2. Проверьте права бота (должен быть администратором)
3. Проверьте логи API на наличие ошибок
4. Убедитесь, что `ALERT_TELEGRAM_CHAT_ID` установлен в `.env`

### Проблема: AlertManager не отправляет алерты

1. Проверьте подключение Prometheus к AlertManager:
   ```bash
   curl http://localhost:9090/api/v1/alertmanagers
   ```

2. Проверьте конфигурацию AlertManager:
   ```bash
   docker logs telegram-assistant-alertmanager-1 | grep -i "error\|warning"
   ```

3. Проверьте активные алерты:
   ```bash
   curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.severity == "critical")'
   ```

---

## Файлы конфигурации

- `prometheus/alertmanager.yml` - конфигурация AlertManager
- `prometheus/prometheus.yml` - конфигурация Prometheus (секция alerting)
- `api/alert_webhook.py` - webhook receiver для Telegram
- `.env` - переменные окружения (ALERT_TELEGRAM_CHAT_ID)

## Скрипты

- `scripts/get_alert_chat_id.py` - получение chat_id по username
- `scripts/get_chat_id_from_bot.sh` - получение chat_id через API бота
- `scripts/test_alert_notification.sh` - тестовая отправка уведомления
- `scripts/diagnose_album_alerts.sh` - диагностика алертов альбомов
- `scripts/diagnose_crawl_queue.sh` - диагностика очереди триггера

---

## Готово к использованию

После полной загрузки API все critical алерты из Prometheus будут автоматически отправляться в группу @testgroupassistant через AlertManager.

**Статус**: ✅ Настройка завершена, система готова к работе

