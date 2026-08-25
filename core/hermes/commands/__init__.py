"""
Hermes Commands - Capa de comandos genérica para operaciones de escritura.

Principio: Los Commands NO saben de Telegram, WebSocket, HTTP, etc.
Reciben CompanyContext, UserContext y parámetros validados.
Devuelven CommandResult.

Separación clara: Query Layer (READ) ≠ Command Layer (WRITE)
"""

from __future__ import annotations

from core.hermes.commands.base import CommandHandler, CommandRegistry, command_registry
from core.hermes.commands.models import (
    CommandIntent,
    CommandPreview,
    CommandResult,
    CommandStatus,
    CommandType,
    PendingCommand,
    ValidatedCommand,
)

# Import handlers to register them
from .handlers import product, thirdparty, invoice, supplier_invoice, order, payment, stock_movement, project, bc3  # noqa: F401

__all__ = [
    # Registry
    "CommandRegistry",
    "CommandHandler",
    "command_registry",
    "register_core_commands",
    # Models
    "CommandType",
    "CommandStatus",
    "CommandIntent",
    "CommandPreview",
    "PendingCommand",
    "ValidatedCommand",
    "CommandResult",
]


def register_core_commands() -> None:
    """Registrar handlers de comandos core en el registry global."""
    from .handlers.product import CreateProductHandler, CreateServiceHandler
    from .handlers.proposal import CreateProposalHandler
    from .handlers.thirdparty import CreateThirdpartyHandler
    from .handlers.invoice import CreateInvoiceHandler, CreateInvoiceFromProposalHandler
    from .handlers.supplier_invoice import CreateSupplierInvoiceHandler
    from .handlers.order import CreateOrderHandler, CreateSupplierOrderHandler
    from .handlers.payment import CreatePaymentHandler, CreateCollectionHandler
    from .handlers.stock_movement import CreateStockMovementHandler
    from .handlers.project import CreateProjectHandler, AddProjectTaskHandler
    from .handlers.bc3 import ImportBC3Handler, ExportBC3Handler

    command_registry.register_core_handler(CreateThirdpartyHandler())
    command_registry.register_core_handler(CreateProductHandler())
    command_registry.register_core_handler(CreateServiceHandler())
    command_registry.register_core_handler(CreateProposalHandler())
    command_registry.register_core_handler(CreateInvoiceHandler())
    command_registry.register_core_handler(CreateInvoiceFromProposalHandler())
    command_registry.register_core_handler(CreateSupplierInvoiceHandler())
    command_registry.register_core_handler(CreateOrderHandler())
    command_registry.register_core_handler(CreateSupplierOrderHandler())
    command_registry.register_core_handler(CreatePaymentHandler())
    command_registry.register_core_handler(CreateCollectionHandler())
    command_registry.register_core_handler(CreateStockMovementHandler())
    command_registry.register_core_handler(CreateProjectHandler())
    command_registry.register_core_handler(AddProjectTaskHandler())
    command_registry.register_core_handler(ImportBC3Handler())
    command_registry.register_core_handler(ExportBC3Handler())
