"""Парсер TGStat для извлечения тем и каналов.
Context7: Надёжный парсинг с обработкой ошибок, retry и валидацией данных.
"""

import re
import time
import hashlib
from typing import List, Dict, Optional, Any
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
import structlog
from prometheus_client import Counter, Histogram

from parser.rate_limiter import RateLimiter
from parser.selenium_browser import SeleniumBrowser
from config import settings

logger = structlog.get_logger()

# Context7: Prometheus метрики для мониторинга парсера
parser_requests_total = Counter(
    'tgstat_parser_requests_total',
    'Total number of HTTP requests to TGStat',
    ['status']
)

parser_errors_total = Counter(
    'tgstat_parser_errors_total',
    'Total number of parsing errors',
    ['error_type']
)

parser_duration_seconds = Histogram(
    'tgstat_parser_duration_seconds',
    'Duration of parsing operations',
    ['operation']
)

rate_limit_hits_total = Counter(
    'tgstat_parser_rate_limit_hits_total',
    'Total number of rate limit hits'
)


class TgStatParser:
    """Парсер для извлечения данных из TGStat.
    
    Context7: Реализует парсинг с rate limiting, retry, валидацией и обработкой ошибок.
    """
    
    def __init__(
        self,
        base_url: str = None,
        rate_limiter: Optional[RateLimiter] = None
    ):
        """Инициализация парсера.
        
        Args:
            base_url: Базовый URL TGStat (Context7: по умолчанию из настроек)
            rate_limiter: Rate limiter для ограничения запросов (Context7: опционально)
        """
        self.base_url = base_url or settings.tgstat_base_url
        self.rate_limiter = rate_limiter or RateLimiter(
            requests_per_second=settings.rate_limit_per_second,
            max_requests=settings.rate_limit_max_requests
        )
        # Context7: Selenium браузер для обхода Cloudflare защиты
        # Context7: Браузер создаётся при первом использовании (lazy initialization)
        self.browser: Optional[SeleniumBrowser] = None
        # Context7: Fallback на requests для простых запросов
        self.session = requests.Session()
        self._setup_browser_headers()
    
    def _setup_browser_headers(self):
        """Настройка браузероподобных заголовков для fallback requests.
        
        Context7: Используется как fallback, когда Selenium недоступен.
        """
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            # Context7: Убираем br (Brotli) из Accept-Encoding, т.к. requests может не распаковывать его автоматически
            # Используем только gzip и deflate, которые поддерживаются стандартной библиотекой
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Cache-Control": "max-age=0",
        })
        # Context7: Кеш для hash HTML страниц
        self.html_cache: Dict[str, str] = {}
    
    def _make_request(
        self,
        url: str,
        retries: int = None
    ) -> Optional[requests.Response]:
        """Выполнение HTTP запроса с retry и rate limiting.
        
        Args:
            url: URL для запроса
            retries: Количество попыток retry (Context7: по умолчанию из настроек)
            
        Returns:
            Response объект или None при ошибке
        """
        retries = retries or settings.http_max_retries
        
        for attempt in range(retries):
            try:
                # Context7: Rate limiting перед каждым запросом
                self.rate_limiter.wait_if_needed()
                
                # Context7: Дополнительная задержка при retry (имитация человеческого поведения)
                if attempt > 0:
                    time.sleep(2 * attempt)  # Context7: Экспоненциальная задержка при retry
                
                # Context7: Выполнение запроса с таймаутом
                response = self.session.get(
                    url,
                    timeout=settings.http_timeout,
                    allow_redirects=True
                )
                
                # Context7: Проверка статуса ответа
                if response.status_code == 403:
                    logger.warning(
                        "403 Forbidden received",
                        url=url,
                        attempt=attempt + 1,
                        headers=dict(response.headers)
                    )
                    # Context7: Для 403 пробуем ещё раз с задержкой
                    if attempt < retries - 1:
                        sleep_time = 5 * (attempt + 1)
                        logger.debug("Waiting before retry after 403", sleep_time=sleep_time)
                        time.sleep(sleep_time)
                        continue
                
                response.raise_for_status()
                
                # Context7: Проверка на Cloudflare защиту (fallback для requests)
                if "Just a moment" in response.text or "cf-browser-verification" in response.text:
                    logger.warning(
                        "Cloudflare protection detected (should use Selenium)",
                        url=url,
                        attempt=attempt + 1
                    )
                    # Context7: Увеличение метрики Cloudflare блокировок
                    parser_requests_total.labels(status='cloudflare').inc()
                    parser_errors_total.labels(error_type='cloudflare').inc()
                    
                    # Context7: Для Cloudflare нужен Selenium
                    if attempt < retries - 1:
                        # Context7: Увеличиваем задержку перед следующей попыткой
                        sleep_time = 5 * (attempt + 1)
                        logger.debug("Waiting before retry", sleep_time=sleep_time)
                        time.sleep(sleep_time)
                        continue
                    else:
                        logger.error("Cloudflare protection not bypassed, use Selenium", url=url)
                        return None
                
                # Context7: Увеличение метрики успешных запросов
                parser_requests_total.labels(status='success').inc()
                
                logger.debug(
                    "HTTP request successful",
                    url=url,
                    status_code=response.status_code,
                    attempt=attempt + 1
                )
                
                return response
                
            except requests.exceptions.Timeout as e:
                # Context7: Увеличение метрик ошибок
                parser_requests_total.labels(status='timeout').inc()
                parser_errors_total.labels(error_type='timeout').inc()
                
                logger.warning(
                    "HTTP request timeout",
                    url=url,
                    attempt=attempt + 1,
                    max_retries=retries,
                    error=str(e)
                )
                if attempt < retries - 1:
                    # Context7: Экспоненциальный backoff
                    sleep_time = 2 ** attempt
                    time.sleep(sleep_time)
                else:
                    logger.error("HTTP request failed after retries", url=url)
                    return None
                    
            except requests.exceptions.RequestException as e:
                # Context7: Увеличение метрик ошибок
                parser_requests_total.labels(status='error').inc()
                parser_errors_total.labels(error_type=type(e).__name__).inc()
                
                logger.warning(
                    "HTTP request error",
                    url=url,
                    attempt=attempt + 1,
                    max_retries=retries,
                    error=str(e),
                    error_type=type(e).__name__
                )
                if attempt < retries - 1:
                    # Context7: Экспоненциальный backoff
                    sleep_time = 2 ** attempt
                    time.sleep(sleep_time)
                else:
                    logger.error("HTTP request failed after retries", url=url)
                    return None
        
        return None
    
    def _get_browser(self, force_new: bool = False) -> Optional[SeleniumBrowser]:
        """Получение или создание Selenium браузера.
        
        Context7: Lazy initialization для оптимизации ресурсов.
        Context7: force_new=True для пересоздания браузера (предотвращает tab crashes).
        
        Args:
            force_new: Принудительно создать новый браузер (Context7: для стабильности)
        
        Returns:
            SeleniumBrowser или None при ошибке
        """
        if force_new and self.browser:
            # Context7: Закрываем старый браузер перед созданием нового
            self._close_browser()
        
        if self.browser is None or force_new:
            try:
                self.browser = SeleniumBrowser(headless=settings.selenium_headless)
                logger.info("Selenium browser created", force_new=force_new)
            except Exception as e:
                logger.error(
                    "Failed to create Selenium browser",
                    error=str(e),
                    error_type=type(e).__name__,
                    exc_info=True
                )
                return None
        return self.browser
    
    def _close_browser(self):
        """Закрытие Selenium браузера.
        
        Context7: Graceful shutdown для освобождения ресурсов.
        """
        if self.browser:
            try:
                self.browser.close()
                self.browser = None
                logger.info("Selenium browser closed")
            except Exception as e:
                logger.warning("Error closing browser", error=str(e))
    
    def __del__(self):
        """Деструктор для закрытия браузера при удалении объекта.
        
        Context7: Обеспечивает освобождение ресурсов.
        """
        self._close_browser()
    
    def _get_html_hash(self, html: str) -> str:
        """Вычисление hash HTML для кеширования.
        
        Context7: Оптимизация производительности через проверку изменений.
        
        Args:
            html: HTML содержимое
            
        Returns:
            SHA256 hash строки
        """
        return hashlib.sha256(html.encode('utf-8')).hexdigest()
    
    def _check_cache(self, url: str, html: str) -> bool:
        """Проверка, изменился ли HTML по сравнению с кешем.
        
        Context7: Избегаем повторной обработки неизменённых страниц.
        
        Args:
            url: URL страницы
            html: HTML содержимое
            
        Returns:
            True если страница не изменилась
        """
        current_hash = self._get_html_hash(html)
        cached_hash = self.html_cache.get(url)
        
        if cached_hash == current_hash:
            logger.debug("HTML cache hit", url=url)
            return True
        
        self.html_cache[url] = current_hash
        return False
    
    def normalize_username(self, username: str) -> str:
        """Нормализация username канала.
        
        Context7: Безопасность - очистка от специальных символов и приведение к lowercase.
        
        Args:
            username: Исходный username (может содержать @)
            
        Returns:
            Нормализованный username без @ и в lowercase
        """
        if not username:
            return ""
        
        # Context7: Убираем @ в начале
        username = username.lstrip('@')
        
        # Context7: Приводим к lowercase
        username = username.lower()
        
        # Context7: Убираем пробелы
        username = username.strip()
        
        # Context7: Валидация - только буквы, цифры и подчёркивания
        username = re.sub(r'[^a-z0-9_]', '', username)
        
        return username
    
    def _extract_channel_from_json(
        self,
        item: Dict[str, Any],
        theme_slug: str
    ) -> Optional[Dict[str, Any]]:
        """Извлечение данных канала из JSON объекта.
        
        Context7: Многие сайты загружают данные через JSON, это более надёжно чем парсинг HTML.
        
        Args:
            item: JSON объект с данными канала
            theme_slug: Slug темы
            
        Returns:
            Словарь с данными канала или None
        """
        try:
            # Context7: Различные возможные ключи в JSON
            username = None
            title = None
            subscribers = None
            er = None
            url = None
            
            # Context7: Поиск username
            for key in ['username', 'channel_username', 'slug', 'name', 'id']:
                if key in item:
                    username = str(item[key]).strip().lstrip('@')
                    break
            
            # Context7: Поиск title
            for key in ['title', 'name', 'display_name', 'channel_name']:
                if key in item:
                    title = str(item[key]).strip()
                    break
            
            # Context7: Поиск subscribers
            for key in ['subscribers', 'subscriber_count', 'members', 'members_count', 'followers']:
                if key in item:
                    subscribers = self._parse_subscribers(str(item[key]))
                    break
            
            # Context7: Поиск ER
            for key in ['er', 'engagement_rate', 'engagement', 'er_percent']:
                if key in item:
                    er = self._parse_er(str(item[key]))
                    break
            
            # Context7: Поиск URL (приоритет TGStat ссылкам)
            url = None
            tgstat_url = None
            
            # Context7: Сначала ищем TGStat ссылку
            for key in ['tgstat_url', 'tgstat_link', 'channel_url', 'url', 'link', 'href']:
                if key in item:
                    url_value = str(item[key]).strip()
                    if 'tgstat.ru/channel/' in url_value:
                        tgstat_url = url_value
                        break
                    elif url and not tgstat_url:
                        url = url_value
            
            # Context7: Если username найден, но URL нет - формируем TGStat URL
            if username and not tgstat_url:
                tgstat_url = f"https://tgstat.ru/channel/@{username}"
            
            # Context7: Валидация - нужен хотя бы username
            if not username:
                return None
            
            # Context7: Нормализация username
            username = self.normalize_username(username)
            
            # Context7: Используем TGStat ссылку, если найдена
            final_url = tgstat_url or url or f"https://tgstat.ru/channel/@{username}"
            
            return {
                'channel_username': username,
                'title': title or username,
                'subscribers': subscribers or 0,
                'er': er,
                'url': final_url,
            }
        except Exception as e:
            logger.debug("Error extracting channel from JSON", error=str(e))
            return None
    
    def parse_themes_list(self) -> List[Dict[str, str]]:
        """Парсинг списка тем со страницы TGStat.
        
        Context7: Извлекает все темы со страницы https://tgstat.ru/tags/theme
        Использует Selenium для обхода Cloudflare защиты.
        
        Returns:
            Список словарей с полями: slug, name, description
        """
        url = urljoin(self.base_url, "/tags/theme")
        logger.info("Parsing themes list", url=url)
        
        # Context7: Измерение времени выполнения
        with parser_duration_seconds.labels(operation='parse_themes_list').time():
            # Context7: Используем Selenium для обхода Cloudflare
            # Context7: Создаём новый браузер для каждого запроса (предотвращает tab crashes)
            html = None
            browser = self._get_browser(force_new=True)
            if browser:
                try:
                    html = browser.get_page_source(url, wait_for_selector='a[href*="/tag/"]')
                    if html:
                        # Context7: Закрываем браузер после успешного парсинга
                        self._close_browser()
                    else:
                        logger.error("Failed to fetch themes list with Selenium", url=url)
                        self._close_browser()  # Context7: Закрываем браузер при ошибке
                except Exception as e:
                    logger.warning(
                        "Selenium browser error, switching to requests fallback",
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    self._close_browser()  # Context7: Закрываем браузер при ошибке
                    html = None
            
            # Context7: Fallback на requests если Selenium не сработал
            if not html:
                logger.warning("Selenium not available or failed, using requests fallback")
                response = self._make_request(url)
                if not response:
                    logger.error("Failed to fetch themes list", url=url)
                    return []
                html = response.text
        
        try:
            soup = BeautifulSoup(html, 'lxml')
            
            # Context7: Парсинг общего количества подборок из текста страницы
            # Ищем паттерны типа "369 каналов и чатов" для понимания общего количества
            page_text = soup.get_text()
            total_themes_on_page = None
            count_patterns = [
                r'(\d+)\s+каналов?\s+и\s+чатов?',
                r'(\d+)\s+подборок?',
                r'(\d+)\s+тем',
                r'всего\s+(\d+)',
                r'(\d+)\s+тегов?',
            ]
            for pattern in count_patterns:
                matches = re.findall(pattern, page_text, re.IGNORECASE)
                if matches:
                    # Берем наибольшее число (обычно это общее количество)
                    numbers = [int(m) for m in matches]
                    total_themes_on_page = max(numbers)
                    logger.info(
                        "Total themes count found on page",
                        total=total_themes_on_page,
                        pattern=pattern
                    )
                    break
            
            themes = []
            seen_slugs = set()  # Context7: Дедупликация тем
            
            # Context7: Стратегия 1: Поиск ссылок на теги в различных местах
            # Варианты селекторов для разных структур страницы
            # Context7: Расширенный список селекторов на основе реальной структуры TGStat
            selectors = [
                'a[href*="/tag/"]',  # Прямые ссылки на теги (основной селектор)
                'a[href*="/tags/"]',  # Альтернативный формат URL
                '.tag-item a',  # Элементы с классом tag-item
                '.theme-item a',  # Элементы с классом theme-item
                '.category-item a',  # Элементы категорий
                '[data-tag] a',  # Элементы с data-атрибутом
                'div.tags a',  # Ссылки внутри div.tags
                'ul.tags li a',  # Ссылки в списке тегов
                '.tag a',  # Ссылки внутри .tag
                '.tag-link',  # Прямой класс tag-link
                'article a[href*="/tag/"]',  # Ссылки в article
                'section a[href*="/tag/"]',  # Ссылки в section
            ]
            
            elements = []
            for selector in selectors:
                try:
                    found = soup.select(selector)
                    if found:
                        logger.debug("Found elements with selector", selector=selector, count=len(found))
                        elements.extend(found)  # Context7: Собираем все найденные элементы
                except Exception as e:
                    logger.debug("Selector failed", selector=selector, error=str(e))
                    continue
            
            # Context7: Дедупликация элементов из селекторов
            if elements:
                seen_hrefs = set()
                unique_elements = []
                for elem in elements:
                    href = elem.get('href', '')
                    if href and href not in seen_hrefs:
                        seen_hrefs.add(href)
                        unique_elements.append(elem)
                elements = unique_elements
                logger.debug("After deduplication from selectors", count=len(elements))
            
            # Context7: Если не нашли через селекторы, ищем все ссылки с /tag/ в href
            # Context7: Это fallback, который всегда должен работать
            if not elements:
                logger.debug("No elements from selectors, using href pattern fallback")
                all_links = soup.find_all('a', href=True)
                elements = [link for link in all_links if '/tag/' in link.get('href', '') or '/tags/' in link.get('href', '')]
                logger.debug("Found links by href pattern", count=len(elements))
                
                # Context7: Дедупликация элементов из fallback
                if elements:
                    seen_hrefs = set()
                    unique_elements = []
                    for elem in elements:
                        href = elem.get('href', '')
                        if href and href not in seen_hrefs:
                            seen_hrefs.add(href)
                            unique_elements.append(elem)
                    elements = unique_elements
                    logger.debug("After deduplication from fallback", count=len(elements))
            
            # Context7: Дедупликация элементов по href
            seen_hrefs = set()
            unique_elements = []
            for elem in elements:
                href = elem.get('href', '')
                if href and href not in seen_hrefs:
                    seen_hrefs.add(href)
                    unique_elements.append(elem)
            elements = unique_elements
            logger.debug("After deduplication", count=len(elements))
            
            for element in elements:
                try:
                    href = element.get('href', '')
                    if not href:
                        continue
                    
                    # Context7: Нормализация href (может быть относительным)
                    if href.startswith('/'):
                        href = urljoin(self.base_url, href)
                    elif not href.startswith('http'):
                        continue
                    
                    # Context7: Извлечение slug из URL
                    # Context7: Поддержка различных форматов URL: /tag/slug, /tags/slug
                    slug_match = re.search(r'/tags?/([^/?]+)', href)
                    if not slug_match:
                        continue
                    
                    slug = slug_match.group(1).strip()
                    
                    # Context7: Пропускаем служебные slug'и
                    if slug in ['theme', 'category', 'all', 'popular']:
                        continue
                    
                    # Context7: Пропускаем дубликаты
                    if slug in seen_slugs:
                        continue
                    seen_slugs.add(slug)
                    
                    # Context7: Извлечение названия темы
                    # Context7: Приоритет 1: Текст ссылки, если она имеет класс font-20 (основные ссылки на темы)
                    name = None
                    if 'font-20' in element.get('class', []):
                        name = element.get_text(strip=True)
                    
                    # Context7: Приоритет 2: Текст ссылки, если он не пустой и не содержит "каналов и чатов"
                    if not name:
                        name = element.get_text(strip=True)
                        # Context7: Пропускаем служебные ссылки типа "34 каналов и чатов"
                        if name and ('каналов и чатов' in name.lower() or name.isdigit() or len(name) < 3):
                            name = None
                    
                    # Context7: Приоритет 3: Ищем название в родительском элементе с классом card
                    if not name:
                        parent = element.parent
                        while parent and not name:
                            # Context7: Ищем ссылку с классом font-20 в родительском элементе
                            title_link = parent.find('a', class_=lambda x: x and 'font-20' in x)
                            if title_link:
                                name = title_link.get_text(strip=True)
                                break
                            # Context7: Или ищем заголовок в родительском элементе
                            if parent.name in ['h1', 'h2', 'h3', 'h4', 'h5', 'h6']:
                                name = parent.get_text(strip=True)
                                break
                            parent = parent.parent
                    
                    # Context7: Приоритет 4: Используем slug как название
                    if not name:
                        name = slug.replace('-', ' ').replace('_', ' ').title()
                    
                    # Context7: Извлечение описания (опционально)
                    description = None
                    # Ищем описание в соседних элементах
                    parent = element.parent
                    if parent:
                        desc_elem = parent.find(class_=re.compile(r'description|desc|text', re.I))
                        if desc_elem:
                            description = desc_elem.get_text(strip=True)
                    
                    # Context7: Валидация данных
                    if not slug or len(slug) < 1:
                        logger.warning("Invalid theme slug", slug=slug)
                        continue
                    
                    if not name or len(name) < 1:
                        logger.warning("Invalid theme name", slug=slug, name=name)
                        continue
                    
                    themes.append({
                        'slug': slug,
                        'name': name,
                        'description': description
                    })
                    
                except Exception as e:
                    logger.warning(
                        "Error parsing theme element",
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True
                    )
                    continue
            
            # Context7: Дедупликация по slug на случай дублей
            unique_themes = {}
            for theme in themes:
                slug = theme['slug']
                if slug not in unique_themes:
                    unique_themes[slug] = theme
            
            themes = list(unique_themes.values())
            
            # Context7: Логирование результатов с сравнением ожидаемого и фактического количества
            parsed_count = len(themes)
            logger.info(
                "Themes list parsed",
                themes_count=parsed_count,
                total_themes_on_page=total_themes_on_page,
                url=url
            )
            
            # Context7: Предупреждение, если найдено меньше тем, чем указано на странице
            if total_themes_on_page and parsed_count < total_themes_on_page:
                missing_count = total_themes_on_page - parsed_count
                logger.warning(
                    "Not all themes were parsed",
                    expected=total_themes_on_page,
                    found=parsed_count,
                    missing=missing_count,
                    missing_pct=round(missing_count / total_themes_on_page * 100, 1)
                )
            
            return themes
            
        except Exception as e:
            logger.error(
                "Error parsing themes list",
                url=url,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            return []
    
    def parse_theme_channels(self, theme_slug: str) -> List[Dict[str, Any]]:
        """Парсинг топ-30 каналов для конкретной темы.
        
        Context7: Извлекает каналы со страницы темы, сортирует по подписчикам.
        
        Args:
            theme_slug: Slug темы
            
        Returns:
            Список словарей с данными каналов (максимум 30)
        """
        url = urljoin(self.base_url, f"/tag/{theme_slug}")
        logger.info("Parsing theme channels", theme_slug=theme_slug, url=url)
        
        # Context7: Измерение времени выполнения
        with parser_duration_seconds.labels(operation='parse_theme_channels').time():
            # Context7: Используем Selenium для обхода Cloudflare
            # Context7: Создаём новый браузер для каждого запроса (предотвращает tab crashes)
            html = None
            browser = self._get_browser(force_new=True)
            if browser:
                try:
                    # Context7: Используем более умные селекторы для ожидания динамического контента
                    # Context7: Пробуем несколько вариантов селекторов, включая элементы с данными о подписчиках
                    selectors_to_try = [
                        'table tr',  # Context7: Таблицы с данными каналов
                        '[class*="subscriber"]',  # Элементы с информацией о подписчиках
                        '[data-subscribers]',  # Data-атрибуты с подписчиками
                        '.channel-item',  # Основной селектор
                        '.card',  # Альтернативный
                        'article',  # Ещё один вариант
                        '[class*="channel"]',  # Более общий селектор
                        'body'  # Fallback - просто ждём загрузки body
                    ]
                    
                    for selector in selectors_to_try:
                        try:
                            html = browser.get_page_source(url, wait_for_selector=selector)
                            if html and len(html) > 20000:  # Context7: Минимум 20KB для полноценной страницы
                                # Context7: Проверяем, что в HTML есть данные о подписчиках
                                html_lower = html.lower()
                                has_subscribers = (
                                    'подписчик' in html_lower or 
                                    'subscriber' in html_lower or 
                                    re.search(r'\d+[.,]?\d*\s*(тыс|млн|k|m)', html_lower) is not None
                                )
                                if has_subscribers:  # Context7: Если есть данные о подписчиках
                                    logger.debug("Page loaded with channel data", selector=selector, html_length=len(html))
                                    break
                                else:
                                    logger.debug("Page loaded but no subscriber data found", selector=selector, html_length=len(html))
                                    # Context7: Продолжаем пробовать другие селекторы
                            elif html:
                                logger.debug("Page loaded but content seems short", selector=selector, html_length=len(html) if html else 0)
                        except Exception as selector_error:
                            logger.debug("Selector failed, trying next", selector=selector, error=str(selector_error))
                            continue
                    
                    if html:
                        # Context7: Закрываем браузер после успешного парсинга
                        self._close_browser()
                    else:
                        logger.error("Failed to fetch theme channels with Selenium", theme_slug=theme_slug, url=url)
                        self._close_browser()  # Context7: Закрываем браузер при ошибке
                except Exception as e:
                    logger.warning(
                        "Selenium browser error, switching to requests fallback",
                        theme_slug=theme_slug,
                        error=str(e),
                        error_type=type(e).__name__
                    )
                    self._close_browser()  # Context7: Закрываем браузер при ошибке
                    html = None
            
            # Context7: Fallback на requests если Selenium не сработал
            if not html:
                logger.warning("Selenium not available or failed, using requests fallback", theme_slug=theme_slug)
                response = self._make_request(url)
                if not response:
                    logger.error("Failed to fetch theme channels", theme_slug=theme_slug, url=url)
                    return []
                html = response.text
        
        # Context7: Проверка кеша
        if self._check_cache(url, html):
            logger.info("Theme channels not changed, using cache", theme_slug=theme_slug)
            # Context7: Возвращаем пустой список, чтобы не обновлять БД
            return []
        
        try:
            soup = BeautifulSoup(html, 'lxml')
            channels = []
            channel_elements = []  # Context7: Инициализация переменной
            
            # Context7: Стратегия 0: Поиск данных в JSON-LD или script тегах
            # Context7: Многие современные сайты загружают данные через JavaScript
            script_tags = soup.find_all('script', type='application/json')
            for script in script_tags:
                try:
                    import json
                    data = json.loads(script.string)
                    # Context7: Ищем данные каналов в JSON
                    if isinstance(data, dict):
                        # Проверяем различные возможные ключи
                        for key in ['channels', 'items', 'data', 'results']:
                            if key in data and isinstance(data[key], list):
                                logger.debug("Found channel data in JSON", key=key, count=len(data[key]))
                                # Обрабатываем данные из JSON
                                for item in data[key]:
                                    if isinstance(item, dict):
                                        channel_data = self._extract_channel_from_json(item, theme_slug)
                                        if channel_data:
                                            channels.append(channel_data)
                except (json.JSONDecodeError, AttributeError):
                    continue
            
            # Context7: Стратегия 1: Поиск по таблицам (TGStat часто использует таблицы)
            # Context7: Сначала ищем таблицы, так как они содержат структурированные данные
            tables = soup.find_all('table')
            if tables:
                for table in tables:
                    rows = table.find_all('tr')
                    # Context7: Пропускаем заголовки (th) и берём строки с данными
                    data_rows = [row for row in rows if row.find('th') is None and row.find('a', href=re.compile(r't\.me/'))]
                    if data_rows:
                        channel_elements.extend(data_rows)
                        logger.debug(
                            "Found channel elements in table",
                            count=len(data_rows),
                            theme_slug=theme_slug
                        )
                        break
            
            # Context7: Стратегия 2: Поиск по классам и data-атрибутам
            # Context7: На страницах TGStat каналы находятся в элементах вида:
            # <div class="card card-body peer-item-box ...">
            #   <a href="https://tgstat.ru/channel/@username" class="text-body">
            if not channel_elements:
                selectors = [
                    '.peer-item-box',  # Context7: Основной селектор TGStat для карточек каналов
                    '.card.peer-item-box',  # Context7: С классом card
                    'div[class*="peer-item"]',  # Context7: Элементы с peer-item в классе
                    'table tbody tr',  # Строки таблицы в tbody
                    'table tr',  # Все строки таблицы
                    '.channel-item',
                    '.channel-card',
                    '.channel',
                    '[data-channel]',
                    '.channels-list .item',
                    '.channels-list li',
                    'table.channels tr',
                    '.ranking-item',
                    '.top-channel',
                    '[class*="channel"]',  # Более общий селектор
                    '[class*="card"]',  # Карточки каналов
                    'tr[data-id]',  # Таблицы с data-id
                    '.list-item',  # Элементы списка
                ]
                
                for selector in selectors:
                    elements = soup.select(selector)
                    if elements:
                        # Context7: Фильтруем элементы, которые содержат ссылки на TGStat каналы или t.me
                        filtered = [
                            el for el in elements 
                            if el.find('a', href=re.compile(r'tgstat\.(?:ru|com)(?:/ru)?/channel/')) or 
                               el.find('a', href=re.compile(r't\.me/'))
                        ]
                        if filtered:
                            logger.debug(
                                f"Found channel elements with selector: {selector}",
                                count=len(filtered),
                                theme_slug=theme_slug
                            )
                            channel_elements.extend(filtered)
                            break
            
            # Context7: Стратегия 2: Поиск по структуре таблицы
            if not channel_elements:
                tables = soup.find_all('table')
                for table in tables:
                    rows = table.find_all('tr')
                    for row in rows:
                        # Пропускаем заголовок
                        if row.find('th'):
                            continue
                        # Проверяем наличие ссылки на t.me
                        if row.find('a', href=re.compile(r't\.me/')):
                            channel_elements.append(row)
            
            # Context7: Стратегия 3: Поиск всех элементов со ссылками на t.me
            if not channel_elements:
                all_links = soup.find_all('a', href=re.compile(r't\.me/'))
                # Группируем ссылки по родительским элементам
                parent_elements = set()
                for link in all_links:
                    parent = link.find_parent(['div', 'li', 'tr', 'article', 'section'])
                    if parent:
                        parent_elements.add(parent)
                channel_elements = list(parent_elements)
                logger.debug(
                    "Found channels by t.me links",
                    count=len(channel_elements),
                    theme_slug=theme_slug
                )
            
            # Context7: Парсинг каждого элемента канала
            seen_usernames = set()  # Context7: Дедупликация по username
            for element in channel_elements:
                try:
                    channel_data = self._extract_channel_data(element, theme_slug)
                    if channel_data:
                        username = channel_data.get('channel_username')
                        # Context7: Пропускаем дубликаты
                        if username and username in seen_usernames:
                            continue
                        seen_usernames.add(username)
                        channels.append(channel_data)
                except Exception as e:
                    logger.warning(
                        "Error parsing channel element",
                        theme_slug=theme_slug,
                        error=str(e),
                        error_type=type(e).__name__,
                        exc_info=True
                    )
                    continue
            
            # Context7: Валидация и сортировка каналов через QualityChecker
            from parser.quality_checker import QualityChecker
            valid_channels, validation_errors = QualityChecker.validate_and_sort_channels(
                channels,
                max_channels=30
            )
            
            if validation_errors:
                logger.warning(
                    "Channel validation errors",
                    theme_slug=theme_slug,
                    errors_count=len(validation_errors),
                    errors=validation_errors[:5]  # Логируем первые 5 ошибок
                )
            
            channels = valid_channels
            
            logger.info(
                "Theme channels parsed",
                theme_slug=theme_slug,
                channels_count=len(channels),
                url=url
            )
            
            return channels
            
        except Exception as e:
            logger.error(
                "Error parsing theme channels",
                theme_slug=theme_slug,
                url=url,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            return []
    
    def _extract_channel_data(
        self,
        element: BeautifulSoup,
        theme_slug: str
    ) -> Optional[Dict[str, Any]]:
        """Извлечение данных канала из HTML элемента.
        
        Context7: Использует множественные стратегии для извлечения всех полей.
        
        Args:
            element: BeautifulSoup элемент канала
            theme_slug: Slug темы для логирования
            
        Returns:
            Словарь с данными канала или None при ошибке
        """
        try:
            # Context7: Стратегия 1: Поиск ссылки на TGStat канал (приоритет)
            # Context7: На страницах подборок каналы имеют ссылки вида https://tgstat.ru/channel/@username
            # Context7: Ссылка обычно находится в теге <a href="https://tgstat.ru/channel/@username" class="text-body">
            username = None
            username_elem = None
            tgstat_url = None
            
            # Context7: Ищем все ссылки в элементе
            all_links = element.find_all('a', href=True)
            for link in all_links:
                href = link.get('href', '')
                # Context7: Ищем ссылки на TGStat каналы (ru и com домены)
                if 'tgstat.ru/channel/' in href or 'tgstat.com/ru/channel/' in href or 'tgstat.com/channel/' in href:
                    # Context7: Извлекаем username из TGStat ссылки
                    # Паттерны: 
                    # - https://tgstat.ru/channel/@username
                    # - https://tgstat.com/ru/channel/@username
                    # - https://tgstat.ru/channel/username (без @)
                    tgstat_match = re.search(r'tgstat\.(?:ru|com)(?:/ru)?/channel/@?([a-zA-Z0-9_]+)', href)
                    if tgstat_match:
                        username = self.normalize_username(tgstat_match.group(1))
                        # Context7: Нормализуем ссылку на TGStat (всегда https://tgstat.ru/channel/@username)
                        tgstat_url = f"https://tgstat.ru/channel/@{username}"
                        username_elem = link
                        logger.debug("Found TGStat channel link", href=href, username=username, theme_slug=theme_slug)
                        break
            
            # Context7: Если не нашли TGStat ссылку, ищем ссылку на t.me (fallback)
            if not username_elem:
                link_selectors = [
                    'a[href*="t.me/"]',
                    'a[href*="telegram.me/"]',
                    '.channel-link a',
                    '.username a',
                ]
                
                for selector in link_selectors:
                    username_elem = element.select_one(selector)
                    if username_elem:
                        break
                
                # Context7: Если не нашли через селекторы, ищем все ссылки в элементе
                if not username_elem:
                    for link in all_links:
                        href = link.get('href', '')
                        if 't.me/' in href or 'telegram.me/' in href:
                            username_elem = link
                            break
                
                if username_elem:
                    # Context7: Извлечение username из href
                    href = username_elem.get('href', '')
                    username_match = re.search(r'(?:t|telegram)\.me/([a-zA-Z0-9_]+)', href)
                    if username_match:
                        username = self.normalize_username(username_match.group(1))
                    else:
                        # Context7: Fallback - извлекаем из текста ссылки
                        link_text = username_elem.get_text(strip=True)
                        username = self.normalize_username(link_text)
            
            if not username:
                return None
            
            # Context7: Если TGStat ссылка не найдена, но есть username - формируем её
            if not tgstat_url:
                tgstat_url = f"https://tgstat.ru/channel/@{username}"
            
            # Context7: Стратегия 2: Извлечение названия канала
            # Context7: На страницах TGStat название находится в <div class="font-16 text-dark text-truncate">Название</div>
            title = None
            
            # Варианты поиска названия (приоритет селекторам TGStat)
            title_selectors = [
                '.font-16.text-dark',  # Context7: Основной селектор TGStat для названия
                '.font-16.text-dark.text-truncate',  # Context7: С классом text-truncate
                '.text-dark.font-16',  # Context7: Обратный порядок классов
                'div.font-16',  # Context7: Более общий селектор
                '.title',
                '.channel-title',
                '.name',
                'h3',
                'h4',
                'h5',
                '.channel-name',
                '[data-title]',
            ]
            
            for selector in title_selectors:
                title_elem = element.select_one(selector)
                if title_elem:
                    title = title_elem.get_text(strip=True)
                    if title and len(title) >= 2:
                        break
            
            # Context7: Если не нашли, ищем в первой ссылке или тексте элемента
            if not title:
                # Пробуем текст ссылки на канал
                if username_elem:
                    title = username_elem.get_text(strip=True)
                if not title or len(title) < 2:
                    # Пробуем весь текст элемента, но берём первую строку
                    element_text = element.get_text(separator='\n', strip=True)
                    lines = [line.strip() for line in element_text.split('\n') if line.strip()]
                    if lines:
                        # Context7: Берём первую непустую строку, которая не похожа на число подписчиков
                        for line in lines:
                            if not re.match(r'^\d+[\s.,]*(тыс|млн|K|M|подписчик|участник)', line, re.IGNORECASE):
                                title = line[:100]  # Ограничиваем длину
                                break
            
            # Context7: Fallback - используем username как title
            if not title:
                title = username
            
            # Context7: Стратегия 3: Извлечение числа подписчиков
            subscribers = None
            subscribers_text = None
            
            # Context7: Для таблиц - ищем в ячейках (td)
            if element.name == 'tr':
                cells = element.find_all('td')
                for cell in cells:
                    cell_text = cell.get_text(strip=True)
                    # Context7: Ищем числа с единицами измерения
                    patterns = [
                        r'([\d.,]+)\s*[МMм]',
                        r'([\d.,]+)\s*[КKк]',
                        r'([\d.,]+)\s*тыс',
                        r'([\d.,]+)\s*млн',
                        r'([\d\s.,]+)\s*подписчик',
                        r'(\d{1,3}(?:\s?\d{3})*(?:\.\d+)?)',  # Большие числа (150 000, 1 500 000)
                    ]
                    for pattern in patterns:
                        match = re.search(pattern, cell_text, re.IGNORECASE)
                        if match:
                            subscribers_text = match.group(1).strip()
                            # Context7: Проверяем, что это действительно число подписчиков (не индекс или другой номер)
                            if len(subscribers_text.replace('.', '').replace(',', '').replace(' ', '')) >= 3:
                                break
                    if subscribers_text:
                        break
            
            # Context7: Варианты поиска подписчиков через селекторы TGStat
            if not subscribers_text:
                # Context7: На страницах TGStat подписчики в формате: <div class="font-12 text-truncate"><b>58 727</b> подписчиков</div>
                subscribers_selectors = [
                    '.font-12.text-truncate',  # Context7: Основной селектор TGStat для подписчиков
                    '.font-12',  # Context7: Более общий селектор
                    '.subscribers',
                    '.subs',
                    '.subscribers-count',
                    '[data-subscribers]',
                    '.followers',
                    '.members',
                    'td',  # Ячейки таблицы
                    'span[class*="number"]',  # Числовые значения
                    'div[class*="stat"]',  # Статистика
                ]
                
                for selector in subscribers_selectors:
                    subscribers_elem = element.select_one(selector)
                    if subscribers_elem:
                        # Context7: Проверяем HTML и текст элемента
                        html_str = str(subscribers_elem)
                        text = subscribers_elem.get_text(strip=True)
                        
                        # Context7: Ищем паттерн "число подписчиков" в HTML и тексте
                        patterns = [
                            r'<b>(\d{1,3}(?:\s?\d{3})*)</b>\s*подписчик',  # Context7: <b>58 727</b> подписчиков
                            r'(\d{1,3}(?:\s?\d{3})*)\s*подписчик',  # Context7: 58 727 подписчиков
                            r'([\d.,]+)\s*[МMм]',  # Миллионы
                            r'([\d.,]+)\s*[КKк]',  # Тысячи
                            r'([\d.,]+)\s*тыс',  # Тысячи (рус)
                            r'([\d.,]+)\s*млн',  # Миллионы (рус)
                        ]
                        
                        for pattern in patterns:
                            html_match = re.search(pattern, html_str, re.IGNORECASE)
                            text_match = re.search(pattern, text, re.IGNORECASE)
                            match = html_match or text_match
                            if match:
                                subscribers_text = match.group(1).strip()
                                # Context7: Проверяем, что это действительно число подписчиков
                                if len(subscribers_text.replace('.', '').replace(',', '').replace(' ', '')) >= 1:
                                    break
                        if subscribers_text:
                            break
            
            # Context7: Если не нашли через селекторы, ищем по всему тексту элемента
            if not subscribers_text:
                element_text = element.get_text()
                # Паттерны: "1.5M подписчиков", "100K", "500 тыс", "150 000"
                patterns = [
                    r'([\d.,]+)\s*[МMм]',  # Миллионы
                    r'([\d.,]+)\s*[КKк]',  # Тысячи
                    r'([\d.,]+)\s*тыс',  # Тысячи (рус)
                    r'([\d.,]+)\s*млн',  # Миллионы (рус)
                    r'(\d{1,3}(?:\s?\d{3})*(?:\.\d+)?)\s*подписчик',  # Большие числа с "подписчик"
                    r'(\d{1,3}(?:\s?\d{3})*(?:\.\d+)?)',  # Большие числа с пробелами (150 000, 1 500 000)
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, element_text, re.IGNORECASE)
                    if matches:
                        # Context7: Берём самое большое число (вероятно, это подписчики)
                        subscribers_text = max(matches, key=lambda x: float(re.sub(r'[\s,]', '', str(x))))
                        # Context7: Проверяем, что число достаточно большое (минимум 100)
                        num_value = float(re.sub(r'[\s,]', '', subscribers_text))
                        if num_value >= 100:
                            break
                        else:
                            subscribers_text = None
            
            if subscribers_text:
                subscribers = self._parse_subscribers(subscribers_text)
                logger.debug("Subscribers parsed", subscribers=subscribers, text=subscribers_text, theme_slug=theme_slug)
            
            # Context7: Стратегия 4: Извлечение ER (опционально)
            er = None
            
            er_selectors = [
                '.er',
                '.engagement-rate',
                '.engagement',
                '[data-er]',
                '[data-engagement]',
            ]
            
            for selector in er_selectors:
                er_elem = element.select_one(selector)
                if er_elem:
                    er_text = er_elem.get_text(strip=True)
                    if er_text:
                        er = self._parse_er(er_text)
                        if er is not None:
                            break
            
            # Context7: Если не нашли ER, ищем в тексте элемента
            if er is None:
                element_text = element.get_text()
                # Паттерн: "ER: 5.2%", "Engagement: 3.5%"
                er_pattern = r'(?:ER|Engagement)[:\s]+([\d.,]+)\s*%?'
                match = re.search(er_pattern, element_text, re.IGNORECASE)
                if match:
                    er = self._parse_er(match.group(1))
            
            # Context7: Формирование URL канала (TGStat ссылка приоритетна)
            # Context7: Если TGStat ссылка найдена, используем её, иначе формируем
            url = tgstat_url or f"https://tgstat.ru/channel/@{username}"
            
            # Context7: Валидация данных перед возвратом
            # Context7: Если subscribers не найден, устанавливаем 0 (разрешаем сохранять каналы без подписчиков)
            if subscribers is None:
                subscribers = 0
            
            if subscribers < 0:
                logger.warning(
                    "Invalid subscribers count",
                    theme_slug=theme_slug,
                    username=username,
                    subscribers=subscribers
                )
                return None
            
            return {
                'channel_username': username,
                'title': title,
                'subscribers': subscribers,
                'er': er,
                'url': url
            }
            
        except Exception as e:
            logger.warning(
                "Error extracting channel data",
                theme_slug=theme_slug,
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            return None
    
    def _parse_subscribers(self, text: str) -> int:
        """Парсинг числа подписчиков из текста.
        
        Context7: Поддержка различных форматов: "1.5M", "100K", "500", "1,234", "тыс", "млн".
        
        Args:
            text: Текст с числом подписчиков
            
        Returns:
            Число подписчиков
        """
        if not text:
            return 0
        
        # Context7: Очистка текста
        text = text.strip()
        
        # Context7: Удаление пробелов внутри числа (например, "1 234")
        text = text.replace(' ', '')
        
        # Context7: Замена запятой на точку для десятичных чисел
        text = text.replace(',', '.')
        
        # Context7: Нормализация русских обозначений
        text = text.upper()
        text = text.replace('ТЫС', 'K')
        text = text.replace('МЛН', 'M')
        text = text.replace('М', 'M')
        text = text.replace('К', 'K')
        
        # Context7: Извлечение числа и множителя
        # Паттерны: "1.5M", "100K", "500", "1.234"
        number_match = re.search(r'([\d.]+)', text)
        if not number_match:
            return 0
        
        try:
            number = float(number_match.group(1))
            
            # Context7: Определение множителя
            if 'M' in text or 'М' in text:
                return int(number * 1_000_000)
            elif 'K' in text or 'К' in text:
                return int(number * 1_000)
            else:
                return int(number)
        except (ValueError, AttributeError) as e:
            logger.warning("Failed to parse subscribers", text=text, error=str(e))
            return 0
    
    def _parse_er(self, text: str) -> Optional[float]:
        """Парсинг Engagement Rate из текста.
        
        Context7: Поддержка форматов "5.2%", "5.2" и т.д.
        
        Args:
            text: Текст с ER
            
        Returns:
            ER в процентах или None
        """
        if not text:
            return None
        
        # Context7: Очистка текста
        text = text.strip()
        
        # Context7: Удаление символа %
        text = text.replace('%', '')
        
        try:
            er = float(text)
            # Context7: Валидация диапазона (0-100)
            if 0 <= er <= 100:
                return er
            else:
                logger.warning("ER out of range", er=er, text=text)
                return None
        except (ValueError, AttributeError):
            logger.warning("Failed to parse ER", text=text)
            return None
