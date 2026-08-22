from __future__ import annotations

"""
Modelos Pydantic para Structured Output del Intent Interpreter.

Estos modelos definen el contrato estricto que Ollama debe cumplir.
No se permite extra="allow" - solo campos explícitos.
"""

from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, Field, field_validator

# =========================================================================
# ENUMS PARA INTENTS
# =========================================================================


class ThirdpartyAction(StrEnum):
    """Acciones soportadas para terceros."""

    LIST = "list_thirdparties"
    SEARCH = "search_thirdparties"
    GET = "get_thirdparty"
    COUNT = "count_thirdparties"


class ThirdpartyPartyType(StrEnum):
    """Tipo de tercero a filtrar."""

    ALL = "all"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class SortField(StrEnum):
    """Campos ordenables permitidos (allowlist)."""

    ROWID = "rowid"
    NAME = "name"
    REF = "ref"
    DATE_CREATION = "date_creation"
    DATE_MODIFICATION = "date_modification"
    EMAIL = "email"
    PHONE = "phone"
    CLIENT = "client"
    FOURNISSEUR = "fournisseur"
    STATUS = "status"


class SortOrder(StrEnum):
    """Órdenes permitidos."""

    ASC = "ASC"
    DESC = "DESC"


# =========================================================================
# MODELOS DE ARGUMENTOS POR ACCIÓN
# =========================================================================


class ListThirdpartiesArgs(BaseModel):
    """Argumentos para list_thirdparties."""

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    party_type: ThirdpartyPartyType = ThirdpartyPartyType.ALL
    sort_field: SortField = SortField.NAME
    sort_order: SortOrder = SortOrder.ASC


class SearchThirdpartiesArgs(BaseModel):
    """Argumentos para search_thirdparties."""

    query: str = Field(..., min_length=1, max_length=200)
    party_type: ThirdpartyPartyType = ThirdpartyPartyType.ALL
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_field: SortField = SortField.NAME
    sort_order: SortOrder = SortOrder.ASC


class GetThirdpartyArgs(BaseModel):
    """Argumentos para get_thirdparty."""

    thirdparty_id: int = Field(..., gt=0)


class CountThirdpartiesArgs(BaseModel):
    """Argumentos para count_thirdparties."""

    party_type: ThirdpartyPartyType = ThirdpartyPartyType.ALL


# =========================================================================
# UNION DE ARGUMENTOS
# =========================================================================


ThirdpartyArgs = ListThirdpartiesArgs | SearchThirdpartiesArgs | GetThirdpartyArgs | CountThirdpartiesArgs


# =========================================================================
# INTENT ESTRUCTURADO PRINCIPAL
# =========================================================================


class StructuredIntent(BaseModel):
    """
    Intent estructurado validado por Pydantic.

    Este es el contrato que Ollama debe cumplir via structured output.
    No se permite extra="allow" - solo campos definidos explícitamente.
    """

    action: ThirdpartyAction
    arguments: ThirdpartyArgs

    # Metadatos opcionales
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    raw_text: str | None = Field(default=None, max_length=500)

    @field_validator("arguments", mode="before")
    @classmethod
    def _validate_arguments_match_action(cls, v: Any, info: Any) -> Any:
        """Validar que los argumentos coincidan con la acción."""
        if not isinstance(v, dict):
            return v

        action = info.data.get("action")
        if not action:
            return v

        # Validación cruzada básica
        if action == ThirdpartyAction.GET:
            if "thirdparty_id" not in v:
                raise ValueError("GET requiere thirdparty_id")
        elif action == ThirdpartyAction.SEARCH:
            if "query" not in v or not v["query"]:
                raise ValueError("SEARCH requiere query no vacía")

        return v


# =========================================================================
# RESULTADO DE INTERPRETACIÓN
# =========================================================================


class InterpretationStatus(StrEnum):
    """Estado del resultado de interpretación."""

    MATCHED = "matched"
    NO_MATCH = "no_match"
    NEEDS_CLARIFICATION = "needs_clarification"
    INVALID_OUTPUT = "invalid_output"
    PROVIDER_ERROR = "provider_error"


class IntentInterpretation(BaseModel):
    """
    Resultado completo de una interpretación.

    Incluye el intent estructurado (si matched) o información de error/clarificación.
    """

    status: InterpretationStatus
    intent: StructuredIntent | None = None
    clarification_message: str | None = None
    error_message: str | None = None
    fallback_used: bool = False
    interpreter_used: str = "unknown"

    def is_actionable(self) -> bool:
        """Verificar si el resultado puede ejecutarse como Tool."""
        return self.status == InterpretationStatus.MATCHED and self.intent is not None


# =========================================================================
# CATÁLOGO DE TOOLS PARA PROMPT
# =========================================================================


class ToolSchema(BaseModel):
    """Esquema reducido de una Tool para el prompt del LLM."""

    name: str
    description: str
    arguments_schema: dict[str, Any]


# =========================================================================
# HELPER: CONVERTIR STRUCTURED INTENT A TOOL CALL
# =========================================================================


def structured_intent_to_tool_call(intent: StructuredIntent) -> tuple[str, dict[str, Any]]:
    """
    Convertir StructuredIntent a (tool_name, parameters) para ToolRegistry.

    Args:
        intent: Intent validado

    Returns:
        Tupla (tool_name, kwargs_dict)
    """
    action = intent.action
    args = intent.arguments

    if action == ThirdpartyAction.LIST:
        list_args = cast(ListThirdpartiesArgs, args)
        filter_customer = None
        if list_args.party_type == ThirdpartyPartyType.CUSTOMER:
            filter_customer = True
        elif list_args.party_type == ThirdpartyPartyType.SUPPLIER:
            filter_customer = False

        return "list_thirdparties", {
            "limit": list_args.limit,
            "offset": list_args.offset,
            "filter_customer": filter_customer,
            "sort_field": list_args.sort_field.value,
            "sort_order": list_args.sort_order.value,
        }

    elif action == ThirdpartyAction.SEARCH:
        search_args = cast(SearchThirdpartiesArgs, args)
        filter_customer = None
        filter_supplier = None
        if search_args.party_type == ThirdpartyPartyType.CUSTOMER:
            filter_customer = True
            filter_supplier = False
        elif search_args.party_type == ThirdpartyPartyType.SUPPLIER:
            filter_customer = False
            filter_supplier = True

        return "search_thirdparties", {
            "query": search_args.query,
            "filter_customer": filter_customer,
            "filter_supplier": filter_supplier,
            "limit": search_args.limit,
            "offset": search_args.offset,
            "sort_field": search_args.sort_field.value,
            "sort_order": search_args.sort_order.value,
        }

    elif action == ThirdpartyAction.GET:
        get_args = cast(GetThirdpartyArgs, args)
        return "get_thirdparty", {
            "thirdparty_id": get_args.thirdparty_id,
        }

    elif action == ThirdpartyAction.COUNT:
        count_args = cast(CountThirdpartiesArgs, args)
        filter_customer = None
        filter_supplier = None
        if count_args.party_type == ThirdpartyPartyType.CUSTOMER:
            filter_customer = True
            filter_supplier = False
        elif count_args.party_type == ThirdpartyPartyType.SUPPLIER:
            filter_customer = False
            filter_supplier = True

        return "count_thirdparties", {
            "filter_customer": filter_customer,
            "filter_supplier": filter_supplier,
            "filter_status": None,
        }

    raise ValueError(f"Acción no soportada: {action}")


# =========================================================================
# CATÁLOGO DE TOOLS PARA EL PROMPT
# =========================================================================


THIRDPARTY_TOOLS_CATALOG: list[ToolSchema] = [
    ToolSchema(
        name="list_thirdparties",
        description="Listar terceros con paginación y filtros opcionales",
        arguments_schema=ListThirdpartiesArgs.model_json_schema(),
    ),
    ToolSchema(
        name="search_thirdparties",
        description="Buscar terceros por texto (nombre, email, teléfono, NIF/CIF, referencia)",
        arguments_schema=SearchThirdpartiesArgs.model_json_schema(),
    ),
    ToolSchema(
        name="get_thirdparty",
        description="Obtener detalle completo de un tercero por ID",
        arguments_schema=GetThirdpartyArgs.model_json_schema(),
    ),
    ToolSchema(
        name="count_thirdparties",
        description="Contar total de terceros con filtros opcionales",
        arguments_schema=CountThirdpartiesArgs.model_json_schema(),
    ),
]


def get_tools_catalog_for_prompt() -> str:
    """Generar representación del catálogo para el system prompt."""
    lines = ["Tools disponibles (solo estas):"]
    for tool in THIRDPARTY_TOOLS_CATALOG:
        lines.append(f"- {tool.name}: {tool.description}")
        # Incluir schema simplificado
        props = tool.arguments_schema.get("properties", {})
        required = tool.arguments_schema.get("required", [])
        args_desc = []
        for prop_name, prop_info in props.items():
            req = " (requerido)" if prop_name in required else ""
            args_desc.append(f"  {prop_name}: {prop_info.get('description', '')}{req}")
        if args_desc:
            lines.append("  Argumentos:")
            lines.extend(args_desc)
    return "\n".join(lines)


# =========================================================================
# FORMATTERS PARA TELEGRAM
# =========================================================================


def format_thirdparties_for_telegram(parties: list[dict[str, Any]], limit: int, offset: int) -> str:
    """Formatear lista de terceros para respuesta Telegram."""
    if not parties:
        return "No se han encontrado terceros."

    lines = ["Terceros encontrados:"]
    for i, p in enumerate(parties, 1):
        tipo = []
        if p.get("is_customer"):
            tipo.append("Cliente")
        if p.get("is_supplier"):
            tipo.append("Proveedor")
        tipo_str = f" ({', '.join(tipo)})" if tipo else ""
        email_str = f" - {p['email']}" if p.get("email") else ""
        phone_str = f" - {p['phone']}" if p.get("phone") else ""
        lines.append(f"{i}. {p['name']}{tipo_str}{email_str}{phone_str}")

    if len(parties) >= limit:
        lines.append(f"\nMostrando los primeros {limit} resultados (offset {offset}).")

    return "\n".join(lines)


def format_thirdparty_detail_for_telegram(detail: dict[str, Any]) -> str:
    """Formatear detalle de tercero para respuesta Telegram."""
    lines = [f"📋 *{detail['name']}*"]
    if detail.get("ref"):
        lines.append(f"Ref: {detail['ref']}")
    if detail.get("vat_number"):
        lines.append(f"NIF/CIF: {detail['vat_number']}")
    if detail.get("email"):
        lines.append(f"Email: {detail['email']}")
    if detail.get("phone"):
        lines.append(f"Teléfono: {detail['phone']}")
    if detail.get("address"):
        addr_parts = [detail["address"]]
        if detail.get("zip"):
            addr_parts.append(detail["zip"])
        if detail.get("town"):
            addr_parts.append(detail["town"])
        lines.append(f"Dirección: {', '.join(addr_parts)}")

    tipo = []
    if detail.get("is_customer"):
        tipo.append("Cliente")
    if detail.get("is_supplier"):
        tipo.append("Proveedor")
    if tipo:
        lines.append(f"Tipo: {', '.join(tipo)}")

    status_map = {0: "Borrador", 1: "Validado", 2: "Enviado"}
    status = status_map.get(detail.get("status", 0), f"Status {detail.get('status', 0)}")
    lines.append(f"Estado: {status}")

    return "\n".join(lines)


def format_count_for_telegram(count: int, party_type: ThirdpartyPartyType) -> str:
    """Formatear respuesta de conteo para Telegram."""
    if party_type == ThirdpartyPartyType.CUSTOMER:
        return f"Hay {count} clientes registrados."
    elif party_type == ThirdpartyPartyType.SUPPLIER:
        return f"Hay {count} proveedores registrados."
    else:
        return f"Hay {count} terceros registrados (clientes + proveedores)."
