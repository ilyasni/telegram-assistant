"""
Feature Flags System with Pydantic Settings and OpenFeature semantics.

[C7-ID: FEATURE-FLAGS-002] Context7 best practice: единая система feature flags
"""

from .flags import (
    FeatureFlags,
    IntegrationFlags,
    DiagnosticFlags,
    ExperimentFlags,
    FlagVariant,
    FlagReason,
    feature_flags,  # Global singleton
)

__all__ = [
    "FeatureFlags",
    "IntegrationFlags",
    "DiagnosticFlags",
    "ExperimentFlags",
    "FlagVariant",
    "FlagReason",
    "feature_flags",
]
