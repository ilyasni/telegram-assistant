"""
URL Canonicalizer для дедупликации и нормализации URL
Context7 best practice: нормализация для content-addressed storage
"""

import hashlib
import logging
from urllib.parse import urlparse, urlunparse, parse_qs, urlencode
from typing import Optional

import structlog

logger = structlog.get_logger()


class URLCanonicalizer:
    """
    Каноникализация URL для дедупликации в S3.
    
    Rules:
    1. Lower case scheme/host
    2. Remove #fragment
    3. Remove utm_* query params
    4. Sort query params
    5. Normalize port (443→ø, 80→ø for https/http)
    6. Remove trailing slash (optional, configurable)
    """
    
    def __init__(self, remove_trailing_slash: bool = True):
        self.remove_trailing_slash = remove_trailing_slash
    
    def canonicalize(self, url: str) -> str:
        """
        Каноникализация URL.
        
        Args:
            url: Исходный URL
            
        Returns:
            Каноникализованный URL
        """
        try:
            parsed = urlparse(url)
            
            # 1. Lower case scheme и host
            scheme = parsed.scheme.lower()
            netloc = parsed.netloc.lower()
            
            # 2. Normalize port (443→ø для https, 80→ø для http)
            if ':' in netloc:
                host, port = netloc.rsplit(':', 1)
                if scheme == 'https' and port == '443':
                    netloc = host
                elif scheme == 'http' and port == '80':
                    netloc = host
            
            # 3. Path normalization
            path = parsed.path
            if self.remove_trailing_slash and path.endswith('/') and len(path) > 1:
                path = path.rstrip('/')
            
            # 4. Query params: remove utm_*, sort остальные
            query_params = parse_qs(parsed.query, keep_blank_values=False)
            
            # Удаляем UTM и другие tracking параметры
            tracking_params = [
                'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
                'fbclid', 'gclid', 'ref', 'source', 'campaign'
            ]
            
            cleaned_params = {
                k: v for k, v in query_params.items()
                if not any(tp in k.lower() for tp in tracking_params)
            }
            
            # Сортируем и кодируем
            if cleaned_params:
                sorted_params = sorted(cleaned_params.items())
                query = urlencode(sorted_params, doseq=True)
            else:
                query = ''
            
            # 5. Fragment всегда удаляем
            fragment = ''
            
            # Собираем каноникализованный URL
            canonical = urlunparse((scheme, netloc, path, parsed.params, query, fragment))
            
            return canonical
            
        except Exception as e:
            logger.warning("Failed to canonicalize URL", url=url, error=str(e))
            # В случае ошибки возвращаем исходный URL
            return url
    
    def compute_hash(self, canonical_url: str) -> str:
        """
        Вычисление SHA256 хеша каноникализованного URL.
        
        Args:
            canonical_url: Каноникализованный URL
            
        Returns:
            SHA256 hex digest (64 символа)
        """
        return hashlib.sha256(canonical_url.encode('utf-8')).hexdigest()
    
    def canonicalize_and_hash(self, url: str) -> tuple[str, str]:
        """
        Каноникализация и хеширование URL за один вызов.
        
        Returns:
            (canonical_url, hash)
        """
        canonical = self.canonicalize(url)
        url_hash = self.compute_hash(canonical)
        return canonical, url_hash

