"""
Утилиты для Telegram bot handlers.

Context7: Общие функции для обработчиков бота, избегаем дублирования кода.
"""

import re
from typing import Optional


def extract_username_from_telegram_url(text: str) -> Optional[str]:
    """
    Извлекает username из Telegram URL или username.
    
    Context7: Поддерживает различные форматы:
    - https://t.me/username
    - http://t.me/username
    - t.me/username
    - @username
    - username
    
    Args:
        text: Текст, содержащий URL или username
        
    Returns:
        Username без @ или None, если не удалось извлечь
    """
    if not text:
        return None
    
    text = text.strip()
    
    # Убираем @ если есть
    if text.startswith('@'):
        username = text[1:]
        # Валидация username (только буквы, цифры, подчёркивания, 5-32 символа)
        if re.match(r'^[a-zA-Z0-9_]{5,32}$', username):
            return username
        return None
    
    # Парсинг URL
    # Паттерн для https://t.me/username или http://t.me/username
    url_pattern = r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/([a-zA-Z0-9_]{5,32})'
    match = re.search(url_pattern, text)
    if match:
        username = match.group(1)
        return username
    
    # Если это просто username без @
    if re.match(r'^[a-zA-Z0-9_]{5,32}$', text):
        return text
    
    return None

