"""
Command Layer - Handlers Package.

V1 ACTIVO (registrado en command_registry):
- CreateThirdpartyHandler
- CreateProductHandler
- CreateServiceHandler

EXPERIMENTAL (código conservado, NO registrado):
- CreateProposalHandler
- CreateInvoiceHandler
- CreateInvoiceFromProposalHandler
- CreateSupplierInvoiceHandler
- CreateOrderHandler
- CreateSupplierOrderHandler
- CreatePaymentHandler
- CreateCollectionHandler
- CreateStockMovementHandler
- CreateProjectHandler
- AddProjectTaskHandler
- ImportBC3Handler
- ExportBC3Handler
"""

from __future__ import annotations

# V1 ACTIVO
from .product import CreateProductHandler, CreateServiceHandler
from .thirdparty import CreateThirdpartyHandler

# EXPERIMENTAL - Importar para disponibilidad, NO registrar automáticamente
from .proposal import CreateProposalHandler  # noqa: F401
from .invoice import CreateInvoiceHandler, CreateInvoiceFromProposalHandler  # noqa: F401
from .supplier_invoice import CreateSupplierInvoiceHandler  # noqa: F401
from .order import CreateOrderHandler, CreateSupplierOrderHandler  # noqa: F401
from .payment import CreatePaymentHandler, CreateCollectionHandler  # noqa: F401
from .stock_movement import CreateStockMovementHandler  # noqa: F401
from .project import CreateProjectHandler, AddProjectTaskHandler  # noqa: F401
from .bc3 import ImportBC3Handler, ExportBC3Handler  # noqa: F401

__all__ = [
    # V1 ACTIVO
    "CreateThirdpartyHandler",
    "CreateProductHandler",
    "CreateServiceHandler",
    # EXPERIMENTAL (código conservado)
    "CreateProposalHandler",
    "CreateInvoiceHandler",
    "CreateInvoiceFromProposalHandler",
    "CreateSupplierInvoiceHandler",
    "CreateOrderHandler",
    "CreateSupplierOrderHandler",
    "CreatePaymentHandler",
    "CreateCollectionHandler",
    "CreateStockMovementHandler",
    "CreateProjectHandler",
    "AddProjectTaskHandler",
    "ImportBC3Handler",
    "ExportBC3Handler",
]
