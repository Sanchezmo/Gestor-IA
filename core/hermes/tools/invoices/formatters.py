"""
Formatters para presentación de facturas en Telegram.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from core.hermes.query.models import InvoicePartyType

# =========================================================================
# HELPERS DE FORMATO
# =========================================================================


def _format_money(amount: Decimal | float | int | str) -> str:
    """Formatear importe monetario para Telegram (formato ES)."""
    d = Decimal(str(amount))
    # Formato: 1.234,56 €
    return f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def _format_date(d: date | str | None) -> str:
    """Formatear fecha para Telegram (DD/MM/YYYY)."""
    if d is None:
        return "—"
    if isinstance(d, str):
        try:
            parsed = date.fromisoformat(d)
            return parsed.strftime("%d/%m/%Y")
        except Exception:
            return d
    return d.strftime("%d/%m/%Y")


# =========================================================================
# CUSTOMER INVOICE FORMATTERS
# =========================================================================


def format_customer_invoices_for_telegram(invoices: list[dict[str, Any]], limit: int, offset: int) -> str:
    """Formatear lista de facturas de cliente para respuesta Telegram."""
    if not invoices:
        return "No se han encontrado facturas de cliente."

    lines = ["Facturas de cliente encontradas:"]
    for i, inv in enumerate(invoices, 1):
        status_emoji = {
            "draft": "📝",
            "validated": "✅",
            "paid": "💰",
            "cancelled": "❌",
        }.get(inv.get("status", ""), "❓")
        due_str = f" (vence: {_format_date(inv.get('due_date'))})" if inv.get("due_date") else ""
        lines.append(
            f"{i}. {inv.get('ref', '—')} {status_emoji}\n"
            f"   Cliente: {inv.get('thirdparty_name', '—')}\n"
            f"   Fecha: {_format_date(inv.get('date'))}{due_str}\n"
            f"   Total: {_format_money(inv.get('total_ttc', 0))} "
            f"(pendiente: {_format_money(inv.get('remaining_amount', 0))})"
        )

    if len(invoices) >= limit:
        lines.append(f"\nMostrando los primeros {limit} resultados (offset {offset}).")

    return "\n".join(lines)


def format_customer_invoice_detail_for_telegram(invoice: dict[str, Any]) -> str:
    """Formatear detalle de factura de cliente para respuesta Telegram."""
    status_emoji = {
        "draft": "📝 Borrador",
        "validated": "✅ Validada",
        "paid": "💰 Pagada",
        "cancelled": "❌ Anulada",
    }.get(invoice.get("status", ""), "❓ Desconocido")

    lines = [f"📄 *{invoice.get('ref', '—')}*"]
    lines.append(f"Estado: {status_emoji}")
    lines.append(f"Cliente: {invoice.get('thirdparty_name', '—')} (ID: {invoice.get('thirdparty_id', '—')})")
    lines.append(f"Fecha: {_format_date(invoice.get('date'))}")
    if invoice.get("due_date"):
        lines.append(f"Vencimiento: {_format_date(invoice.get('due_date'))}")
    lines.append(f"Subtotal: {_format_money(invoice.get('total_ht', 0))}")
    lines.append(f"Total: {_format_money(invoice.get('total_ttc', 0))}")
    lines.append(f"Pagado: {_format_money(invoice.get('paid_amount', 0))}")
    lines.append(f"Pendiente: {_format_money(invoice.get('remaining_amount', 0))}")

    return "\n".join(lines)


# =========================================================================
# SUPPLIER INVOICE FORMATTERS
# =========================================================================


def format_supplier_invoices_for_telegram(invoices: list[dict[str, Any]], limit: int, offset: int) -> str:
    """Formatear lista de facturas de proveedor para respuesta Telegram."""
    if not invoices:
        return "No se han encontrado facturas de proveedor."

    lines = ["Facturas de proveedor encontradas:"]
    for i, inv in enumerate(invoices, 1):
        status_emoji = {
            "draft": "📝",
            "validated": "✅",
            "paid": "💰",
            "cancelled": "❌",
        }.get(inv.get("status", ""), "❓")
        due_str = f" (vence: {_format_date(inv.get('due_date'))})" if inv.get("due_date") else ""
        lines.append(
            f"{i}. {inv.get('ref', '—')} {status_emoji}\n"
            f"   Proveedor: {inv.get('thirdparty_name', '—')}\n"
            f"   Fecha: {_format_date(inv.get('date'))}{due_str}\n"
            f"   Total: {_format_money(inv.get('total_ttc', 0))} "
            f"(pendiente: {_format_money(inv.get('remaining_amount', 0))})"
        )

    if len(invoices) >= limit:
        lines.append(f"\nMostrando los primeros {limit} resultados (offset {offset}).")

    return "\n".join(lines)


def format_supplier_invoice_detail_for_telegram(invoice: dict[str, Any]) -> str:
    """Formatear detalle de factura de proveedor para respuesta Telegram."""
    status_emoji = {
        "draft": "📝 Borrador",
        "validated": "✅ Validada",
        "paid": "💰 Pagada",
        "cancelled": "❌ Anulada",
    }.get(invoice.get("status", ""), "❓ Desconocido")

    lines = [f"📄 *{invoice.get('ref', '—')}*"]
    lines.append(f"Estado: {status_emoji}")
    lines.append(f"Proveedor: {invoice.get('thirdparty_name', '—')} (ID: {invoice.get('thirdparty_id', '—')})")
    lines.append(f"Fecha: {_format_date(invoice.get('date'))}")
    if invoice.get("due_date"):
        lines.append(f"Vencimiento: {_format_date(invoice.get('due_date'))}")
    lines.append(f"Subtotal: {_format_money(invoice.get('total_ht', 0))}")
    lines.append(f"Total: {_format_money(invoice.get('total_ttc', 0))}")
    lines.append(f"Pagado: {_format_money(invoice.get('paid_amount', 0))}")
    lines.append(f"Pendiente: {_format_money(invoice.get('remaining_amount', 0))}")

    return "\n".join(lines)


# =========================================================================
# COMMON FORMATTERS
# =========================================================================


def format_invoice_count_for_telegram(count: int, party_type: InvoicePartyType) -> str:
    """Formatear respuesta de conteo para Telegram."""
    if party_type == InvoicePartyType.CUSTOMER:
        return f"Hay {count} facturas de cliente registradas."
    elif party_type == InvoicePartyType.SUPPLIER:
        return f"Hay {count} facturas de proveedor registradas."
    else:
        return f"Hay {count} facturas registradas."
