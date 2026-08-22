"""
Tools para gestión de terceros (clientes/proveedores) en Dolibarr.

Core tools disponibles para todas las instancias.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.tools import Tool, ToolDefinition, ToolResult, tool_registry
from core.integrations.dolibarr.client import DolibarrException

# =========================================================================
# ALLOWLISTS FOR DOLIBARR PARAMETERS
# =========================================================================

# Dolibarr thirdparty sortable fields (confirmed from Dolibarr API)
ALLOWED_THIRDPARTY_SORT_FIELDS: frozenset[str] = frozenset(
    {
        "rowid",  # ID interno
        "name",  # Nombre
        "ref",  # Referencia
        "date_creation",  # Fecha creación
        "date_modification",  # Fecha modificación
        "email",  # Email
        "phone",  # Teléfono
        "client",  # Es cliente (0/1)
        "fournisseur",  # Es proveedor (0/1)
        "status",  # Estado
    }
)

ALLOWED_SORT_ORDERS: frozenset[str] = frozenset({"ASC", "DESC"})

# =========================================================================
# LIST THIRDPARTIES TOOL
# =========================================================================


@dataclass(frozen=True, slots=True)
class ListThirdpartiesParams:
    """Parámetros para list_thirdparties."""

    limit: int = 20
    offset: int = 0
    filter_customer: bool | None = None  # True=clientes, False=proveedores, None=todos
    filter_status: int | None = None  # 0=borrador, 1=activo, etc.
    sort_field: Literal[
        "rowid",
        "name",
        "ref",
        "date_creation",
        "date_modification",
        "email",
        "phone",
        "client",
        "fournisseur",
        "status",
    ] = "name"
    sort_order: Literal["ASC", "DESC"] = "ASC"

    def __post_init__(self) -> None:
        # Validate sort_field against allowlist
        if self.sort_field not in ALLOWED_THIRDPARTY_SORT_FIELDS:
            raise ValueError(
                f"sort_field '{self.sort_field}' no permitido. "
                f"Valores permitidos: {', '.join(sorted(ALLOWED_THIRDPARTY_SORT_FIELDS))}"
            )
        # Validate sort_order against allowlist
        if self.sort_order not in ALLOWED_SORT_ORDERS:
            raise ValueError(
                f"sort_order '{self.sort_order}' no permitido. "
                f"Valores permitidos: {', '.join(sorted(ALLOWED_SORT_ORDERS))}"
            )

    def to_dolibarr_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": self.limit,
            "offset": self.offset,
            "sortfield": self.sort_field,
            "sortorder": self.sort_order,
        }
        if self.filter_customer is not None:
            params["sqlfilters"] = f"t.client:={1 if self.filter_customer else 0}"
        if self.filter_status is not None:
            if "sqlfilters" in params:
                params["sqlfilters"] = params["sqlfilters"] + f" AND t.status:={self.filter_status}"
            else:
                params["sqlfilters"] = f"t.status:={self.filter_status}"
        return params


@dataclass(frozen=True, slots=True)
class ThirdpartySummary:
    """Resumen de tercero para respuesta Telegram/UI."""

    id: int
    name: str
    is_customer: bool
    is_supplier: bool
    email: str | None
    phone: str | None
    status: int  # 0=borrador, 1=validado


class ListThirdpartiesTool(Tool):
    """
    Tool para listar terceros de Dolibarr.

    Permiso requerido: thirdparty.read
    Canales compatibles: Telegram, API, LLM, CLI
    """

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="list_thirdparties",
            description="Listar terceros (clientes/proveedores) de Dolibarr con paginación",
            parameters_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "filter_customer": {"type": "boolean", "description": "True=solo clientes, False=solo proveedores"},
                    "filter_status": {"type": "integer", "description": "Filtrar por status (0=borrador, 1=activo)"},
                    "sort_field": {
                        "type": "string",
                        "enum": sorted(ALLOWED_THIRDPARTY_SORT_FIELDS),
                        "default": "name",
                    },
                    "sort_order": {"type": "string", "enum": sorted(ALLOWED_SORT_ORDERS), "default": "ASC"},
                },
                "additionalProperties": False,
            },
            required_permissions=frozenset(["thirdparty.read"]),
            is_core=True,
        )
        super().__init__(definition)

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        **params: Any,
    ) -> ToolResult:
        # Validar y parsear parámetros
        try:
            list_params = ListThirdpartiesParams(**params)
        except Exception as e:
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message=f"Parámetros inválidos: {e}",
            )

        # Validación adicional de valores (dataclass no valida por sí solo)
        if list_params.limit < 1 or list_params.limit > 100:
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message="El parámetro 'limit' debe estar entre 1 y 100",
            )
        if list_params.offset < 0:
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message="El parámetro 'offset' debe ser >= 0",
            )
        if list_params.sort_order not in ("ASC", "DESC"):
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message="El parámetro 'sort_order' debe ser 'ASC' o 'DESC'",
            )

        # Crear cliente Dolibarr para ESTA instancia
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # Llamar a Dolibarr
                raw_parties = await client.list_thirdparties(**list_params.to_dolibarr_params())

                # Transformar a resumen útil
                parties = [
                    ThirdpartySummary(
                        id=p.get("id", 0),
                        name=p.get("name", "Sin nombre"),
                        is_customer=bool(p.get("client", 0)),
                        is_supplier=bool(p.get("fournisseur", 0)),
                        email=p.get("email"),
                        phone=p.get("phone"),
                        status=p.get("status", 0),
                    )
                    for p in raw_parties
                ]

                return ToolResult.ok(
                    data={
                        "thirdparties": [
                            {
                                "id": p.id,
                                "name": p.name,
                                "is_customer": p.is_customer,
                                "is_supplier": p.is_supplier,
                                "email": p.email,
                                "phone": p.phone,
                                "status": p.status,
                            }
                            for p in parties
                        ],
                        "count": len(parties),
                        "limit": list_params.limit,
                        "offset": list_params.offset,
                        "has_more": len(parties) == list_params.limit,
                    },
                    metadata={
                        "instance_id": company_context.instance_id,
                        "dolibarr_user_id": user_context.dolibarr_user_id,
                    },
                )

        except DolibarrException as e:
            # Error de Dolibarr - NO exponer detalles internos
            return ToolResult.error(
                error_code="DOLIBARR_ERROR",
                error_message="No he podido consultar Dolibarr en este momento",
                metadata={
                    "endpoint": e.endpoint,
                    "status_code": e.status_code,
                },
            )
        except Exception:
            # Error inesperado
            return ToolResult.error(
                error_code="INTERNAL_ERROR",
                error_message="Error interno procesando la solicitud",
            )


# =========================================================================
# REGISTRO AUTOMÁTICO
# =========================================================================


def register_core_thirdparty_tools() -> None:
    """Registrar tools de terceros en el registry global."""
    tool_registry.register_core_tool(ListThirdpartiesTool())
