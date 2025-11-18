"""
Vision Policy Engine
Context7 best practice: конфигурируемые политики обработки медиа, budget gates, sampling
"""

import logging
from typing import Dict, Any, Optional, List, Tuple
from pathlib import Path

import yaml
import structlog
from prometheus_client import Counter, Gauge

logger = structlog.get_logger()

# ============================================================================
# METRICS
# ============================================================================

vision_policy_decisions_total = Counter(
    'vision_policy_decisions_total',
    'Policy engine decisions',
    ['decision', 'reason']  # allow | deny | skip | fallback
)

vision_policy_sampling_skipped = Counter(
    'vision_policy_sampling_skipped',
    'Images skipped due to sampling policy'
)


class VisionPolicyEngine:
    """
    Vision Policy Engine для управления обработкой медиа.
    
    Features:
    - Проверка лимитов размеров
    - Budget gate проверки
    - Sampling стратегии
    - Routing правила (OCR fallback, skip channels)
    """
    
    def __init__(self, policy_config_path: Optional[str] = None, config: Optional[Dict[str, Any]] = None):
        """
        Инициализация Policy Engine.
        
        Args:
            policy_config_path: Путь к YAML файлу с политикой
            config: Dict конфигурации (переопределяет файл)
        """
        if config:
            self.config = config
        elif policy_config_path:
            self.config = self._load_config(policy_config_path)
        else:
            # Default config
            self.config = self._get_default_config()
        
        logger.info(
            "VisionPolicyEngine initialized",
            limits=self.config.get("limits", {}),
            sampling_enabled=self.config.get("sampling", {}).get("enabled", True)
        )
    
    def _load_config(self, config_path: str) -> Dict[str, Any]:
        """Загрузка конфигурации из YAML файла."""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load vision policy config: {config_path}, error={str(e)}")
            return self._get_default_config()
    
    def _get_default_config(self) -> Dict[str, Any]:
        """Default конфигурация."""
        return {
            "limits": {
                "max_image_mb": 15,
                "max_document_mb": 40,
                "max_daily_tokens_per_tenant": 250000,
                "max_concurrent_requests": 3,
            },
            "sampling": {
                "per_post_max_images": 2,
                "prefer_with_text_overlay": True,
                "skip_small_images_kb": 10,
            },
            "routing": {
                "skip_if_channel": [],
                "skip_if_lang_not_in": ["ru", "en"],
            },
            "fallback": {
                "ocr_engine": "paddle",
            },
            "prompt_presets": {
                "default_key": "default",
                "templates": {
                    "default": "Верни JSON {\"classification\": \"photo|meme|document|screenshot|infographic|other\", \"description\": \"<=160 симв.\", \"is_meme\": bool, \"labels\": [], \"objects\": [], \"ocr\": {\"text\": \"...\", \"engine\": \"gigachat\"} или null}. Если есть текст — заполни ocr.text. Без пояснений.",
                    "document": "Документ: JSON как в default. classification='document'. Выдели ключевые заголовки. OCR text обязателен.",
                    "screenshot": "Скриншот: JSON как в default. classification='screenshot'. Укажи основное содержание интерфейса. OCR text обязателен.",
                    "photo": "Фото/инфографика: JSON как в default. Сфокусируйся на описании сцены. Если есть текст — добавь его в ocr.",
                },
                "mime_mapping": {
                    "application/pdf": "document",
                    "application/msword": "document",
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",
                    "image/png": "screenshot",
                    "image/jpeg": "photo",
                    "image/jpg": "photo",
                    "image/webp": "photo",
                    "image/bmp": "photo",
                },
            },
        }
    
    def check_file_size(self, size_bytes: int, mime_type: str) -> Tuple[bool, Optional[str]]:
        """
        Проверка размера файла по MIME типу.
        
        Returns:
            (allowed, reason_if_denied)
        """
        limits = self.config.get("limits", {})
        
        # Определяем тип контента
        if mime_type.startswith("image/"):
            max_mb = limits.get("max_image_mb", 15)
            content_type = "image"
        elif mime_type.startswith("application/") or mime_type.startswith("text/"):
            max_mb = limits.get("max_document_mb", 40)
            content_type = "document"
        else:
            return False, "unsupported_mime_type"
        
        max_bytes = max_mb * 1024 * 1024
        
        if size_bytes > max_bytes:
            vision_policy_decisions_total.labels(decision="deny", reason="file_too_large").inc()
            return False, f"{content_type}_too_large"
        
        return True, None
    
    def check_allowed_mime(self, mime_type: str) -> bool:
        """Проверка, поддерживается ли MIME тип."""
        allowed = self.config.get("allowed_mime_types", {})
        images = allowed.get("images", [])
        documents = allowed.get("documents", [])
        
        return mime_type in (images + documents)
    
    def should_sample(
        self,
        media_files: List[Dict[str, Any]],
        post_tags: Optional[List[str]] = None
    ) -> Tuple[List[Dict[str, Any]], str]:
        """
        Sampling: выбор подмножества медиа для обработки.
        
        Args:
            media_files: Список медиа файлов
            post_tags: Теги поста (для определения приоритетов)
            
        Returns:
            (selected_files, reason)
        """
        sampling = self.config.get("sampling", {})
        
        if not sampling.get("enabled", True):
            return media_files, "sampling_disabled"
        
        per_post_max = sampling.get("per_post_max_images", 2)
        
        if len(media_files) <= per_post_max:
            return media_files, "within_limit"
        
        # Применяем sampling
        skip_small_kb = sampling.get("skip_small_images_kb", 10)
        prefer_text = sampling.get("prefer_with_text_overlay", True)
        
        # Фильтруем маленькие изображения
        filtered = [
            f for f in media_files
            if f.get("size_bytes", 0) >= skip_small_kb * 1024
        ]
        
        if len(filtered) <= per_post_max:
            vision_policy_sampling_skipped.inc(len(media_files) - len(filtered))
            return filtered, "small_images_filtered"
        
        # Приоритет: предполагаем, что мемы/скриншоты имеют больший размер
        # (упрощённая эвристика, реальная должна быть через предварительную классификацию)
        if prefer_text:
            sorted_files = sorted(
                filtered,
                key=lambda x: x.get("size_bytes", 0),
                reverse=True
            )
        else:
            sorted_files = filtered
        
        selected = sorted_files[:per_post_max]
        
        vision_policy_sampling_skipped.inc(len(media_files) - len(selected))
        vision_policy_decisions_total.labels(decision="sampling", reason="per_post_limit").inc()
        
        logger.debug(
            "Sampling applied",
            original_count=len(media_files),
            selected_count=len(selected),
            reason="per_post_limit"
        )
        
        return selected, "sampling_applied"
    
    def should_skip_channel(self, channel_username: Optional[str]) -> bool:
        """Проверка, нужно ли пропустить канал."""
        skip_list = self.config.get("routing", {}).get("skip_if_channel", [])
        if not skip_list:
            return False
        
        return channel_username in skip_list
    
    def should_use_ocr_fallback(
        self,
        quota_exhausted: bool,
        mime_type: str
    ) -> bool:
        """
        Определение, использовать ли OCR fallback.
        
        Args:
            quota_exhausted: Исчерпана ли квота Vision API
            mime_type: MIME тип файла
            
        Returns:
            True если нужен OCR fallback
        """
        routing = self.config.get("routing", {})
        ocr_conditions = routing.get("ocr_only_if", [])
        
        # Проверка quota_exhausted
        if quota_exhausted and "quota_exhausted" in ocr_conditions:
            vision_policy_decisions_total.labels(decision="fallback", reason="quota_exhausted").inc()
            return True
        
        # Проверка MIME типа
        ocr_mime_list = None
        for condition in ocr_conditions:
            if isinstance(condition, dict) and "mime" in condition:
                ocr_mime_list = condition["mime"]
                break
        
        if ocr_mime_list and mime_type in ocr_mime_list:
            vision_policy_decisions_total.labels(decision="fallback", reason="mime_type").inc()
            return True
        
        return False
    
    def evaluate_media_for_vision(
        self,
        media_file: Dict[str, Any],
        channel_username: Optional[str] = None,
        quota_exhausted: bool = False
    ) -> Dict[str, Any]:
        """
        Комплексная оценка медиа для Vision анализа.
        
        Returns:
            {
                "allowed": bool,
                "reason": str,
                "use_ocr": bool,
                "skip": bool
            }
        """
        mime_type = media_file.get("mime_type", "")
        size_bytes = media_file.get("size_bytes", 0)
        
        result = {
            "allowed": False,
            "reason": None,
            "use_ocr": False,
            "skip": False,
            "prompt_key": self._resolve_prompt_key(mime_type),
        }
        
        # Проверка 1: MIME тип
        if not self.check_allowed_mime(mime_type):
            result["reason"] = "unsupported_mime"
            vision_policy_decisions_total.labels(decision="deny", reason="unsupported_mime").inc()
            return result
        
        # Проверка 2: Размер файла
        size_ok, size_reason = self.check_file_size(size_bytes, mime_type)
        if not size_ok:
            result["reason"] = size_reason
            return result
        
        # Проверка 3: Channel skip list
        if self.should_skip_channel(channel_username):
            result["skip"] = True
            result["reason"] = "channel_skipped"
            vision_policy_decisions_total.labels(decision="skip", reason="channel").inc()
            return result
        
        # Проверка 4: OCR fallback
        if self.should_use_ocr_fallback(quota_exhausted, mime_type):
            result["use_ocr"] = True
            result["allowed"] = True  # Разрешено, но через OCR
            return result
        
        # Все проверки пройдены
        result["allowed"] = True
        result["reason"] = "policy_passed"
        vision_policy_decisions_total.labels(decision="allow", reason="policy_passed").inc()
        
        return result

    def _resolve_prompt_key(self, mime_type: str) -> str:
        presets = self.config.get("prompt_presets", {})
        default_key = presets.get("default_key", "default")
        mapping = presets.get("mime_mapping", {})
        if not mime_type:
            return default_key
        mime_lower = mime_type.lower()
        if mime_lower in mapping:
            return mapping[mime_lower]
        family = mime_lower.split("/")[0] + "/*"
        return mapping.get(family, default_key)

    def get_prompt_template(self, prompt_key: Optional[str] = None) -> Optional[str]:
        presets = self.config.get("prompt_presets", {})
        templates = presets.get("templates", {})
        default_key = presets.get("default_key", "default")
        key = prompt_key or default_key
        return templates.get(key) or templates.get(default_key)

