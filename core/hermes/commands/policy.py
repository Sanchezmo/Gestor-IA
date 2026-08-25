"""
Command Layer V1 - Company Policy.

Extensible policy abstraction for company-specific rules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.hermes.context import CompanyContext

from .models import CommandType, ValidatedCommand


class CompanyPolicy(ABC):
    """Protocol for company-specific command validation and enrichment."""

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


class DefaultCompanyPolicy(CompanyPolicy):
    """Default no-op policy - passes commands through unchanged."""

    def validate_command(
        self, command_type: CommandType, payload: dict[str, Any], company_context: CompanyContext
    ) -> ValidatedCommand:
        return ValidatedCommand(command_type=command_type, payload=payload)

    def enrich_command(
        self, command_type: CommandType, payload: dict[str, Any], company_context: CompanyContext
    ) -> dict[str, Any]:
        return payload


def get_company_policy(instance_id: str) -> CompanyPolicy:
    """Factory for instance-specific policy. Returns DefaultCompanyPolicy for now."""
    # Future: load from instance config
    return DefaultCompanyPolicy()
