"""
Formatters para presentación de productos/servicios en Telegram.
"""

from decimal import Decimal
from typing import Any

from core.hermes.query.models import ProductTypeFilter

# =========================================================================
# HELPERS DE FORMATO
# =========================================================================


CURRENCY_FORMATS = {
    "EUR": {"symbol": "€", "position": "after", "decimal": ",", "thousands": "."},
    "USD": {"symbol": "$", "position": "before", "decimal": ".", "thousands": ","},
    "GBP": {"symbol": "£", "position": "before", "decimal": ".", "thousands": ","},
}


def _format_money(amount: Decimal | float | int | str, currency: str = "EUR") -> str:
    """Formatear importe monetario para Telegram según moneda."""
    d = Decimal(str(amount))
    fmt = CURRENCY_FORMATS.get(currency, CURRENCY_FORMATS["EUR"])
    # Format with thousands separator
    formatted = f"{d:,.2f}"
    # Swap separators based on currency
    if fmt["decimal"] == ",":
        formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    if fmt["position"] == "after":
        return f"{formatted} {fmt['symbol']}"
    else:
        return f"{fmt['symbol']}{formatted}"


# =========================================================================
# PRODUCT FORMATTERS
# =========================================================================


def format_products_for_telegram(
    products: list[dict[str, Any]],
    limit: int,
    page: int,
    currency: str = "EUR",
) -> str:
    """Formatear lista de productos/servicios para respuesta Telegram."""
    if not products:
        return "No se han encontrado productos/servicios."

    lines = ["Productos/servicios encontrados:"]
    for i, p in enumerate(products, 1):
        type_label = "Producto" if p.get("type") == "PRODUCT" else "Servicio"
        price_str = _format_money(p.get("price_ttc", Decimal("0")), currency)
        vat_str = f" (IVA {p.get('vat_rate', 0)}%)" if p.get("vat_rate") else ""
        stock_str = ""
        if p.get("type") == "PRODUCT" and p.get("stock_reel") is not None:
            stock_str = f" | Stock: {p['stock_reel']} uds"

        lines.append(
            f"{i}. {p.get('ref', '—')} — {p.get('label', 'Sin nombre')}\n"
            f"   Tipo: {type_label} | Precio: {price_str}{vat_str}{stock_str}"
        )

    if len(products) >= limit:
        lines.append(f"\nMostrando {limit} resultados (página {page}).")

    return "\n".join(lines)


def format_product_detail_for_telegram(product: dict[str, Any], currency: str = "EUR") -> str:
    """Formatear detalle de producto/servicio para respuesta Telegram."""
    type_emoji = "📦" if product.get("type") == "PRODUCT" else "🔧"
    type_label = "Producto" if product.get("type") == "PRODUCT" else "Servicio"

    status_map = {0: "Borrador", 1: "Activo", 2: "Descontinuado"}
    status = status_map.get(product.get("status", 0), f"Desconocido ({product.get('status')})")

    lines = [
        f"{type_emoji} *{product.get('ref', '—')} — {product.get('label', 'Sin nombre')}*",
        f"Tipo: {type_label}",
        f"Estado: {status}",
    ]

    if product.get("description"):
        lines.append(f"Descripción: {product['description']}")

    price_ht = _format_money(product.get("price", Decimal("0")), currency)
    price_ttc = _format_money(product.get("price_ttc", Decimal("0")), currency)
    lines.append(f"Precio base: {price_ht}")
    lines.append(f"Precio con IVA: {price_ttc}")

    if product.get("vat_rate"):
        lines.append(f"IVA: {product['vat_rate']}%")

    if product.get("price_min"):
        lines.append(f"Precio mínimo: {_format_money(product['price_min'], currency)}")

    # Stock (only for products)
    if product.get("type") == "PRODUCT":
        if product.get("stock_reel") is not None:
            lines.append(f"Stock real: {product['stock_reel']} uds")
        if product.get("desiredstock") is not None:
            lines.append(f"Stock deseado: {product['desiredstock']} uds")
        if product.get("seuil_stock_alerte") is not None:
            lines.append(f"Alerta stock: {product['seuil_stock_alerte']} uds")
        if product.get("default_warehouse"):
            lines.append(f"Almacén por defecto: {product['default_warehouse']}")

    if product.get("barcode"):
        lines.append(f"Código de barras: {product['barcode']}")

    if product.get("supplier_info"):
        lines.append(f"Proveedor: {product['supplier_info'].get('soc_name', '—')}")

    return "\n".join(lines)


def format_product_count_for_telegram(count: int, product_type: ProductTypeFilter) -> str:
    """Formatear respuesta de conteo para Telegram."""
    if product_type == ProductTypeFilter.PRODUCT:
        return f"Hay {count} productos registrados."
    elif product_type == ProductTypeFilter.SERVICE:
        return f"Hay {count} servicios registrados."
    else:
        return f"Hay {count} productos/servicios registrados."
