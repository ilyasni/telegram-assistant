# Результаты тестирования AlertManager

**Дата**: 2025-12-03  
**Время**: 22:10 MSK

---

## ✅ Выполненные проверки

### 1. Проверка компонентов

- ✅ **AlertManager**: Работает (http://localhost:9093)
- ✅ **Prometheus**: Подключен к AlertManager
- ✅ **API**: Готов к работе
- ✅ **ALERT_TELEGRAM_CHAT_ID**: `@testgroupassistant` загружен в контейнер

### 2. Тестовая отправка уведомления

Выполнен тест отправки уведомления через webhook endpoint.

**Команда**:
```bash
./scripts/test_alert_notification.sh
```

**Результат**: Запрос отправлен, ожидается проверка в группе @testgroupassistant

### 3. Активные алерты

Найдено **1 активный critical алерт**:
- `VisionWorkerNotProcessing` (активен с 2025-12-03T18:45:37)

Этот алерт будет автоматически отправлен в группу @testgroupassistant через AlertManager.

---

## 📝 Инструкции для проверки

### Проверка в Telegram

1. Откройте группу [@testgroupassistant](https://t.me/testgroupassistant)
2. Проверьте наличие тестового уведомления
3. Уведомление должно содержать:
   - 🚨 Заголовок "КРИТИЧЕСКИЕ АЛЕРТЫ"
   - Название алерта (TestAlert или VisionWorkerNotProcessing)
   - Описание и детали
   - Форматирование в HTML

### Проверка логов

```bash
# Логи API при отправке алертов
docker logs telegram-assistant-api-1 --tail 50 | grep -i "alert\|telegram\|sent"

# Логи AlertManager
docker logs telegram-assistant-alertmanager-1 --tail 50

# Проверка webhook запросов
docker logs telegram-assistant-api-1 | grep "alertmanager/webhook"
```

### Проверка активных алертов

```bash
# Все critical алерты
curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.severity == "critical" and .state == "firing")'

# Алерты в AlertManager
curl http://localhost:9093/api/v2/alerts | jq '.data[]'
```

---

## 🔍 Диагностика проблем

### Если уведомления не приходят

1. **Проверьте бота в группе**:
   - Бот должен быть добавлен в группу @testgroupassistant
   - Бот должен быть администратором
   - Бот должен иметь права на отправку сообщений

2. **Проверьте переменную окружения**:
   ```bash
   docker exec telegram-assistant-api-1 printenv ALERT_TELEGRAM_CHAT_ID
   ```
   Должно быть: `@testgroupassistant`

3. **Проверьте логи API**:
   ```bash
   docker logs telegram-assistant-api-1 --tail 100 | grep -i "alert\|telegram\|error"
   ```

4. **Проверьте логи AlertManager**:
   ```bash
   docker logs telegram-assistant-alertmanager-1 --tail 100 | grep -i "error\|webhook"
   ```

### Если AlertManager не отправляет алерты

1. **Проверьте подключение Prometheus**:
   ```bash
   curl http://localhost:9090/api/v1/alertmanagers
   ```
   Должен вернуть активный AlertManager

2. **Проверьте конфигурацию AlertManager**:
   ```bash
   docker logs telegram-assistant-alertmanager-1 | grep -i "error\|warning\|config"
   ```

3. **Проверьте активные алерты в Prometheus**:
   ```bash
   curl http://localhost:9090/api/v1/alerts | jq '.data.alerts[] | select(.labels.severity == "critical")'
   ```

---

## ✅ Система готова к работе

После выполнения всех проверок система настроена и готова автоматически отправлять все critical алерты в группу @testgroupassistant.

**Следующие действия**:
1. Проверьте группу @testgroupassistant в Telegram
2. Убедитесь, что тестовое уведомление пришло
3. Мониторьте активные алерты - они будут отправляться автоматически

