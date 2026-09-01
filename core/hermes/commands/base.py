"""
Command Layer V1 - Base Classes.

CommandHandler protocol and CommandRegistry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext

from .models import CommandPreview, CommandResult, CommandType


class CommandHandler(ABC):
    """Protocol for command handlers."""

    @property
    @abstractmethod
    def command_type(self) -> CommandType:
        pass

    @property
    @abstractmethod
    def required_permission(self) -> str:
        pass

    @abstractmethod
    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize payload. Raises ValueError if invalid."""
        pass

    @abstractmethod
    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate human-readable preview for confirmation."""
        pass

    @abstractmethod
    async def execute(
        self, company_context: CompanyContext, user_context: UserContext, validated_payload: dict[str, Any], document_hash: str | None = None
    ) -> CommandResult:
        """Execute command against Dolibarr."""
        pass

    @abstractmethod
    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        """Data to include in audit log."""
        pass


class CommandRegistry:
    """Registry of command handlers per instance."""

    def __init__(self) -> None:
        self._core_handlers: dict[CommandType, CommandHandler] = {}
        self._instance_handlers: dict[str, dict[CommandType, CommandHandler]] = {}

    def register_core_handler(self, handler: CommandHandler) -> None:
        """Register handler available to all instances."""
        self._core_handlers[handler.command_type] = handler

    def register_instance_handler(self, instance_id: str, handler: CommandHandler) -> None:
        """Register handler for specific instance."""
        if instance_id not in self._instance_handlers:
            self._instance_handlers[instance_id] = {}
        self._instance_handlers[instance_id][handler.command_type] = handler

    def get_handler(self, instance_id: str, command_type: CommandType) -> CommandHandler | None:
        """Get handler: instance-specific first, then core."""
        if instance_id in self._instance_handlers:
            handler = self._instance_handlers[instance_id].get(command_type)
            if handler:
                return handler
        return self._core_handlers.get(command_type)

    def list_available(self, instance_id: str) -> list[CommandHandler]:
        """List all handlers available for an instance."""
        handlers = list(self._core_handlers.values())
        handlers.extend(self._instance_handlers.get(instance_id, {}).values())
        return handlers

    def clear_instance(self, instance_id: str) -> None:
        """Clear instance-specific handlers."""
        self._instance_handlers.pop(instance_id, None)

    def clear_all(self) -> None:
        """Clear all handlers."""
        self._core_handlers.clear()
        self._instance_handlers.clear()


# Global registry instance
command_registry = CommandRegistry()
