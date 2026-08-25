"""
Command Layer V1 - Company Policy.

Extensible policy abstraction for company-specific rules.
Instance isolation: each instance gets its own policy instance.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.hermes.context import CompanyContext

from .models import CommandType, ValidatedCommand


class CompanyPolicy(ABC):
    """Protocol for company-specific command validation and enrichment.

    Instance isolation: policy MUST only use company_context for rules.
    Never access global state or other instances' config.
    """

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id

    @abstractmethod
    def validate_command(
        self, command_type: CommandType, payload: dict[str, Any], company_context: CompanyContext
    ) -> ValidatedCommand:
        """Validate and enrich command payload with company rules."""
        pass

    @abstractmethod
    def enrich_command(
        self, command_type: CommandType, payload: dict[str, Any], company_context: CompanyContext
    ) -> dict[str, Any]:
        """Add company defaults (VAT, currency, series, warehouse, etc.)."""
        pass

    def _verify_instance(self, company_context: CompanyContext) -> None:
        """Verify company_context belongs to this policy's instance."""
        if company_context.instance_id != self.instance_id:
            raise ValueError(
                f"Cross-instance policy access denied: "
                f"policy for '{self.instance_id}' cannot process '{company_context.instance_id}'"
            )


class DefaultCompanyPolicy(CompanyPolicy):
    """Default no-op policy - passes commands through unchanged.

    Safe by default: no cross-instance leakage, no implicit defaults.
    """

    def validate_command(
        self, command_type: CommandType, payload: dict[str, Any], company_context: CompanyContext
    ) -> ValidatedCommand:
        self._verify_instance(company_context)
        return ValidatedCommand(command_type=command_type, payload=payload)

    def enrich_command(
        self, command_type: CommandType, payload: dict[str, Any], company_context: CompanyContext
    ) -> dict[str, Any]:
        self._verify_instance(company_context)
        return payload


def get_company_policy(instance_id: str) -> CompanyPolicy:
    """Factory for instance-specific policy.

    Each instance gets its own policy instance for isolation.
    Future: load custom policy from instance config.
    """
    return DefaultCompanyPolicy(instance_id)
