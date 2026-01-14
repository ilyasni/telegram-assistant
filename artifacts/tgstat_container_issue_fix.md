# Исправление проблемы с падением браузера в tgstat-parser

**Дата**: 2026-01-13  
**Проблема**: Контейнер tgstat-parser работает, но браузер постоянно падает

## Проблема

1. **Статус контейнера**: `unhealthy`
2. **Зомби-процессы**: Много процессов ChromeDriver в состоянии `defunct`
3. **Ошибки**: Браузер падает с ошибкой `Connection refused` (ChromeDriver сессия закрывается)
4. **Не пересоздается**: Браузер не пересоздается автоматически при таких ошибках

## Решение

Улучшена обработка ошибок в `selenium_browser.py`:

**Файл**: `tgstat-parser/parser/selenium_browser.py`

Добавлена проверка на дополнительные типы ошибок сессии:
- `connection refused`
- `maxretryerror`
- `newconnectionerror`

Теперь браузер автоматически пересоздается при любых ошибках сессии, не только при `tab crashed` или `session deleted`.

## Изменения

```python
# Добавлена проверка на дополнительные типы ошибок
is_session_error = (
    "tab crashed" in error_msg.lower() or 
    "session deleted" in error_msg.lower() or 
    "disconnected" in error_msg.lower() or
    "connection refused" in error_msg.lower() or  # НОВОЕ
    "maxretryerror" in error_msg.lower() or       # НОВОЕ
    "newconnectionerror" in error_msg.lower()     # НОВОЕ
)
```

## Следующие шаги

1. Перезапустить контейнер для применения изменений
2. Проверить, что браузер пересоздается автоматически
3. При необходимости запустить синхронизацию заново

## Мониторинг

Проверка статуса:
```bash
docker compose ps tgstat-parser
docker compose logs --tail=100 tgstat-parser | grep -E "(Browser recreated|session error)" -i
```
