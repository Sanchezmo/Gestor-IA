"""
Utilidades compartidas para tools de facturas (cliente y proveedor).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

# =========================================================================
# ALLOWLISTS FOR DOLIBARR PARAMETERS
# =========================================================================

# Dolibarr invoice sortable fields
ALLOWED_INVOICE_SORT_FIELDS: frozenset[str] = frozenset(
    {
        "rowid",
        "ref",
        "date",
        "date_lim_reglement",
        "total_ttc",
        "soc_name",
        "status",
    }
)

ALLOWED_INVOICE_SORT_ORDERS: frozenset[str] = frozenset({"ASC", "DESC"})


# =========================================================================
# SHARED HELPERS
# =========================================================================


def validate_pagination(limit: int, offset: int) -> None:
    """Validar parámetros de paginación."""
    if limit < 1 or limit > 100:
        raise ValueError("El parámetro 'limit' debe estar entre 1 y 100")
    if offset < 0:
        raise ValueError("El parámetro 'offset' debe ser >= 0")


def validate_sort(sort_field: str, sort_order: str) -> None:
    """Validar parámetros de ordenación."""
    if sort_order not in ALLOWED_INVOICE_SORT_ORDERS:
        raise ValueError(f"sort_order debe ser ASC o DESC, recibido: {sort_order}")
    if sort_field not in ALLOWED_INVOICE_SORT_FIELDS:
        raise ValueError(f"sort_field no permitido: {sort_field}")


def map_invoice_status_to_dolibarr(status: str | None) -> int | None:
    """Mapear InvoiceStatus a código numérico Dolibarr.

    Falla si el estado no es válido (FAIL CLOSED).
    """
    if status is None:
        return None
    status_map = {"draft": 0, "validated": 1, "paid": 2, "cancelled": 3}
    if status not in status_map:
        raise ValueError(f"Estado de factura inválido: '{status}'. Valores permitidos: {list(status_map.keys())}")
    return status_map[status]


def date_to_timestamp(dt: date | None, end_of_day: bool = False) -> int | None:
    """Convertir date a timestamp Unix para Dolibarr."""
    if dt is None:
        return None
    time = datetime.max.time() if end_of_day else datetime.min.time()
    return int(datetime.combine(dt, time).timestamp())


def escape_sql_like(value: str) -> str:
    """Escapar valor para uso en sqlfilters LIKE de Dolibarr."""
    return value.strip().replace("'", "''").replace("%", "\\%").replace("_", "\\_")


def build_sqlfilters(parts: list[str]) -> str:
    """Unir partes de sqlfilters con AND."""
    return " AND ".join(parts)


def to_decimal(value: Any) -> Decimal:
    """Convertir valor a Decimal de forma segura."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return Decimal("0")
    return Decimal("0")


def timestamp_to_date(value: Any) -> date | None:
    """Convertir timestamp a date."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value).date()
    if isinstance(value, str):
        try:
            return datetime.fromtimestamp(int(value)).date()
        except Exception:
            return None
    return None
