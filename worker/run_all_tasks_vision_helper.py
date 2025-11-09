"""
Context7: Helper функция для создания VisionAnalysisTask из env переменных.
"""
import os
from typing import Dict, Any


def get_s3_config_from_env() -> Dict[str, Any]:
    """Context7: Получение S3 конфигурации из переменных окружения."""
    return {
        "endpoint_url": os.getenv("S3_ENDPOINT_URL", "https://s3.cloud.ru"),
        "access_key_id": os.getenv("S3_ACCESS_KEY_ID", ""),
        "secret_access_key": os.getenv("S3_SECRET_ACCESS_KEY", ""),
        "bucket_name": os.getenv("S3_BUCKET_NAME", ""),
        "region": os.getenv("S3_REGION", "ru-central-1"),
        "use_compression": os.getenv("S3_USE_COMPRESSION", "true").lower() == "true",
        "limits": {
            "total_gb": float(os.getenv("S3_STORAGE_LIMIT_GB", "15.0")),
            "emergency_threshold_gb": float(os.getenv("S3_EMERGENCY_THRESHOLD_GB", "14.0")),
            "per_tenant_gb": float(os.getenv("S3_PER_TENANT_LIMIT_GB", "2.0"))
        }
    }


def get_vision_config_from_env() -> Dict[str, Any]:
    """
    Context7: Получение Vision конфигурации из переменных окружения.
    
    Поддерживает два формата credentials согласно документации gpt2giga:
    1. GIGACHAT_CREDENTIALS (base64 или plain) + GIGACHAT_SCOPE
    2. GIGACHAT_CLIENT_ID + GIGACHAT_CLIENT_SECRET
    """
    gigachat_credentials = os.getenv("GIGACHAT_CREDENTIALS", "")
    gigachat_scope = os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS")
    client_id = os.getenv("GIGACHAT_CLIENT_ID", "")
    client_secret = os.getenv("GIGACHAT_CLIENT_SECRET", "")
    
    # Context7: Приоритет - GIGACHAT_CREDENTIALS (согласно документации gpt2giga)
    credentials_string = None
    if gigachat_credentials:
        # Формат: credentials уже готовы (base64 или plain string)
        # GigaChatVisionAdapter ожидает строку credentials
        credentials_string = gigachat_credentials
    elif client_id and client_secret:
        # Fallback: собираем из client_id:client_secret в base64
        import base64
        credentials_plain = f"{client_id}:{client_secret}"
        credentials_string = base64.b64encode(credentials_plain.encode('utf-8')).decode('utf-8')
    else:
        raise ValueError(
            "GigaChat credentials не установлены. "
            "Установите GIGACHAT_CREDENTIALS + GIGACHAT_SCOPE "
            "или GIGACHAT_CLIENT_ID + GIGACHAT_CLIENT_SECRET"
        )
    
    # Context7: Формируем config для GigaChatVisionAdapter
    # credentials должен быть строкой (base64 encoded credentials)
    vision_config = {
        "credentials": credentials_string,
        "scope": gigachat_scope,
        "model": os.getenv("GIGACHAT_VISION_MODEL", "GigaChat-Pro"),
        "base_url": os.getenv("GIGACHAT_BASE_URL", "https://gigachat.devices.sberbank.ru/api/v1"),
        "verify_ssl": os.getenv("GIGACHAT_VERIFY_SSL", "false").lower() == "true",
        "timeout": int(os.getenv("GIGACHAT_VISION_TIMEOUT", "600")),
        "max_daily_tokens": int(os.getenv("GIGACHAT_MAX_DAILY_TOKENS", "1250000")),  # Context7: Увеличен в 5 раз (250000 * 5)
        "max_concurrent": int(os.getenv("GIGACHAT_MAX_CONCURRENT_REQUESTS", "3")),
        "policy_config_path": os.getenv("VISION_POLICY_CONFIG_PATH", "/app/config/vision_policy.yml"),
        "ocr_fallback_enabled": os.getenv("VISION_OCR_FALLBACK_ENABLED", "true").lower() == "true",
        "ocr_engine": os.getenv("VISION_OCR_ENGINE", "paddle"),
        "ocr_languages": os.getenv("VISION_OCR_LANGUAGES", "rus+eng"),
        "paddle_endpoint": os.getenv("LOCAL_OCR_ENDPOINT", "http://paddleocr:8008/v1/ocr"),
        "paddle_timeout": float(os.getenv("LOCAL_OCR_TIMEOUT", "8.0")),
        "openrouter_model": os.getenv("OPENROUTER_VISION_MODEL", "qwen/qwen2.5-vl-32b-instruct:free"),
        "local_ocr_primary_enabled": os.getenv("VISION_LOCAL_OCR_PRIMARY_ENABLED", "true").lower() == "true",
        "dlq_enabled": os.getenv("VISION_DLQ_ENABLED", "true").lower() == "true",
        "roi_crop_enabled": os.getenv("VISION_ROI_CROP_ENABLED", "false").lower() == "true",
        "low_priority_queue_enabled": os.getenv("VISION_LOW_PRIORITY_QUEUE_ENABLED", "false").lower() == "true"
    }
    
    return vision_config

