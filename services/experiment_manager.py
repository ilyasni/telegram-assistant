"""Vision experiment manager for A/B control (Context7)."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from typing import Dict, Optional

import yaml

try:
    import structlog

    logger = structlog.get_logger()
except ImportError:  # pragma: no cover
    import logging

    logger = logging.getLogger(__name__)


@dataclass
class ExperimentDefinition:
    name: str
    enabled: bool = True
    salt: str = ""
    default_variant: str = "control"
    variants: Dict[str, float] = field(default_factory=lambda: {"control": 1.0})
    forced_assignments: Dict[str, str] = field(default_factory=dict)
    excluded_tenants: set[str] = field(default_factory=set)


class VisionExperimentManager:
    """Deterministic experiment assignment per tenant."""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path
        self.experiments: Dict[str, ExperimentDefinition] = {}
        self._load_config()

    def _load_config(self) -> None:
        if not self.config_path or not os.path.exists(self.config_path):
            logger.info(
                "Vision experiments config not found",
                path=self.config_path,
                experiments_enabled=False,
            )
            return

        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                raw_config = yaml.safe_load(f) or {}
        except Exception as exc:  # pragma: no cover - config I/O error
            logger.error(
                "Failed to read vision experiments config",
                path=self.config_path,
                error=str(exc),
            )
            return

        experiments_cfg = raw_config.get("experiments", {})
        for name, cfg in experiments_cfg.items():
            definition = self._normalize_definition(name, cfg or {})
            self.experiments[name] = definition

        logger.info(
            "Vision experiments loaded",
            path=self.config_path,
            experiments=list(self.experiments.keys()),
        )

    def _normalize_definition(self, name: str, cfg: Dict[str, object]) -> ExperimentDefinition:
        enabled = bool(cfg.get("enabled", True))
        salt = cfg.get("salt") or name
        default_variant = cfg.get("default_variant") or "control"

        variants_cfg = cfg.get("variants") or {"control": 0.5, "experiment": 0.5}
        weights: Dict[str, float] = {}
        total_weight = 0.0
        for variant, weight in variants_cfg.items():
            try:
                value = float(weight)
            except (TypeError, ValueError):  # pragma: no cover - invalid config
                value = 0.0
            weights[variant] = max(value, 0.0)
            total_weight += weights[variant]

        if total_weight <= 0:
            weights = {"control": 1.0}
            total_weight = 1.0

        normalized_variants = {
            variant: weight / total_weight for variant, weight in weights.items()
        }

        forced_assignments = cfg.get("force_tenants", {}) or {}
        excluded_tenants = set(cfg.get("exclude_tenants", []) or [])

        return ExperimentDefinition(
            name=name,
            enabled=enabled,
            salt=str(salt),
            default_variant=str(default_variant),
            variants=normalized_variants,
            forced_assignments={str(k): str(v) for k, v in forced_assignments.items()},
            excluded_tenants={str(tenant) for tenant in excluded_tenants},
        )

    def assign(self, experiment: str, tenant_id: Optional[str]) -> str:
        definition = self.experiments.get(experiment)
        if not definition or not definition.enabled or not tenant_id:
            return definition.default_variant if definition else "control"

        if tenant_id in definition.excluded_tenants:
            return definition.default_variant

        forced_variant = definition.forced_assignments.get(tenant_id)
        if forced_variant:
            return forced_variant

        hash_input = f"{definition.salt}:{tenant_id}"
        hash_bytes = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()
        scale = int(hash_bytes[:8], 16) / 0xFFFFFFFF

        cumulative = 0.0
        selected_variant = definition.default_variant
        for variant, weight in definition.variants.items():
            cumulative += weight
            if scale < cumulative:
                selected_variant = variant
                break

        return selected_variant

    def assign_all(self, tenant_id: Optional[str]) -> Dict[str, str]:
        if not tenant_id:
            return {}
        return {
            name: self.assign(name, tenant_id)
            for name, definition in self.experiments.items()
            if definition.enabled
        }


