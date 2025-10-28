"""Context7: Утилиты для безопасной работы с datetime и timezone."""
from datetime import datetime, timezone
from typing import Optional

def ensure_dt_utc(x) -> Optional[datetime]:
    """
    Безопасная конвертация любого типа в aware datetime UTC.
    
    Context7 (redis-py + psycopg2): 
    - Redis возвращает bytes/str (может быть +0000 без двоеточия)
    - PostgreSQL (psycopg2) возвращает datetime напрямую
    - Защита от вызова fromisoformat на datetime объекте
    - Обработка пустых строк и нестандартных форматов
    
    Args:
        x: datetime | str | bytes | None
        
    Returns:
        aware datetime UTC или None при ошибке
    """
    if x is None:
        return None
    if isinstance(x, datetime):
        return x if x.tzinfo else x.replace(tzinfo=timezone.utc)
    if isinstance(x, (bytes, bytearray)):
        x = x.decode("utf-8", errors="ignore")
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return None
        s = s.replace('Z', '+00:00')
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            # Fallback: обработка +0000 без двоеточия (2025-10-28T05:00:00+0000)
            if len(s) > 5 and (s[-5] in ['+', '-']) and ':' not in s[-5:]:
                try:
                    dt = datetime.fromisoformat(s[:-2] + ':' + s[-2:])
                except Exception:
                    return None
            else:
                return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None

