# Исправление Selenium/Xvfb для обхода Cloudflare

**Дата**: 2026-01-13  
**Задача**: Исправить инициализацию Selenium/Xvfb для обхода Cloudflare защиты при парсинге TGStat

## Проблема

- Selenium не мог инициализироваться в Docker контейнере
- Cloudflare блокировал простые HTTP-запросы (403 Forbidden)
- Многие темы не парсились (только 97 из 369)

## Решение

### 1. Обновление инициализации undetected-chromedriver

**Файл**: `tgstat-parser/parser/selenium_browser.py`

**Изменения**:
- Использование `uc.ChromeOptions()` вместо стандартного `ChromeOptions`
- Правильная настройка опций для Docker + Xvfb
- Удаление конфликтующих опций (`excludeSwitches`), так как undetected-chromedriver сам применяет патчи

**Ключевые настройки**:
```python
options = uc.ChromeOptions()
options.add_argument("--disable-blink-features=AutomationControlled")
options.add_argument("--no-sandbox")  # Обязательно для Docker
options.add_argument("--disable-dev-shm-usage")
options.add_argument("--disable-gpu")  # GPU не нужен в Xvfb

self.driver = uc.Chrome(
    options=options,
    headless=False,  # Non-headless для обхода Cloudflare (Xvfb обеспечивает display)
    use_subprocess=True,  # Для стабильности в Docker
    no_sandbox=True,  # Обязательно для Docker (best practice)
    suppress_welcome=True,
    log_level=0
)
```

### 2. Best Practices из Context7

- Использование `uc.ChromeOptions()` для правильной работы с undetected-chromedriver
- `no_sandbox=True` обязательно для Docker
- `use_subprocess=True` для стабильности в Docker
- Non-headless режим с Xvfb для обхода Cloudflare

### 3. Проверка работы

**Результат**:
- ✅ Браузер успешно инициализируется через `xvfb-run`
- ✅ DISPLAY правильно устанавливается (`:99`, `:100`)
- ✅ Chrome запускается в виртуальном display

## Текущий статус

- **Инициализация браузера**: ✅ Работает
- **Xvfb**: ✅ Работает (запускается через CMD в Dockerfile)
- **Парсинг тем**: Требует тестирования через реальный запуск парсера

## Следующие шаги

1. Протестировать парсинг списка тем через реальный запуск парсера
2. Проверить, что все 369 тем парсятся
3. Убедиться, что Cloudflare challenge проходит успешно

## Важные замечания

- При тестировании через `docker compose exec` нужно использовать `xvfb-run`, так как Xvfb не запущен автоматически
- В production (через CMD в Dockerfile) xvfb-run запускается автоматически
- DISPLAY устанавливается автоматически xvfb-run
