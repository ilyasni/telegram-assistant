"""
S3 Storage Service - Re-export from shared package for backward compatibility.

[C7-ID: ARCH-SHARED-001] Context7: Перемещено в shared/s3_storage для соблюдения архитектурных границ.
Этот файл оставлен для обратной совместимости и переадресует импорт в shared пакет.

DEPRECATED: Используйте `from shared.s3_storage import S3StorageService` вместо `from services.s3_storage import S3StorageService`
"""

import warnings

# Context7: Re-export из shared пакета для обратной совместимости
try:
    from shared.s3_storage import S3StorageService
    
    # Предупреждение при использовании deprecated импорта
    warnings.warn(
        "Importing S3StorageService from 'api.services.s3_storage' is deprecated. "
        "Use 'from shared.s3_storage import S3StorageService' instead.",
        DeprecationWarning,
        stacklevel=2
    )
except ImportError:
    # Fallback для случаев, когда shared пакет не установлен
    import sys
    import os
    
    # Пытаемся добавить shared в путь
    shared_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'shared', 'python'))
    if shared_path not in sys.path:
        sys.path.insert(0, shared_path)
    
    try:
        from shared.s3_storage import S3StorageService
    except ImportError:
        # Критическая ошибка - shared пакет должен быть установлен
        raise ImportError(
            "S3StorageService moved to shared package. "
            "Please install shared package: pip install -e ./shared/python"
        )

__all__ = ['S3StorageService']
