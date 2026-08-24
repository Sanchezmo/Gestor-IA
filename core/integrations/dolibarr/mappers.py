"""
Mappers para convertir entre esquemas Pydantic y formato Dolibarr.

ADAPTADO desde Transvega Animal - adapters/dolibarr/mappers.py
Genérico para terceros, productos, facturas.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

# Mapeo de códigos de país ISO -> Dolibarr country_id
COUNTRY_CODE_TO_ID = {
    "ES": 4,  # Spain
    "US": 223,  # United States
    "FR": 69,  # France
    "DE": 58,  # Germany
    "IT": 100,  # Italy
    "PT": 168,  # Portugal
    "MX": 137,  # Mexico
    "CO": 43,  # Colombia
    "AR": 10,  # Argentina
    "CL": 39,  # Chile
    "PE": 164,  # Peru
    "EC": 63,  # Ecuador
    "PA": 159,  # Panama
    "DO": 56,  # Dominican Republic
    "VE": 229,  # Venezuela
}


def map_country_code_to_id(country_code: str | None) -> int | None:
    """Convertir código ISO país a country_id de Dolibarr."""
    if not country_code:
        return None
    return COUNTRY_CODE_TO_ID.get(country_code.upper())


def thirdparty_to_dolibarr(data: dict[str, Any], is_client: bool = True, is_supplier: bool = False) -> dict[str, Any]:
    """
    Convertir dict de tercero a formato Dolibarr API.

    Args:
        data: Dict con campos normalizados (snake_case)
        is_client: Si es cliente (client=1)
        is_supplier: Si es proveedor (fournisseur=1)

    Returns:
        Dict con claves Dolibarr (camelCase, tva_intra, etc.)
    """
    # Campos que se mapean directamente
    direct_fields = {
        "name",
        "name_alias",
        "email",
        "phone",
        "phone_mobile",
        "fax",
        "address",
        "zip",
        "town",
        "state_id",
        "country_id",
        "default_lang",
        "fk_parent",
        "iban",
        "bic",
        "skype",
        "twitter",
        "facebook",
        "linkedin",
        "shipping_method_id",
        "payment_term_id",
        "bar_code",
        "note_private",
        "note_public",
    }

    result = {}

    # Mapear campos directos
    for key in direct_fields:
        if key in data and data[key] is not None:
            result[key] = data[key]

    # Mapear campos con nombres diferentes
    field_mapping = {
        "vat_number": "tva_intra",  # Dolibarr usa tva_intra
        "country_code": "country_id",  # Se resuelve abajo
        "client": "client",
        "supplier": "fournisseur",  # Dolibarr usa fournisseur
    }

    for our_key, dolibarr_key in field_mapping.items():
        if our_key in data and data[our_key] is not None:
            if our_key == "country_code":
                country_id = map_country_code_to_id(data[our_key])
                if country_id:
                    result["country_id"] = country_id
            elif our_key == "client":
                result["client"] = 1 if data[our_key] else 0
            elif our_key == "supplier":
                result["fournisseur"] = 1 if data[our_key] else 0
            else:
                result[dolibarr_key] = data[our_key]

    # Forzar client/supplier según parámetros
    if is_client:
        result["client"] = 1
    if is_supplier:
        result["fournisseur"] = 1
        result["client"] = 0

    # Generar code_client si es cliente y no viene
    if is_client and "code_client" not in result:
        result["code_client"] = f"CLI-{int(datetime.now().timestamp())}"

    return result


def thirdparty_update_to_dolibarr(data: dict[str, Any]) -> dict[str, Any]:
    """Convertir actualización de tercero (solo campos no-None)."""
    field_mapping = {
        "vat_number": "tva_intra",
        "country_code": "country_id",
        "client": "client",
        "supplier": "fournisseur",
    }

    result = {}
    for key, value in data.items():
        if value is None:
            continue
        dolibarr_key = field_mapping.get(key, key)
        if key == "country_code":
            country_id = map_country_code_to_id(value)
            if country_id:
                result["country_id"] = country_id
        elif key in ("client", "supplier"):
            target_key = "client" if key == "client" else "fournisseur"
            result[target_key] = 1 if value else 0
        else:
            result[dolibarr_key] = value

    return result


def dolibarr_to_thirdparty(data: dict[str, Any]) -> dict[str, Any]:
    """
    Convertir respuesta Dolibarr a formato normalizado.

    Mapeo inverso:
    - fk_country -> country_id
    - fk_state -> state_id
    - rowid -> id
    - date_creation -> datec (datetime)
    - date_modification -> datem (datetime)
    - tva_intra -> vat_number
    """
    field_mapping = {
        "fk_country": "country_id",
        "fk_state": "state_id",
        "rowid": "id",
        "date_creation": "datec",
        "date_modification": "datem",
        "fk_user_creat": "fk_user_author",
        "fk_user_modif": "fk_user_modif",
        "tva_intra": "vat_number",
        "fournisseur": "supplier",
    }

    result = {}
    for key, value in data.items():
        our_key = field_mapping.get(key, key)

        # Convertir timestamps a datetime
        if our_key in ("datec", "datem") and isinstance(value, (int, float)):
            result[our_key] = datetime.fromtimestamp(value)
        # Handle empty string -> None
        elif our_key == "country_code" and value == "":
            result[our_key] = None
        # Handle null strings for integer fields
        elif our_key in ("fk_user_author", "fk_user_modif") and value in (None, "", "null"):
            result[our_key] = 1
        elif key == "status":
            result["status"] = int(value) if value is not None else 1
        else:
            result[our_key] = value

    # Defaults para campos requeridos
    if "id" not in result and "rowid" in data:
        result["id"] = data["rowid"]
    if "datec" not in result:
        result["datec"] = datetime.now()
    if "datem" not in result:
        result["datem"] = datetime.now()
    if "fk_user_author" not in result:
        result["fk_user_author"] = 1
    if "fk_user_modif" not in result:
        result["fk_user_modif"] = 1
    if "country_id" not in result:
        result["country_id"] = None
    if "state_id" not in result:
        result["state_id"] = None

    return result


def dolibarr_list_to_thirdparties(data: list) -> list:
    """Convertir lista de terceros Dolibarr a lista normalizada."""
    return [dolibarr_to_thirdparty(item) for item in data]


# =========================================================================
# MAPPERS PARA PRODUCTOS
# =========================================================================


def product_to_dolibarr(data: dict[str, Any]) -> dict[str, Any]:
    """Convertir producto a formato Dolibarr."""
    direct_fields = {
        "ref",
        "label",
        "description",
        "note",
        "type",
        "status",
        "price",
        "price_ttc",
        "price_base_type",
        "tva_tx",
        "weight",
        "weight_units",
        "length",
        "surface",
        "volume",
        "barcode",
        "fk_product_type",
        "fk_default_vat_code",
    }

    result = {}
    for key in direct_fields:
        if key in data and data[key] is not None:
            result[key] = data[key]

    return result


def dolibarr_to_product(data: dict[str, Any]) -> dict[str, Any]:
    """Convertir producto Dolibarr a formato normalizado."""
    field_mapping = {
        "rowid": "id",
        "date_creation": "datec",
        "date_modification": "datem",
        "fk_user_creat": "fk_user_author",
        "fk_user_modif": "fk_user_modif",
    }

    result = {}
    for key, value in data.items():
        our_key = field_mapping.get(key, key)
        if our_key in ("datec", "datem") and isinstance(value, (int, float)):
            result[our_key] = datetime.fromtimestamp(value)
        else:
            result[our_key] = value

    return result


# =========================================================================
# MAPPERS PARA PRODUCTOS V4 (Query Layer Read-Only)
# =========================================================================


DOLIBARR_TYPE_TO_LABEL = {0: "PRODUCT", 1: "SERVICE", "0": "PRODUCT", "1": "SERVICE"}


def _extract_supplier_info(data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract supplier information if present in Dolibarr response."""
    supplier_fields = ["fk_soc", "soc_name", "supplier_ref", "supplier_price"]
    supplier_data = {k: data.get(k) for k in supplier_fields if k in data and data.get(k) is not None}
    return supplier_data if supplier_data else None


def _has_supplier_info(data: dict[str, Any]) -> bool:
    return any(k in data and data.get(k) is not None for k in ["fk_soc", "soc_name", "supplier_ref"])


def _extract_extrafields(data: dict[str, Any]) -> dict[str, Any] | None:
    """Extract extrafields (keys starting with 'extrafield_' or similar)."""
    extrafields = {k: v for k, v in data.items() if k.startswith("extrafield_") or k.startswith("options_")}
    return extrafields if extrafields else None


def _has_extrafields(data: dict[str, Any]) -> bool:
    return any(k.startswith("extrafield_") or k.startswith("options_") for k in data.keys())


def dolibarr_to_product_summary(data: dict[str, Any], currency: str = "EUR") -> dict[str, Any]:
    """
    Convert Dolibarr product to ProductSummary dict.

    Args:
        data: Dolibarr product response
        currency: ISO 4217 currency code from CompanyContext

    Returns:
        Dict with ProductSummary fields
    """
    # Type mapping
    product_type = DOLIBARR_TYPE_TO_LABEL.get(data.get("type", 0), "PRODUCT")

    # Money fields - use Decimal
    price = _to_decimal(data.get("price"))
    price_ttc = _to_decimal(data.get("price_ttc"))
    price_min = _to_decimal(data.get("price_min")) if data.get("price_min") is not None else None
    vat_rate = _to_decimal(data.get("tva_tx"))

    # Stock fields (may not be present for services)
    stock_reel = _to_decimal(data.get("stock_reel")) if data.get("stock_reel") is not None else None
    desiredstock = _to_decimal(data.get("desiredstock")) if data.get("desiredstock") is not None else None
    seuil_stock_alerte = _to_decimal(data.get("seuil_stock_alerte")) if data.get("seuil_stock_alerte") is not None else None

    return {
        "id": data.get("id") or data.get("rowid"),
        "ref": data.get("ref"),
        "label": data.get("label"),
        "type": product_type,
        "status": int(data.get("status", 0)),
        "price": price,
        "price_ttc": price_ttc,
        "vat_rate": vat_rate,
        "currency": currency,
        "stock_reel": stock_reel,
        "desiredstock": desiredstock,
        "seuil_stock_alerte": seuil_stock_alerte,
        "default_warehouse": data.get("fk_default_warehouse"),
        "barcode": data.get("barcode"),
    }


def dolibarr_to_product_detail(data: dict[str, Any], currency: str = "EUR") -> dict[str, Any]:
    """
    Convert Dolibarr product to ProductDetail dict (extends summary).

    Args:
        data: Dolibarr product response
        currency: ISO 4217 currency code from CompanyContext

    Returns:
        Dict with ProductDetail fields
    """
    summary = dolibarr_to_product_summary(data, currency)

    # Additional fields
    summary.update({
        "description": data.get("description"),
        "price_min": _to_decimal(data.get("price_min")) if data.get("price_min") is not None else None,
        "price_base_type": data.get("price_base_type"),  # "HT" or "TTC"
        "weight": _to_decimal(data.get("weight")) if data.get("weight") is not None else None,
        "weight_units": data.get("weight_units"),
        "length": _to_decimal(data.get("length")) if data.get("length") is not None else None,
        "surface": _to_decimal(data.get("surface")) if data.get("surface") is not None else None,
        "volume": _to_decimal(data.get("volume")) if data.get("volume") is not None else None,
        "units": data.get("units"),
        # Supplier info if present
        "supplier_info": _extract_supplier_info(data) if _has_supplier_info(data) else None,
        # Extrafields
        "extrafields": _extract_extrafields(data) if _has_extrafields(data) else None,
    })

    return summary


# =========================================================================
# MAPPERS PARA FACTURAS PROVEEDOR
# =========================================================================


def supplier_invoice_to_dolibarr(data: dict[str, Any]) -> dict[str, Any]:
    """Convertir factura proveedor a formato Dolibarr."""
    # Dolibarr usa 'socid' para proveedor, 'thirdparty_id' en nuestro modelo
    result = data.copy()
    if "thirdparty_id" in result and "socid" not in result:
        result["socid"] = result.pop("thirdparty_id")

    # Mapear campos
    field_mapping = {
        "invoice_number": "ref",
        "invoice_date": "date",
        "due_date": "date_lim_reglement",
        "subtotal": "total_ht",
        "tax_total": "total_tva",
        "total": "total_ttc",
        "currency": "fk_multicurrency",
        "payment_term_id": "fk_cond_reglement",
        "note": "note_private",
    }

    mapped = {}
    for our_key, dolibarr_key in field_mapping.items():
        if our_key in result:
            mapped[dolibarr_key] = result.pop(our_key)

    result.update(mapped)
    return result


def dolibarr_to_supplier_invoice(data: dict[str, Any]) -> dict[str, Any]:
    """Convertir factura proveedor Dolibarr a formato normalizado."""
    field_mapping = {
        "rowid": "id",
        "ref": "invoice_number",
        "date": "invoice_date",
        "date_lim_reglement": "due_date",
        "total_ht": "subtotal",
        "total_tva": "tax_total",
        "total_ttc": "total",
        "fk_multicurrency": "currency",
        "fk_cond_reglement": "payment_term_id",
        "note_private": "note",
        "date_creation": "datec",
        "date_modification": "datem",
        "fk_user_creat": "fk_user_author",
        "fk_user_modif": "fk_user_modif",
        "socid": "thirdparty_id",
    }

    result = {}
    for key, value in data.items():
        our_key = field_mapping.get(key, key)
        if our_key in ("datec", "datem", "date", "date_lim_reglement") and isinstance(value, (int, float)):
            result[our_key] = datetime.fromtimestamp(value)
        else:
            result[our_key] = value

    return result


# =========================================================================
# MAPPERS PARA FACTURAS CLIENTE
# =========================================================================


def _map_invoice_status(status_code: int) -> str:
    """Mapear código de estado Dolibarr a string normalizado."""
    mapping = {
        0: "draft",
        1: "validated",
        2: "paid",
        3: "cancelled",
    }
    return mapping.get(status_code, "unknown")


def _to_decimal(value: Any) -> Decimal:
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


def _timestamp_to_date(value: Any) -> date | None:
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


def dolibarr_to_customer_invoice(data: dict[str, Any]) -> dict[str, Any]:
    """
    Convertir factura cliente Dolibarr a dict normalizado para CustomerInvoiceSummary.

    Args:
        data: Respuesta de Dolibarr API para factura cliente

    Returns:
        Dict con campos: id, ref, thirdparty_id, thirdparty_name, date, due_date,
        status, total_ht, total_ttc, paid_amount, remaining_amount
    """
    # Extraer thirdparty info
    thirdparty_id = data.get("fk_soc") or data.get("socid")
    thirdparty_name = data.get("soc_name") or data.get("thirdparty_name") or "Sin nombre"

    # Montos
    total_ht = _to_decimal(data.get("total_ht") or data.get("total_ht"))
    total_ttc = _to_decimal(data.get("total_ttc") or data.get("total_ttc"))
    paid_amount = _to_decimal(data.get("total_paid") or data.get("paid_amount") or data.get("amount_paid"))

    # remaining_amount: distinguir entre campo ausente y valor 0 explícito
    remaining_amount_raw = None
    for field in ("total_remain", "remaining_amount", "total_to_pay"):
        if field in data and data[field] is not None:
            remaining_amount_raw = data[field]
            break

    if remaining_amount_raw is not None:
        remaining_amount = _to_decimal(remaining_amount_raw)
    elif total_ttc > 0:
        # Solo calcular si el campo no viene en la respuesta
        remaining_amount = total_ttc - paid_amount
    else:
        remaining_amount = Decimal("0")

    return {
        "id": data.get("id") or data.get("rowid"),
        "ref": data.get("ref"),
        "thirdparty_id": thirdparty_id,
        "thirdparty_name": thirdparty_name,
        "date": _timestamp_to_date(data.get("date") or data.get("datec")),
        "due_date": _timestamp_to_date(data.get("date_lim_reglement")),
        "status": _map_invoice_status(data.get("status", 0)),
        "total_ht": total_ht,
        "total_ttc": total_ttc,
        "paid_amount": paid_amount,
        "remaining_amount": remaining_amount,
    }


def dolibarr_to_supplier_invoice_summary(data: dict[str, Any]) -> dict[str, Any]:
    """
    Convertir factura proveedor Dolibarr a dict normalizado para SupplierInvoiceSummary.

    Args:
        data: Respuesta de Dolibarr API para factura proveedor

    Returns:
        Dict con campos: id, ref, thirdparty_id, thirdparty_name, date, due_date,
        status, total_ht, total_ttc, paid_amount, remaining_amount
    """
    # Extraer thirdparty info (proveedor usa socid)
    thirdparty_id = data.get("socid") or data.get("fk_soc")
    thirdparty_name = data.get("soc_name") or data.get("thirdparty_name") or "Sin nombre"

    # Montos
    total_ht = _to_decimal(data.get("total_ht") or data.get("total_ht"))
    total_ttc = _to_decimal(data.get("total_ttc") or data.get("total_ttc"))
    paid_amount = _to_decimal(data.get("total_paid") or data.get("paid_amount") or data.get("amount_paid"))

    # remaining_amount: distinguir entre campo ausente y valor 0 explícito
    remaining_amount_raw = None
    for field in ("total_remain", "remaining_amount", "total_to_pay"):
        if field in data and data[field] is not None:
            remaining_amount_raw = data[field]
            break

    if remaining_amount_raw is not None:
        remaining_amount = _to_decimal(remaining_amount_raw)
    elif total_ttc > 0:
        # Solo calcular si el campo no viene en la respuesta
        remaining_amount = total_ttc - paid_amount
    else:
        remaining_amount = Decimal("0")

    return {
        "id": data.get("id") or data.get("rowid"),
        "ref": data.get("ref"),
        "thirdparty_id": thirdparty_id,
        "thirdparty_name": thirdparty_name,
        "date": _timestamp_to_date(data.get("date") or data.get("datec")),
        "due_date": _timestamp_to_date(data.get("date_lim_reglement")),
        "status": _map_invoice_status(data.get("status", 0)),
        "total_ht": total_ht,
        "total_ttc": total_ttc,
        "paid_amount": paid_amount,
        "remaining_amount": remaining_amount,
    }
