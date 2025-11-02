"""
Context7 best practice: Утилиты для нормализации значений метрик.
Контроль кардинальности labels, нормализация значений media_type.
"""

from typing import Optional


def normalize_media_type(media_type: str) -> str:
    """
    Нормализация типа медиа для метрик.
    
    Context7 best practice: ограничение кардинальности labels.
    Нормализуем различные варианты к стандартным значениям.
    
    Args:
        media_type: Исходный тип медиа
        
    Returns:
        Нормализованное значение: photo, video, album, doc
    """
    if not media_type:
        return "unknown"
    
    media_type_lower = media_type.lower()
    
    # Нормализация к стандартным значениям
    if media_type_lower in ('photo', 'image', 'jpg', 'jpeg', 'png'):
        return "photo"
    elif media_type_lower in ('video', 'mp4', 'mov', 'avi'):
        return "video"
    elif media_type_lower in ('album', 'group', 'media_group'):
        return "album"
    elif media_type_lower in ('document', 'doc', 'pdf', 'file'):
        return "doc"
    else:
        return "unknown"


def normalize_outcome(success: bool, error: Optional[str] = None) -> str:
    """
    Нормализация исхода операции для метрик.
    
    Args:
        success: Успешность операции
        error: Тип ошибки (если есть)
        
    Returns:
        Нормализованное значение: ok, err
    """
    return "ok" if success else "err"


def normalize_stage(stage: str) -> str:
    """
    Нормализация этапа обработки для метрик.
    
    Args:
        stage: Этап обработки
        
    Returns:
        Нормализованное значение: parse, vision, retag
    """
    stage_lower = stage.lower()
    
    if stage_lower in ('parse', 'parsing', 'download', 'upload'):
        return "parse"
    elif stage_lower in ('vision', 'analyze', 'analysis'):
        return "vision"
    elif stage_lower in ('retag', 'retagging', 're-tag'):
        return "retag"
    else:
        return "unknown"

