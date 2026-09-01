"""
AI Act Compliance Architecture - Feature Registry and Policy Engine.

This module provides the compliance-by-design infrastructure for AI features
in Gestor-IA. It implements feature-level classification, AI use policies,
traceability, data minimisation, human oversight, transparency, retention,
HR firewall, and runtime versioning.

Technical compliance architecture only. Legal classification must be reviewed
when features, intended purposes, deployment contexts or applicable regulations change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from enum import StrEnum
from typing import Any
from uuid import uuid4


# =========================================================================
# AI FEATURE CLASSIFICATION
# =========================================================================

class AIRiskClassification(StrEnum):
    """Internal risk classification for AI features (not legal classification)."""
    MINIMAL = "minimal"           # No special controls required
    LIMITED = "limited"           # Transparency + human oversight
    HIGH = "high"                 # Full compliance controls required
    UNACCEPTABLE = "unacceptable" # Must be disabled


class AIUsePolicy(StrEnum):
    """Policy for AI provider routing and data handling."""
    ALLOW = "allow"                           # AI allowed with standard controls
    DENY = "deny"                             # AI blocked for this feature
    REQUIRE_HUMAN_OVERSIGHT = "require_human_oversight"  # Human must confirm output
    LOCAL_ONLY = "local_only"                 # Only private AI providers allowed
    CLOUD_ALLOWED = "cloud_allowed"           # Cloud AI providers permitted
    REGULATORY_REVIEW_REQUIRED = "regulatory_review_required"  # Feature gated


# =========================================================================
# AI FEATURE REGISTRY
# =========================================================================

@dataclass(frozen=True, slots=True)
class AIFeature:
    """Registration of an AI feature/system for compliance tracking."""
    feature_id: str
    system_id: str
    provider: str
    model: str
    model_version: str | None = None
    intended_purpose: str = ""
    risk_classification: AIRiskClassification = AIRiskClassification.LIMITED
    data_policy: AIUsePolicy = AIUsePolicy.LOCAL_ONLY
    human_oversight_required: bool = True
    transparency_required: bool = True
    enabled: bool = True
    effective_from: datetime = field(default_factory=lambda: datetime.now(UTC))
    effective_to: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AIFeatureRegistry:
    """Registry of AI features with policy enforcement."""

    def __init__(self) -> None:
        self._features: dict[str, AIFeature] = {}

    def register(self, feature: AIFeature) -> None:
        """Register an AI feature."""
        if feature.feature_id in self._features:
            raise ValueError(f"Feature {feature.feature_id} already registered")
        self._features[feature.feature_id] = feature

    def get(self, feature_id: str) -> AIFeature | None:
        """Get feature by ID."""
        return self._features.get(feature_id)

    def list_enabled(self) -> list[AIFeature]:
        """List all enabled features."""
        now = datetime.now(UTC)
        return [
            f for f in self._features.values()
            if f.enabled and f.effective_from <= now and (f.effective_to is None or f.effective_to >= now)
        ]

    def check_policy(self, feature_id: str, requested_policy: AIUsePolicy) -> bool:
        """Check if a policy is allowed for a feature."""
        feature = self.get(feature_id)
        if not feature:
            return False

        if not feature.enabled:
            return False

        # REGULATORY_REVIEW_REQUIRED gates the feature entirely
        if feature.data_policy == AIUsePolicy.REGULATORY_REVIEW_REQUIRED:
            return False

        # LOCAL_ONLY means only LOCAL_ONLY policy is allowed
        if feature.data_policy == AIUsePolicy.LOCAL_ONLY:
            return requested_policy in (AIUsePolicy.LOCAL_ONLY, AIUsePolicy.REQUIRE_HUMAN_OVERSIGHT)

        # DENY blocks everything
        if feature.data_policy == AIUsePolicy.DENY:
            return False

        # ALLOW permits the requested policy
        return True


# =========================================================================
# AI TRACEABILITY
# =========================================================================

@dataclass(frozen=True, slots=True)
class AITraceRecord:
    """Record of an AI execution for traceability (no sensitive content)."""
    operation_id: str
    instance_id: str
    feature_id: str
    provider: str
    model: str
    model_version: str | None
    policy: AIUsePolicy
    local_or_cloud: str  # "local" or "cloud"
    timestamp: datetime
    success: bool
    latency_ms: int
    fallback_used: bool = False
    error_code: str | None = None
    # Correlation IDs (no sensitive data)
    correlation_id: str | None = None
    preview_id: str | None = None
    confirmation_id: str | None = None
    erp_write_id: str | None = None


class AITraceabilityLogger:
    """Logs AI executions with correlation to business operations."""

    def __init__(self, audit_logger: Any) -> None:
        self._audit = audit_logger

    async def log_execution(
        self,
        operation_id: str,
        instance_id: str,
        feature_id: str,
        provider: str,
        model: str,
        policy: AIUsePolicy,
        local_or_cloud: str,
        success: bool,
        latency_ms: int,
        model_version: str | None = None,
        fallback_used: bool = False,
        error_code: str | None = None,
        correlation_id: str | None = None,
        preview_id: str | None = None,
        confirmation_id: str | None = None,
        erp_write_id: str | None = None,
    ) -> str:
        """Log an AI execution trace."""
        trace_id = str(uuid4())
        record = AITraceRecord(
            operation_id=operation_id,
            instance_id=instance_id,
            feature_id=feature_id,
            provider=provider,
            model=model,
            model_version=model_version,
            policy=policy,
            local_or_cloud=local_or_cloud,
            timestamp=datetime.now(UTC),
            success=success,
            latency_ms=latency_ms,
            fallback_used=fallback_used,
            error_code=error_code,
            correlation_id=correlation_id,
            preview_id=preview_id,
            confirmation_id=confirmation_id,
            erp_write_id=erp_write_id,
        )

        # Log to audit system (implementation depends on audit_logger interface)
        try:
            await self._audit.log(
                instance_id=instance_id,
                actor_type="system",
                actor_id="ai_traceability",
                action="ai.execution",
                request_id=trace_id,
                correlation_id=correlation_id,
                resource_type="ai_execution",
                resource_id=trace_id,
                new_state=record.__dict__,
                success=success,
                error_code=error_code,
                duration_ms=latency_ms,
            )
        except Exception:
            # Never fail the main operation due to trace logging
            pass

        return trace_id


# =========================================================================
# DATA MINIMISATION
# =========================================================================

class DataMinimisationFilter:
    """Filters AI trace data to exclude sensitive content."""

    # Fields that MUST NOT be stored in AI traces
    FORBIDDEN_FIELDS = frozenset([
        "pdf_content",
        "image_content",
        "ocr_text",
        "full_prompt",
        "bank_account",
        "api_key",
        "telegram_token",
        "dolibarr_key",
        "password",
        "secret",
        "credit_card",
        "iban",
        "swift_bic",
    ])

    @classmethod
    def sanitize(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Remove forbidden fields from a data dict."""
        sanitized: dict[str, Any] = {}
        for key, value in data.items():
            if cls._is_forbidden(key):
                continue
            if isinstance(value, dict):
                sanitized[key] = cls.sanitize(value)
            elif isinstance(value, list):
                sanitized[key] = [
                    cls.sanitize(v) if isinstance(v, dict) else v for v in value
                ]
            else:
                sanitized[key] = value
        return sanitized

    @classmethod
    def _is_forbidden(cls, key: str) -> bool:
        key_lower = key.lower()
        return any(forbidden in key_lower for forbidden in cls.FORBIDDEN_FIELDS)


# =========================================================================
# RETENTION CONFIGURATION
# =========================================================================

@dataclass(frozen=True, slots=True)
class RetentionConfig:
    """Retention policy for a data category within an instance."""
    instance_id: str
    category: str  # "ai_trace", "document", "audit", etc.
    ttl_days: int
    description: str = ""


class RetentionPolicy:
    """Manages retention policies per instance and category."""

    def __init__(self) -> None:
        self._policies: dict[str, RetentionConfig] = {}

    def set_policy(self, config: RetentionConfig) -> None:
        """Set retention policy for instance/category."""
        key = f"{config.instance_id}:{config.category}"
        self._policies[key] = config

    def get_ttl_days(self, instance_id: str, category: str) -> int | None:
        """Get TTL in days for instance/category."""
        key = f"{instance_id}:{category}"
        policy = self._policies.get(key)
        return policy.ttl_days if policy else None


# =========================================================================
# HUMAN OVERSIGHT
# =========================================================================

@dataclass(frozen=True, slots=True)
class HumanOversightRecord:
    """Record of human confirmation of AI output."""
    operation_id: str
    instance_id: str
    feature_id: str
    confirmed_by: str  # telegram_user_id or system identifier
    confirmed_at: datetime
    preview_hash: str  # Hash of preview content that was confirmed
    preview_version: str  # Version identifier of the preview
    approval_action: str  # "confirmed", "corrected", "cancelled"


class HumanOversightRecorder:
    """Records human oversight decisions for AI outputs."""

    def __init__(self, audit_logger: Any) -> None:
        self._audit = audit_logger

    async def record_confirmation(
        self,
        operation_id: str,
        instance_id: str,
        feature_id: str,
        confirmed_by: str,
        preview_hash: str,
        preview_version: str,
        approval_action: str,
    ) -> str:
        """Record a human confirmation decision."""
        record = HumanOversightRecord(
            operation_id=operation_id,
            instance_id=instance_id,
            feature_id=feature_id,
            confirmed_by=confirmed_by,
            confirmed_at=datetime.now(UTC),
            preview_hash=preview_hash,
            preview_version=preview_version,
            approval_action=approval_action,
        )

        try:
            await self._audit.log(
                instance_id=instance_id,
                actor_type="telegram_user",
                actor_id=confirmed_by,
                action="ai.human_oversight",
                resource_type="ai_human_oversight",
                resource_id=operation_id,
                new_state=record.__dict__,
                success=True,
            )
        except Exception:
            pass

        return record.operation_id


# =========================================================================
# TRANSPARENCY NOTICE
# =========================================================================

@dataclass(frozen=True, slots=True)
class TransparencyNotice:
    """Configurable transparency notice for AI features."""
    feature_id: str
    channel: str  # "telegram", "web", "api", "onboarding"
    message: str
    enabled: bool = True


class TransparencyManager:
    """Manages transparency notices per feature and channel."""

    def __init__(self) -> None:
        self._notices: dict[str, TransparencyNotice] = {}

    def set_notice(self, notice: TransparencyNotice) -> None:
        """Set a transparency notice."""
        key = f"{notice.feature_id}:{notice.channel}"
        self._notices[key] = notice

    def get_notice(self, feature_id: str, channel: str) -> TransparencyNotice | None:
        """Get transparency notice for feature/channel."""
        key = f"{feature_id}:{channel}"
        return self._notices.get(key)

    def should_show(self, feature_id: str, channel: str) -> bool:
        """Check if notice should be shown."""
        notice = self.get_notice(feature_id, channel)
        return notice is not None and notice.enabled


# =========================================================================
# REGULATORY REVIEW GATE (HR/High-Risk Firewall)
# =========================================================================

class RegulatoryReviewGate:
    """Gates features requiring regulatory review (disabled by default)."""

    def __init__(self) -> None:
        self._approved_features: set[str] = set()

    def is_allowed(self, feature_id: str, feature: AIFeature) -> bool:
        """Check if a feature with REGULATORY_REVIEW_REQUIRED is allowed."""
        if feature.data_policy != AIUsePolicy.REGULATORY_REVIEW_REQUIRED:
            return True
        return feature_id in self._approved_features

    def approve_feature(self, feature_id: str) -> None:
        """Explicitly approve a feature for regulatory review (maintainer action)."""
        self._approved_features.add(feature_id)

    def revoke_approval(self, feature_id: str) -> None:
        """Revoke approval for a feature."""
        self._approved_features.discard(feature_id)


# =========================================================================
# RUNTIME VERSIONING
# =========================================================================

@dataclass(frozen=True, slots=True)
class RuntimeVersions:
    """Captured runtime versions at operation start for auditability."""
    gestor_ia_version: str
    workflow_version: str
    ai_config_version: str
    provider_version: str  # e.g., "ollama:qwen3.5:4b-invoice"
    policy_version: str
    git_sha: str | None = None


class RuntimeVersionCapture:
    """Captures runtime versions at operation boundaries."""

    @staticmethod
    def capture(
        gestor_ia_version: str,
        workflow_version: str,
        ai_config_version: str,
        provider_version: str,
        policy_version: str,
        git_sha: str | None = None,
    ) -> RuntimeVersions:
        """Capture current runtime versions."""
        return RuntimeVersions(
            gestor_ia_version=gestor_ia_version,
            workflow_version=workflow_version,
            ai_config_version=ai_config_version,
            provider_version=provider_version,
            policy_version=policy_version,
            git_sha=git_sha,
        )


# =========================================================================
# SINGLETON INSTANCES (initialized at startup)
# =========================================================================

# Global registry instances - initialized by application bootstrap
feature_registry: AIFeatureRegistry | None = None
traceability_logger: AITraceabilityLogger | None = None
minimisation_filter: DataMinimisationFilter = DataMinimisationFilter()
retention_policy: RetentionPolicy = RetentionPolicy()
oversight_recorder: HumanOversightRecorder | None = None
transparency_manager: TransparencyManager = TransparencyManager()
regulatory_gate: RegulatoryReviewGate = RegulatoryReviewGate()
version_capture: RuntimeVersionCapture = RuntimeVersionCapture()


def init_ai_compliance(audit_logger: Any) -> None:
    """Initialize all AI compliance components."""
    global feature_registry, traceability_logger, oversight_recorder

    feature_registry = AIFeatureRegistry()
    traceability_logger = AITraceabilityLogger(audit_logger)
    oversight_recorder = HumanOversightRecorder(audit_logger)

    # Register default features
    _register_default_features()


def _register_default_features() -> None:
    """Register built-in AI features."""
    if not feature_registry:
        return

    # Supplier Invoice Extraction feature
    feature_registry.register(AIFeature(
        feature_id="supplier_invoice_extraction",
        system_id="gestor-ia-invoice-processing",
        provider="ollama",
        model="qwen3.5:4b-invoice",
        intended_purpose="Extract structured accounting information from supplier invoices",
        risk_classification=AIRiskClassification.LIMITED,
        data_policy=AIUsePolicy.LOCAL_ONLY,
        human_oversight_required=True,
        transparency_required=True,
    ))

    # Future HR features (disabled by regulatory gate)
    feature_registry.register(AIFeature(
        feature_id="candidate_ranking",
        system_id="gestor-ia-hr",
        provider="ollama",
        model="",
        intended_purpose="Rank job candidates based on CV analysis",
        risk_classification=AIRiskClassification.HIGH,
        data_policy=AIUsePolicy.REGULATORY_REVIEW_REQUIRED,
        human_oversight_required=True,
        transparency_required=True,
        enabled=False,  # Disabled until regulatory review
    ))

    feature_registry.register(AIFeature(
        feature_id="employee_evaluation",
        system_id="gestor-ia-hr",
        provider="ollama",
        model="",
        intended_purpose="Evaluate employee performance for promotion decisions",
        risk_classification=AIRiskClassification.HIGH,
        data_policy=AIUsePolicy.REGULATORY_REVIEW_REQUIRED,
        human_oversight_required=True,
        transparency_required=True,
        enabled=False,
    ))