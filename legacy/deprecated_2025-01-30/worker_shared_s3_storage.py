"""
⚠️ DEPRECATED ⚠️

@deprecated since=2025-01-30 remove_by=2025-02-13
Reason: Точный дубликат api/services/s3_storage.py
Replacement: from api.services.s3_storage import S3StorageService

Этот файл перемещён из worker/shared/s3_storage.py в legacy/
[C7-ID: CODE-CLEANUP-025] Context7 best practice: карантин deprecated кода
"""

import warnings
import os

# Runtime guard: блокируем импорт в production
if os.getenv("ENV") == "production":
    raise ImportError(
        "worker/shared/s3_storage.py is deprecated (duplicate of api/services/s3_storage.py). "
        "Use 'from api.services.s3_storage import S3StorageService' instead. "
        "See legacy/deprecated_2025-01-30/README.md"
    )

warnings.warn(
    "worker/shared/s3_storage.py is DEPRECATED (duplicate). "
    "Use api/services/s3_storage.py instead. "
    "See legacy/deprecated_2025-01-30/README.md",
    DeprecationWarning,
    stacklevel=2
)

# Re-export для обратной совместимости (redirect to api.services)
try:
    from api.services.s3_storage import *
except ImportError:
    # Если api.services недоступен, показываем ошибку
    raise ImportError(
        "Cannot import from api.services.s3_storage. "
        "Please ensure api.services.s3_storage is available."
    )
