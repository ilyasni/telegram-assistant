"""Selenium browser manager для обхода Cloudflare защиты.
Context7: Использует undetected-chromedriver для anti-detection.
"""

import time
import random
from typing import Optional
import structlog
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
# Context7: uc.ChromeOptions будет импортирован динамически при использовании undetected-chromedriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

# Context7: undetected-chromedriver для обхода bot detection
try:
    import undetected_chromedriver as uc
    UNDETECTED_AVAILABLE = True
except ImportError:
    UNDETECTED_AVAILABLE = False

from config import settings

logger = structlog.get_logger()

# Context7: Логирование статуса undetected-chromedriver после инициализации logger
if UNDETECTED_AVAILABLE:
    logger.debug("undetected-chromedriver imported", version=getattr(uc, '__version__', 'unknown'))
else:
    logger.warning("undetected-chromedriver not available")


class SeleniumBrowser:
    """Менеджер Selenium браузера для парсинга.
    
    Context7: Реализует anti-detection меры и graceful shutdown.
    """
    
    def __init__(self, headless: bool = None):
        """Инициализация браузера.
        
        Args:
            headless: Использовать headless режим (Context7: по умолчанию из настроек)
        """
        self.headless = headless if headless is not None else settings.selenium_headless
        self.driver: Optional[webdriver.Chrome] = None
        self._initialize_browser()
    
    def _initialize_browser(self):
        """Инициализация браузера с anti-detection настройками.
        
        Context7: Использует undetected-chromedriver для обхода Cloudflare.
        Context7: Best practices для Docker + Xvfb + Chrome 143 (2026).
        """
        try:
            if UNDETECTED_AVAILABLE:
                logger.info("Initializing undetected Chrome browser")
                import os
                display = os.environ.get('DISPLAY')
                logger.debug("DISPLAY environment variable", display=display)
                
                # Context7: Используем uc.ChromeOptions() вместо стандартного ChromeOptions
                # Context7: Это важно для правильной работы undetected-chromedriver
                options = uc.ChromeOptions()
                
                # Context7: Настройка page load strategy для предотвращения tab crashes
                # Context7: NONE - не ждём загрузку, используем explicit waits для конкретных элементов
                page_load_strategy = getattr(settings, 'selenium_page_load_strategy', 'none').lower()
                if page_load_strategy not in ['normal', 'eager', 'none']:
                    page_load_strategy = 'none'
                    logger.warning("Invalid page_load_strategy, using 'none'")
                options.page_load_strategy = page_load_strategy
                logger.debug("Page load strategy set", strategy=page_load_strategy)
                
                # Context7: Anti-detection меры для обхода Cloudflare
                # Context7: undetected-chromedriver сам применяет необходимые патчи для обхода детекции
                # Context7: Добавляем только базовые аргументы, остальное делает undetected-chromedriver
                options.add_argument("--disable-blink-features=AutomationControlled")
                
                # Context7: Настройки для Docker
                options.add_argument("--no-sandbox")  # Context7: обязательно для Docker
                options.add_argument("--disable-dev-shm-usage")  # Context7: для ограниченной памяти в Docker
                options.add_argument("--disable-gpu")  # Context7: GPU не нужен в Xvfb
                options.add_argument("--disable-setuid-sandbox")
                
                # Context7: Размер окна для имитации реального браузера
                width, height = map(int, settings.selenium_window_size.split(','))
                options.add_argument(f"--window-size={width},{height}")
                
                # Context7: User-Agent для имитации реального браузера (актуальный для 2026)
                options.add_argument(
                    "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
                
                # Context7: Языковые настройки
                options.add_argument("--lang=ru-RU,ru")
                options.add_experimental_option('prefs', {
                    'intl.accept_languages': 'ru-RU,ru,en-US,en'
                })
                
                # Context7: Инициализация с правильными параметрами для Docker + Xvfb
                # Context7: headless=False для non-headless режима (Xvfb обеспечивает display)
                # Context7: no_sandbox=True обязательно для Docker
                # Context7: use_subprocess=True для стабильности в Docker
                self.driver = uc.Chrome(
                    options=options,
                    headless=False,  # Context7: non-headless для обхода Cloudflare (Xvfb обеспечивает display)
                    version_main=None,  # Context7: автоматический выбор версии Chrome
                    use_subprocess=True,  # Context7: для стабильности в Docker
                    driver_executable_path=None,  # Context7: автоматический выбор
                    browser_executable_path=None,  # Context7: автоматический выбор
                    no_sandbox=True,  # Context7: обязательно для Docker (best practice)
                    suppress_welcome=True,  # Context7: подавляем welcome screen
                    log_level=0  # Context7: минимальное логирование для производительности
                )
                logger.info(
                    "Undetected Chrome browser initialized",
                    headless=False,
                    display=display,
                    page_load_strategy=page_load_strategy,
                    no_sandbox=True
                )
            else:
                logger.warning("undetected-chromedriver not available, using standard Chrome")
                options = self._get_chrome_options()
                self.driver = webdriver.Chrome(options=options)
                logger.info("Standard Chrome browser initialized", headless=self.headless)
            
            # Context7: Настройка таймаутов
            # Context7: С NONE стратегией page_load_timeout не используется, но устанавливаем для совместимости
            self.driver.set_page_load_timeout(30)  # Context7: короткий таймаут, так как используем NONE стратегию
            # Context7: Минимальный implicit wait - используем explicit waits для надёжности
            self.driver.implicitly_wait(1)  # Context7: минимальный implicit wait, полагаемся на explicit waits
            
            # Context7: Установка размера окна для имитации реального браузера
            width, height = map(int, settings.selenium_window_size.split(','))
            self.driver.set_window_size(width, height)
            
        except Exception as e:
            logger.error(
                "Failed to initialize browser",
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            raise
    
    def _get_chrome_options(self) -> ChromeOptions:
        """Получение настроек Chrome с anti-detection мерами.
        
        Context7: Настройки для обхода bot detection и оптимизации производительности.
        
        Returns:
            ChromeOptions с настроенными параметрами
        """
        options = ChromeOptions()
        
        # Context7: Non-headless режим для обхода Cloudflare
        # Context7: Xvfb обеспечивает виртуальный display, поэтому headless не нужен
        # Context7: Не добавляем --headless, чтобы браузер выглядел как обычный
        
        # Context7: Anti-detection меры
        options.add_argument("--disable-blink-features=AutomationControlled")
        # Context7: Используем правильный синтаксис для новых версий Chrome
        options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        options.add_experimental_option('useAutomationExtension', False)
        # Context7: Удаляем флаг автоматизации из User-Agent
        options.add_argument("--disable-dev-shm-usage")
        
        # Context7: Размер окна для имитации реального браузера
        width, height = map(int, settings.selenium_window_size.split(','))
        options.add_argument(f"--window-size={width},{height}")
        
        # Context7: User-Agent для имитации реального браузера
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        
        # Context7: Оптимизация производительности для Docker + Xvfb
        options.add_argument("--no-sandbox")  # Context7: требуется для Docker
        # Context7: НЕ отключаем dev-shm-usage, так как увеличили shm_size в docker-compose
        # options.add_argument("--disable-dev-shm-usage")  # Отключено - используем увеличенный /dev/shm
        options.add_argument("--disable-gpu")  # Context7: GPU не нужен в Xvfb
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-extensions")  # Context7: отключаем расширения для производительности
        # Context7: Дополнительные опции для работы в Xvfb и предотвращения crashes
        options.add_argument("--disable-setuid-sandbox")
        options.add_argument("--disable-background-timer-throttling")
        options.add_argument("--disable-backgrounding-occluded-windows")
        options.add_argument("--disable-renderer-backgrounding")
        # Context7: Опции для предотвращения crashes в Docker
        options.add_argument("--disable-ipc-flooding-protection")
        options.add_argument("--disable-crash-reporter")
        options.add_argument("--disable-logging")
        # Context7: Дополнительные опции для стабильности
        options.add_argument("--disable-features=TranslateUI,VizDisplayCompositor")
        options.add_argument("--disable-sync")
        options.add_argument("--disable-default-apps")
        # Context7: Агрессивные опции для предотвращения crashes
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-infobars")
        options.add_argument("--disable-notifications")
        options.add_argument("--disable-popup-blocking")
        options.add_argument("--disable-prompt-on-repost")
        options.add_argument("--disable-translate")
        options.add_argument("--disable-web-security")  # Context7: для обхода CORS, может помочь с загрузкой
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--ignore-ssl-errors")
        options.add_argument("--ignore-certificate-errors-spki-list")
        # Context7: Увеличенные лимиты памяти через флаги
        options.add_argument("--js-flags=--max-old-space-size=2048 --expose-gc")
        # Context7: Ограничение процессов для стабильности
        options.add_argument("--max_old_space_size=2048")
        
        # Context7: Блокировка ненужных ресурсов для ускорения и предотвращения crashes
        prefs = {
            "profile.managed_default_content_settings.images": 2,  # Блокировать изображения
            "profile.default_content_setting_values.notifications": 2,
            # Context7: Дополнительные настройки для оптимизации
            "profile.default_content_setting_values.media_stream": 2,  # Блокировать медиа
            "profile.default_content_setting_values.geolocation": 2,  # Блокировать геолокацию
            "profile.managed_default_content_settings.stylesheets": 2,  # Блокировать CSS (опционально, может сломать layout)
        }
        options.add_experimental_option("prefs", prefs)
        
        # Context7: Page load strategy для предотвращения tab crashes
        # Context7: EAGER - ждём только DOM, не ждём все ресурсы
        page_load_strategy = getattr(settings, 'selenium_page_load_strategy', 'eager').lower()
        if page_load_strategy in ['normal', 'eager', 'none']:
            options.page_load_strategy = page_load_strategy
        else:
            options.page_load_strategy = 'eager'
        
        # Context7: Языковые настройки
        options.add_argument("--lang=ru-RU,ru")
        options.add_experimental_option('prefs', {
            'intl.accept_languages': 'ru-RU,ru,en-US,en'
        })
        
        return options
    
    def get_page_source(self, url: str, wait_for_selector: Optional[str] = None, retries: int = 3) -> Optional[str]:
        """Получение HTML содержимого страницы.
        
        Args:
            url: URL для загрузки
            wait_for_selector: CSS селектор для ожидания (Context7: опционально)
            retries: Количество попыток при tab crash (Context7: по умолчанию 2)
            
        Returns:
            HTML содержимое страницы или None при ошибке
        """
        if not self.driver:
            logger.error("Browser not initialized")
            return None
        
        for attempt in range(retries):
            try:
                logger.debug("Loading page", url=url, attempt=attempt + 1)
                # Context7: Задержка перед загрузкой страницы для стабильности
                if attempt > 0:
                    time.sleep(random.uniform(5, 8))
                
                # Context7: Загрузка страницы с NONE стратегией (не ждём ничего, используем explicit waits)
                # Context7: Это максимально быстро и предотвращает таймауты
                try:
                    self.driver.get(url)
                except TimeoutException:
                    # Context7: С NONE стратегией таймаут может быть, но это нормально
                    logger.debug("Page load timeout (expected with NONE strategy)", url=url)
                
                # Context7: Имитация человеческого поведения - короткая задержка
                time.sleep(random.uniform(1, 2))
                
                # Context7: Explicit wait для конкретных элементов (NONE стратегия требует явных waits)
                # Context7: Это более надёжно и быстрее, чем ждать все ресурсы
                if wait_for_selector:
                    try:
                        # Context7: Ждём появления элемента с увеличенным таймаутом
                        # Context7: С NONE стратегией это критично - страница не ждёт загрузки
                        WebDriverWait(self.driver, 45).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, wait_for_selector))
                        )
                        logger.debug("Page content loaded", selector=wait_for_selector)
                        
                        # Context7: Проверяем готовность JavaScript через document.readyState
                        # Context7: Ждём, пока JavaScript полностью загрузится и выполнится
                        max_js_wait = 30  # Context7: увеличенное время для тяжёлых страниц
                        js_waited = 0
                        while js_waited < max_js_wait:
                            try:
                                ready_state = self.driver.execute_script("return document.readyState")
                                if ready_state == "complete":
                                    # Context7: Дополнительная проверка - ждём стабилизации DOM
                                    # Context7: Для тяжёлых страниц нужно больше времени для динамического контента
                                    time.sleep(random.uniform(5, 8))  # Context7: увеличенное время для загрузки данных
                                    # Context7: Проверяем, что контент действительно загрузился
                                    try:
                                        # Пробуем найти элементы каналов и данные о подписчиках
                                        check_script = """
                                        var elements = document.querySelectorAll('[class*="channel"], [class*="card"], table tr, tbody tr');
                                        var bodyText = document.body.innerText;
                                        var hasSubscribers = bodyText.includes('подписчик') || 
                                                             bodyText.includes('subscriber') ||
                                                             bodyText.match(/\\d+[\\s.,]*(тыс|млн|K|M|тысяч|миллион)/i);
                                        // Проверяем наличие таблиц с данными
                                        var tables = document.querySelectorAll('table');
                                        var tableRows = document.querySelectorAll('table tbody tr, table tr');
                                        return {
                                            elements: elements.length,
                                            tableRows: tableRows.length,
                                            tables: tables.length,
                                            hasSubscribers: hasSubscribers !== null,
                                            bodyLength: bodyText.length
                                        };
                                        """
                                        result = self.driver.execute_script(check_script)
                                        logger.debug("DOM check", 
                                                   elements=result.get('elements'), 
                                                   table_rows=result.get('tableRows'),
                                                   tables=result.get('tables'),
                                                   has_subscribers=result.get('hasSubscribers'), 
                                                   body_length=result.get('bodyLength'),
                                                   waited=js_waited)
                                        # Context7: Если найдено достаточно элементов (таблицы или карточки) И есть данные о подписчиках
                                        if (result.get('tableRows', 0) > 5 or result.get('elements', 0) > 5) and result.get('hasSubscribers', False):
                                            logger.debug("JavaScript content loaded with subscriber data", waited=js_waited, elements=result.get('elements'))
                                            # Context7: Дополнительная задержка для полной загрузки всех данных
                                            time.sleep(random.uniform(2, 4))
                                            break
                                        elif js_waited > 20:  # Context7: Увеличенное время ожидания для тяжёлых страниц
                                            logger.warning("Continuing after max wait time", waited=js_waited)
                                            break
                                    except Exception as e:
                                        logger.debug("Error checking DOM", error=str(e))
                                        if js_waited > 20:
                                            break
                                    logger.debug("JavaScript content loaded", ready_state=ready_state, waited=js_waited)
                                    break
                            except WebDriverException:
                                # Context7: Если браузер закрылся, выходим
                                logger.warning("Browser closed during JS wait", waited=js_waited)
                                break
                            time.sleep(1)
                            js_waited += 1
                        
                        if js_waited >= max_js_wait:
                            logger.warning("JavaScript may not be fully loaded", waited=js_waited)
                    except TimeoutException:
                        logger.warning("Timeout waiting for selector", selector=wait_for_selector)
                        # Context7: Продолжаем даже если селектор не найден - возможно контент загрузился
                    except WebDriverException as e:
                        # Context7: Обработка ошибок браузера
                        if "tab crashed" in str(e).lower() or "session deleted" in str(e).lower():
                            raise  # Context7: Пробрасываем для retry логики
                        logger.warning("WebDriver error during wait", error=str(e))
                else:
                    # Context7: Если селектор не указан, ждём минимальное время для базовой загрузки
                    time.sleep(random.uniform(2, 3))
                
                # Context7: Получаем page source (EAGER стратегия уже загрузила DOM)
                page_source = self.driver.page_source
                
                # Context7: Проверка на Cloudflare challenge
                if "Just a moment" in page_source or "cf-browser-verification" in page_source or "challenges.cloudflare.com" in page_source:
                    logger.warning("Cloudflare challenge detected, waiting...")
                    # Context7: Ожидание прохождения challenge с несколькими проверками
                    # Context7: Увеличенное время ожидания для non-headless режима
                    max_wait = 45  # Context7: максимум 45 секунд ожидания для Turnstile
                    wait_interval = 3
                    waited = 0
                    
                    while waited < max_wait:
                        time.sleep(wait_interval)
                        waited += wait_interval
                        page_source = self.driver.page_source
                        
                        # Context7: Проверяем, прошёл ли challenge
                        if "Just a moment" not in page_source and "cf-browser-verification" not in page_source and "challenges.cloudflare.com" not in page_source:
                            logger.info("Cloudflare challenge passed", waited_seconds=waited)
                            # Context7: Дополнительная задержка после прохождения challenge
                            time.sleep(random.uniform(2, 4))
                            page_source = self.driver.page_source
                            break
                        
                        logger.debug("Still waiting for Cloudflare challenge", waited_seconds=waited)
                    
                    if "Just a moment" in page_source or "cf-browser-verification" in page_source or "challenges.cloudflare.com" in page_source:
                        logger.error("Cloudflare challenge not passed after waiting", waited_seconds=waited)
                        if attempt < retries - 1:
                            continue
                        return None
                
                logger.debug("Page source retrieved", length=len(page_source))
                return page_source
                
            except WebDriverException as e:
                error_msg = str(e)
                # Context7: Проверка на tab crash, session deleted, connection refused
                is_session_error = (
                    "tab crashed" in error_msg.lower() or 
                    "session deleted" in error_msg.lower() or 
                    "disconnected" in error_msg.lower() or
                    "connection refused" in error_msg.lower() or
                    "maxretryerror" in error_msg.lower() or
                    "newconnectionerror" in error_msg.lower()
                )
                
                if is_session_error:
                    logger.warning(
                        "Browser session error, retrying",
                        url=url,
                        attempt=attempt + 1,
                        max_retries=retries,
                        error=error_msg[:100]  # Context7: первые 100 символов ошибки
                    )
                    if attempt < retries - 1:
                        # Context7: Пересоздаём браузер при ошибке сессии
                        try:
                            self.close()
                            # Context7: Увеличенная задержка перед пересозданием браузера
                            time.sleep(random.uniform(8, 12))
                            self._initialize_browser()
                            # Context7: Дополнительная задержка после инициализации
                            time.sleep(random.uniform(2, 4))
                            logger.info("Browser recreated after session error")
                        except Exception as init_error:
                            logger.error("Failed to recreate browser", error=str(init_error))
                            return None
                        continue
                    else:
                        logger.error("Browser session error after all retries", url=url)
                        return None
                else:
                    logger.error("WebDriver error", url=url, error=error_msg, error_type=type(e).__name__)
                    return None
                    
            except TimeoutException as e:
                logger.error("Page load timeout", url=url, error=str(e), attempt=attempt + 1)
                if attempt < retries - 1:
                    time.sleep(random.uniform(2, 4))
                    continue
                return None
            except Exception as e:
                logger.error(
                    "Unexpected error loading page",
                    url=url,
                    error=str(e),
                    error_type=type(e).__name__,
                    attempt=attempt + 1,
                    exc_info=True
                )
                if attempt < retries - 1:
                    time.sleep(random.uniform(2, 4))
                    continue
                return None
        
        return None
    
    def close(self):
        """Закрытие браузера.
        
        Context7: Graceful shutdown с обработкой ошибок.
        """
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Browser closed successfully")
            except Exception as e:
                logger.warning("Error closing browser", error=str(e))
            finally:
                self.driver = None
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
