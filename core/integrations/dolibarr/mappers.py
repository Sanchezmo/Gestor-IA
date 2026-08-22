"""
Mappers para convertir entre esquemas Pydantic y formato Dolibarr.

ADAPTADO desde Transvega Animal - adapters/dolibarr/mappers.py
Genérico para terceros, productos, facturas.
"""

from datetime import datetime
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
