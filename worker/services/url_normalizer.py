"""
URL Normalizer Service
Context7 best practice: нормализация URL для дедупликации, экстракция, расширение коротких ссылок
"""

import re
import urllib.parse
from typing import List, Optional
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode, unquote

import structlog

logger = structlog.get_logger()

# Короткие домены для расширения (опционально)
SHORTENER_DOMAINS = {
    't.me', 'bit.ly', 't.co', 'tinyurl.com', 'goo.gl', 'ow.ly', 'is.gd',
    'short.link', 'clck.ru', 'vk.cc', 'ya.ru', 'qr.ae'
}


class URLNormalizer:
    """
    Сервис для нормализации и обработки URL.
    
    Features:
    - Нормализация URL (lower host, punycode, strip utm/gclid, rstrip '/')
    - Нормализация мобильных зеркал (m., amp.) → канонический хост
    - Экстракция URL из текста (Markdown, Telegram форматы)
    - Разворачивание коротких ссылок (опционально)
    """
    
    def __init__(self, strip_params: Optional[List[str]] = None):
        """
        Инициализация URL Normalizer.
        
        Args:
            strip_params: Список параметров для удаления (по умолчанию utm_*, gclid, fbclid)
        """
        self.strip_params = strip_params or ['utm_source', 'utm_medium', 'utm_campaign', 
                                             'utm_term', 'utm_content', 'gclid', 'fbclid']
        # Поддержка utm_* как паттерна
        self.strip_patterns = [p for p in self.strip_params if '*' in p]
        self.strip_exact = [p for p in self.strip_params if '*' not in p]
    
    def normalize_url(self, url: str, strip_params: Optional[List[str]] = None) -> str:
        """
        Нормализация URL для дедупликации.
        
        Правила:
        - lower host, punycode
        - strip utm/gclid/fbclid параметры
        - rstrip '/' (если не root)
        - нормализация мобильных зеркал (m., amp.) → канонический хост
        
        Args:
            url: Исходный URL
            strip_params: Дополнительные параметры для удаления (переопределяет self.strip_params)
            
        Returns:
            Нормализованный URL
        """
        try:
            parsed = urlparse(url)
            
            # 1. Lower case scheme и host
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            
            # 2. Нормализация мобильных зеркал
            # m.example.com → example.com
            # amp.example.com → example.com
            if netloc.startswith('m.') and len(netloc) > 2:
                netloc = netloc[2:]
            elif netloc.startswith('amp.') and len(netloc) > 4:
                netloc = netloc[4:]
            
            # 3. Path normalization
            path = parsed.path
            # Удаляем trailing slash (кроме root)
            if path.endswith('/') and len(path) > 1:
                path = path.rstrip('/')
            
            # 4. Query params: удаляем tracking параметры
            params_to_strip = strip_params or self.strip_params
            if parsed.query:
                query_params = parse_qs(parsed.query, keep_blank_values=False)
                
                # Удаляем точные совпадения
                exact_strip = [p for p in params_to_strip if '*' not in p]
                cleaned_params = {k: v for k, v in query_params.items() 
                                 if k not in exact_strip}
                
                # Удаляем по паттернам (utm_*)
                pattern_strip = [p.replace('*', '') for p in params_to_strip if '*' in p]
                for pattern in pattern_strip:
                    cleaned_params = {k: v for k, v in cleaned_params.items() 
                                     if not k.startswith(pattern)}
                
                # Сортируем и кодируем
                if cleaned_params:
                    sorted_params = sorted(cleaned_params.items())
                    query = urlencode(sorted_params, doseq=True)
                else:
                    query = ''
            else:
                query = ''
            
            # 5. Fragment всегда удаляем
            fragment = ''
            
            # Собираем нормализованный URL
            normalized = urlunparse((scheme, netloc, path, parsed.params, query, fragment))
            
            return normalized
            
        except Exception as e:
            logger.warning("Failed to normalize URL", url=url, error=str(e))
            # В случае ошибки возвращаем исходный URL
            return url
    
    def extract_urls_from_text(self, text: str) -> List[str]:
        """
        Извлечение URL из текста с поддержкой различных форматов.
        
        Поддерживает:
        - Markdown форматы: `](url)`, `"url"`, `(url)`
        - Telegram форматы: `](url)`, `"url"`
        - Обычные URL: `https://example.com`
        - Авто-декод %-escape
        
        Args:
            text: Текст для поиска URL
            
        Returns:
            Список нормализованных URL
        """
        if not text:
            return []
        
        urls = []
        
        # Паттерн 1: Markdown ссылки [text](url)
        markdown_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        for match in re.finditer(markdown_pattern, text):
            url = match.group(2)
            # Декодируем %-escape
            url = unquote(url)
            # Нормализуем
            normalized = self.normalize_url(url)
            if normalized and normalized not in urls:
                urls.append(normalized)
        
        # Паттерн 2: URL в кавычках "url" или 'url'
        quoted_pattern = r'["\'](https?://[^"\']+)["\']'
        for match in re.finditer(quoted_pattern, text):
            url = match.group(1)
            url = unquote(url)
            normalized = self.normalize_url(url)
            if normalized and normalized not in urls:
                urls.append(normalized)
        
        # Паттерн 3: URL в скобках (url)
        parens_pattern = r'\((https?://[^)]+)\)'
        for match in re.finditer(parens_pattern, text):
            url = match.group(1)
            url = unquote(url)
            normalized = self.normalize_url(url)
            if normalized and normalized not in urls:
                urls.append(normalized)
        
        # Паттерн 4: Обычные URL (https://...)
        url_pattern = r'https?://[^\s)\]]+'
        for match in re.finditer(url_pattern, text):
            url = match.group(0)
            # Убираем trailing punctuation
            url = url.rstrip('.,;:!?')
            url = unquote(url)
            normalized = self.normalize_url(url)
            if normalized and normalized not in urls:
                urls.append(normalized)
        
        return urls
    
    def expand_short_url(self, url: str) -> Optional[str]:
        """
        Разворачивание коротких ссылок (опционально через feature flag).
        
        Args:
            url: Короткий URL
            
        Returns:
            Развёрнутый URL или None если не удалось
        """
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # Проверяем, является ли домен коротким
            if domain not in SHORTENER_DOMAINS:
                return None
            
            # TODO: Реализовать разворачивание через HEAD запрос с redirect following
            # Пока возвращаем None (требует HTTP клиента)
            logger.debug("Short URL expansion not implemented", url=url, domain=domain)
            return None
            
        except Exception as e:
            logger.warning("Failed to expand short URL", url=url, error=str(e))
            return None
    
    def get_domain(self, url: str) -> Optional[str]:
        """
        Извлечение домена из URL.
        
        Args:
            url: URL
            
        Returns:
            Домен или None
        """
        try:
            parsed = urlparse(url)
            return parsed.netloc.lower()
        except Exception:
            return None

