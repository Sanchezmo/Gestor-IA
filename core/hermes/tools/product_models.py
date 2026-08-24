"""
Modelos de dominio para Productos/Servicios - Query Layer V4 Read-Only.

Modelos inmutables (frozen=True, slots=True) para respuestas de tools.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


# =========================================================================
# PRODUCT SUMMARY (para list/search responses)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ProductSummary:
    """Resumen de producto/servicio para respuestas de lista/búsqueda."""

    id: int
    ref: str
    label: str
    type: str  # "PRODUCT" | "SERVICE"
    status: int
    price: Decimal
    price_ttc: Decimal
    vat_rate: Decimal
    currency: str
    stock_reel: Decimal | None = None
    desiredstock: Decimal | None = None
    seuil_stock_alerte: Decimal | None = None
    default_warehouse: str | None = None
    barcode: str | None = None


# =========================================================================
# PRODUCT DETAIL (para get_product response)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ProductDetail:
    """Detalle completo de producto/servicio."""

    id: int
    ref: str
    label: str
    type: str  # "PRODUCT" | "SERVICE"
    status: int
    price: Decimal
    price_ttc: Decimal
    vat_rate: Decimal
    currency: str
    stock_reel: Decimal | None = None
    desiredstock: Decimal | None = None
    seuil_stock_alerte: Decimal | None = None
    default_warehouse: str | None = None
    barcode: str | None = None
    description: str | None = None
    price_min: Decimal | None = None
    price_base_type: str | None = None  # "HT" | "TTC"
    weight: Decimal | None = None
    weight_units: str | None = None
    length: Decimal | None = None
    surface: Decimal | None = None
    volume: Decimal | None = None
    units: str | None = None
    supplier_info: dict[str, Any] | None = None
    extrafields: dict[str, Any] | None = None


# =========================================================================
# TYPE CONSTANTS
# =========================================================================

DOLIBARR_TYPE_TO_LABEL = {0: "PRODUCT", 1: "SERVICE", "0": "PRODUCT", "1": "SERVICE"}
DOLIBARR_TYPE_FROM_LABEL = {"PRODUCT": 0, "SERVICE": 1}


def get_product_type_label(dolibarr_type: int | str) -> str:
    """Convert Dolibarr type (0/1) to label (PRODUCT/SERVICE)."""
    return DOLIBARR_TYPE_TO_LABEL.get(dolibarr_type, "PRODUCT")


def get_product_type_value(label: str) -> int:
    """Convert label (PRODUCT/SERVICE) to Dolibarr type (0/1)."""
    return DOLIBARR_TYPE_FROM_LABEL.get(label.upper(), 0)