from datetime import datetime, timezone

__all__ = ["ensure_dt_utc"]

def ensure_dt_utc(value: datetime | None) -> datetime | None:
    """Преобразует datetime в формат с UTC таймзоной."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
