"""
Централизованная система депрекейтов с метриками и логированием.

[C7-ID: CODE-CLEANUP-030] Context7 best practice: единая система депрекейтов
"""

import warnings
from datetime import datetime
from typing import Optional
import structlog

logger = structlog.get_logger()

# Метрики Prometheus (ленивая инициализация)
_metrics_initialized = False
_deprecation_counter = None


def _init_metrics():
    """Ленивая инициализация метрик Prometheus."""
    global _metrics_initialized, _deprecation_counter
    
    if _metrics_initialized:
        return
    
    try:
        from prometheus_client import Counter
        _deprecation_counter = Counter(
            'deprecation_warnings_total',
            'Total number of deprecation warnings',
            ['module', 'replacement']
        )
        _metrics_initialized = True
    except ImportError:
        # Prometheus не доступен, работаем без метрик
        pass


def warn_deprecated(
    module: str,
    replacement: str,
    remove_by: Optional[str] = None,
    stacklevel: int = 2
) -> None:
    """
    Выдать предупреждение о депрекейте с метриками и логированием.
    
    Args:
        module: Имя устаревшего модуля/функции
        replacement: Рекомендуемая замена
        remove_by: Дата удаления (YYYY-MM-DD) или None
        stacklevel: Уровень стека для warnings.warn
    """
    # Инициализация метрик
    _init_metrics()
    
    # Формирование сообщения
    message = f"{module} is DEPRECATED. Use {replacement} instead."
    if remove_by:
        message += f" Will be removed by {remove_by}."
    
    # Выдача предупреждения
    warnings.warn(
        message,
        DeprecationWarning,
        stacklevel=stacklevel
    )
    
    # Логирование
    logger.warning(
        "deprecation_warning",
        module=module,
        replacement=replacement,
        remove_by=remove_by,
        stacklevel=stacklevel
    )
    
    # Метрика Prometheus
    if _deprecation_counter is not None:
        _deprecation_counter.labels(
            module=module,
            replacement=replacement
        ).inc()


def check_removal_date(remove_by: Optional[str]) -> bool:
    """
    Проверить, не прошла ли дата удаления.
    
    Args:
        remove_by: Дата удаления (YYYY-MM-DD) или None
        
    Returns:
        True если дата прошла, False иначе
    """
    if not remove_by:
        return False
    
    try:
        removal_date = datetime.strptime(remove_by, "%Y-%m-%d")
        return datetime.now() > removal_date
    except ValueError:
        logger.error("invalid_removal_date", remove_by=remove_by)
        return False

