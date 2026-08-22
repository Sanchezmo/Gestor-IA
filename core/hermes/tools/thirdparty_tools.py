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

# Search fields that Dolibarr API supports via sqlfilters
ALLOWED_THIRDPARTY_SEARCH_FIELDS: frozenset[str] = frozenset(
    {
        "name",  # Nombre comercial
        "email",  # Email
        "phone",  # Teléfono
        "vat_number",  # NIF/CIF
        "ref",  # Referencia
        "address",  # Dirección
        "town",  # Ciudad
        "zip",  # Código postal
    }
)

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


@dataclass(frozen=True, slots=True)
class ThirdpartyDetail:
    """Detalle completo de tercero para respuesta estructurada."""

    id: int
    name: str
    ref: str | None
    email: str | None
    phone: str | None
    vat_number: str | None
    address: str | None
    town: str | None
    zip: str | None
    is_customer: bool
    is_supplier: bool
    status: int
    date_creation: str | None
    date_modification: str | None


@dataclass(frozen=True, slots=True)
class SearchThirdpartiesParams:
    """Parámetros para search_thirdparties."""

    query: str
    filter_customer: bool | None = None  # True=clientes, False=proveedores, None=todos
    filter_supplier: bool | None = None  # True=proveedores, False=no proveedores, None=todos
    limit: int = 20
    offset: int = 0
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
        # Validate query
        if not self.query or not self.query.strip():
            raise ValueError("El parámetro 'query' no puede estar vacío")
        if len(self.query) > 200:
            raise ValueError("El parámetro 'query' es demasiado largo (máx 200 caracteres)")
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
        # Validate limit
        if self.limit < 1 or self.limit > 100:
            raise ValueError("El parámetro 'limit' debe estar entre 1 y 100")
        if self.offset < 0:
            raise ValueError("El parámetro 'offset' debe ser >= 0")

    def to_dolibarr_params(self) -> dict[str, Any]:
        """Convertir a parámetros Dolibarr con sqlfilters para búsqueda."""
        params: dict[str, Any] = {
            "limit": self.limit,
            "offset": self.offset,
            "sortfield": self.sort_field,
            "sortorder": self.sort_order,
        }

        # Build sqlfilters for search
        sqlfilters_parts: list[str] = []

        # Search query - use multiple fields with OR
        # Dolibarr supports: (t.name:like:'%query%' OR t.email:like:'%query%' ...)
        search_term = self.query.strip()
        if search_term:
            # Escape special chars for Dolibarr LIKE
            escaped = search_term.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
            search_conditions = [
                f"t.name:like:'%{escaped}%'",
                f"t.email:like:'%{escaped}%'",
                f"t.phone:like:'%{escaped}%'",
                f"t.tva_intra:like:'%{escaped}%'",
                f"t.ref:like:'%{escaped}%'",
            ]
            sqlfilters_parts.append(f"({' OR '.join(search_conditions)})")

        # Filter by customer/supplier
        if self.filter_customer is not None:
            sqlfilters_parts.append(f"t.client:={1 if self.filter_customer else 0}")
        if self.filter_supplier is not None:
            sqlfilters_parts.append(f"t.fournisseur:={1 if self.filter_supplier else 0}")

        if sqlfilters_parts:
            params["sqlfilters"] = " AND ".join(sqlfilters_parts)

        return params


@dataclass(frozen=True, slots=True)
class GetThirdpartyParams:
    """Parámetros para get_thirdparty."""

    thirdparty_id: int

    def __post_init__(self) -> None:
        if self.thirdparty_id <= 0:
            raise ValueError("El parámetro 'thirdparty_id' debe ser > 0")


@dataclass(frozen=True, slots=True)
class CountThirdpartiesParams:
    """Parámetros para count_thirdparties."""

    filter_customer: bool | None = None  # True=clientes, False=proveedores, None=todos
    filter_supplier: bool | None = None  # True=proveedores, False=no proveedores, None=todos
    filter_status: int | None = None  # 0=borrador, 1=activo, etc.

    def __post_init__(self) -> None:
        if self.filter_status is not None and self.filter_status < 0:
            raise ValueError("El parámetro 'filter_status' debe ser >= 0")

    def to_dolibarr_params(self) -> dict[str, Any]:
        """Convertir a parámetros Dolibarr para conteo."""
        params: dict[str, Any] = {"limit": 1}  # Solo necesitamos 1 para verificar existencia
        sqlfilters_parts: list[str] = []

        if self.filter_customer is not None:
            sqlfilters_parts.append(f"t.client:={1 if self.filter_customer else 0}")
        if self.filter_supplier is not None:
            sqlfilters_parts.append(f"t.fournisseur:={1 if self.filter_supplier else 0}")
        if self.filter_status is not None:
            sqlfilters_parts.append(f"t.status:={self.filter_status}")

        if sqlfilters_parts:
            params["sqlfilters"] = " AND ".join(sqlfilters_parts)

        return params


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
# SEARCH THIRDPARTIES TOOL
# =========================================================================


class SearchThirdpartiesTool(Tool):
    """
    Tool para buscar terceros de Dolibarr por texto libre.

    Permiso requerido: thirdparty.read
    Canales compatibles: Telegram, API, LLM, CLI
    """

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="search_thirdparties",
            description="Buscar terceros (clientes/proveedores) por nombre, email, teléfono, NIF/CIF o referencia",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Término de búsqueda (nombre, email, teléfono, NIF/CIF, referencia)",
                        "minLength": 1,
                        "maxLength": 200,
                    },
                    "filter_customer": {"type": "boolean", "description": "True=solo clientes, False=solo no clientes"},
                    "filter_supplier": {
                        "type": "boolean",
                        "description": "True=solo proveedores, False=solo no proveedores",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "sort_field": {
                        "type": "string",
                        "enum": sorted(ALLOWED_THIRDPARTY_SORT_FIELDS),
                        "default": "name",
                    },
                    "sort_order": {"type": "string", "enum": sorted(ALLOWED_SORT_ORDERS), "default": "ASC"},
                },
                "required": ["query"],
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
            search_params = SearchThirdpartiesParams(**params)
        except Exception as e:
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message=f"Parámetros inválidos: {e}",
            )

        # Crear cliente Dolibarr para ESTA instancia
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # Llamar a Dolibarr
                raw_parties = await client.list_thirdparties(**search_params.to_dolibarr_params())

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
                        "limit": search_params.limit,
                        "offset": search_params.offset,
                        "has_more": len(parties) == search_params.limit,
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
# GET THIRDPARTY TOOL
# =========================================================================


class GetThirdpartyTool(Tool):
    """
    Tool para obtener detalle de un tercero por ID.

    Permiso requerido: thirdparty.read
    Canales compatibles: Telegram, API, LLM, CLI
    """

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="get_thirdparty",
            description="Obtener detalle completo de un tercero por su ID",
            parameters_schema={
                "type": "object",
                "properties": {
                    "thirdparty_id": {"type": "integer", "minimum": 1, "description": "ID del tercero en Dolibarr"},
                },
                "required": ["thirdparty_id"],
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
            get_params = GetThirdpartyParams(**params)
        except Exception as e:
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message=f"Parámetros inválidos: {e}",
            )

        # Crear cliente Dolibarr para ESTA instancia
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                raw_party = await client.get_thirdparty(get_params.thirdparty_id)

                # Transformar a detalle completo
                detail = ThirdpartyDetail(
                    id=raw_party.get("id", 0),
                    name=raw_party.get("name", "Sin nombre"),
                    ref=raw_party.get("ref"),
                    email=raw_party.get("email"),
                    phone=raw_party.get("phone"),
                    vat_number=raw_party.get("vat_number") or raw_party.get("tva_intra"),
                    address=raw_party.get("address"),
                    town=raw_party.get("town"),
                    zip=raw_party.get("zip"),
                    is_customer=bool(raw_party.get("client", 0)),
                    is_supplier=bool(raw_party.get("fournisseur", 0)),
                    status=raw_party.get("status", 0),
                    date_creation=raw_party.get("date_creation"),
                    date_modification=raw_party.get("date_modification"),
                )

                return ToolResult.ok(
                    data={
                        "thirdparty": {
                            "id": detail.id,
                            "name": detail.name,
                            "ref": detail.ref,
                            "email": detail.email,
                            "phone": detail.phone,
                            "vat_number": detail.vat_number,
                            "address": detail.address,
                            "town": detail.town,
                            "zip": detail.zip,
                            "is_customer": detail.is_customer,
                            "is_supplier": detail.is_supplier,
                            "status": detail.status,
                            "date_creation": detail.date_creation,
                            "date_modification": detail.date_modification,
                        }
                    },
                    metadata={
                        "instance_id": company_context.instance_id,
                        "dolibarr_user_id": user_context.dolibarr_user_id,
                    },
                )

        except DolibarrException as e:
            if e.status_code == 404:
                return ToolResult.error(
                    error_code="NOT_FOUND",
                    error_message="Tercero no encontrado",
                )
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
# COUNT THIRDPARTIES TOOL
# =========================================================================


class CountThirdpartiesTool(Tool):
    """
    Tool para contar terceros de Dolibarr.

    Permiso requerido: thirdparty.read
    Canales compatibles: Telegram, API, LLM, CLI
    """

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="count_thirdparties",
            description="Contar total de terceros (clientes/proveedores) con filtros opcionales",
            parameters_schema={
                "type": "object",
                "properties": {
                    "filter_customer": {"type": "boolean", "description": "True=solo clientes, False=solo no clientes"},
                    "filter_supplier": {
                        "type": "boolean",
                        "description": "True=solo proveedores, False=solo no proveedores",
                    },
                    "filter_status": {
                        "type": "integer",
                        "description": "Filtrar por status (0=borrador, 1=activo)",
                        "minimum": 0,
                    },
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
            count_params = CountThirdpartiesParams(**params)
        except Exception as e:
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message=f"Parámetros inválidos: {e}",
            )

        # Crear cliente Dolibarr para ESTA instancia
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # Obtener lista para contar (Dolibarr no tiene endpoint /count nativo)
                # Usamos list con limit grande para obtener el total real
                raw_parties = await client.list_thirdparties(**count_params.to_dolibarr_params())
                total_count = len(raw_parties)

                # Si hay más resultados de los que pedimos, Dolibarr no devuelve el total
                # En ese caso hacemos una segunda llamada sin limit para contar
                # NOTA: Esto es una limitación de la API Dolibarr
                if total_count == 1:  # Nuestro limit=1
                    # Hacer conteo real paginando
                    total_count = 0
                    offset = 0
                    page_size = 100
                    while True:
                        params = count_params.to_dolibarr_params()
                        params["limit"] = page_size
                        params["offset"] = offset
                        parties = await client.list_thirdparties(**params)
                        if not parties:
                            break
                        total_count += len(parties)
                        if len(parties) < page_size:
                            break
                        offset += page_size

                return ToolResult.ok(
                    data={
                        "count": total_count,
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
    tool_registry.register_core_tool(SearchThirdpartiesTool())
    tool_registry.register_core_tool(GetThirdpartyTool())
    tool_registry.register_core_tool(CountThirdpartiesTool())
