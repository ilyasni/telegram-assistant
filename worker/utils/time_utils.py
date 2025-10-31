from datetime import datetime, timezone
from typing import Union, Optional

def ensure_dt_utc(dt: Union[str, datetime, None]) -> Optional[datetime]:
    """
    Context7: Безопасная утилита для парсинга дат с гарантией UTC timezone.
    
    Args:
        dt: Строка даты, datetime объект или None
        
    Returns:
        datetime объект с UTC timezone или None
        
    Raises:
        ValueError: Если строка даты не может быть распарсена
    """
    if dt is None:
        return None
    
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            # Naive datetime - присваиваем UTC
            return dt.replace(tzinfo=timezone.utc)
        else:
            # Datetime с timezone - конвертируем в UTC
            return dt.astimezone(timezone.utc)
    
    if isinstance(dt, str):
        # Убираем 'Z' в конце и заменяем на '+00:00' для совместимости с fromisoformat
        if dt.endswith('Z'):
            dt = dt[:-1] + '+00:00'
        
        # Парсим ISO формат
        try:
            parsed_dt = datetime.fromisoformat(dt)
        except ValueError:
            # Пробуем другие форматы
            try:
                parsed_dt = datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    parsed_dt = datetime.strptime(dt, '%Y-%m-%dT%H:%M:%S')
                except ValueError:
                    raise ValueError(f"Unable to parse datetime string: {dt}")
        
        # Убеждаемся, что timezone UTC
        if parsed_dt.tzinfo is None:
            parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
        else:
            parsed_dt = parsed_dt.astimezone(timezone.utc)
        
        return parsed_dt
    
    raise ValueError(f"Unsupported datetime type: {type(dt)}")
