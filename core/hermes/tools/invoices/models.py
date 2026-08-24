"""
Modelos de datos para facturas (cliente y proveedor).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_cls
from decimal import Decimal

# =========================================================================
# CUSTOMER INVOICE MODELS
# =========================================================================


@dataclass(frozen=True, slots=True)
class CustomerInvoiceSummary:
    """Resumen de factura de cliente para respuesta Telegram/UI."""

    id: int
    ref: str
    thirdparty_id: int
    thirdparty_name: str
    date: date_cls | None
    due_date: date_cls | None
    status: str  # draft, validated, paid, cancelled
    total_ht: Decimal
    total_ttc: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal


# =========================================================================
# SUPPLIER INVOICE MODELS
# =========================================================================


@dataclass(frozen=True, slots=True)
class SupplierInvoiceSummary:
    """Resumen de factura de proveedor para respuesta Telegram/UI."""

    id: int
    ref: str
    thirdparty_id: int
    thirdparty_name: str
    date: date_cls | None
    due_date: date_cls | None
    status: str  # draft, validated, paid, cancelled
    total_ht: Decimal
    total_ttc: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
