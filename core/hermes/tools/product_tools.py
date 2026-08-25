"""
Tools para consulta de catálogo de productos/servicios en Dolibarr - READ ONLY.

Permiso requerido: product.read
Canales compatibles: Telegram, API, LLM, CLI
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.tools.base import Tool, ToolDefinition, ToolResult, tool_registry
from core.integrations.dolibarr.client import DolibarrException
from core.integrations.dolibarr.mappers import (
    dolibarr_to_product_summary,
    dolibarr_to_product_detail,
)

# =========================================================================
# ALLOWLISTS FOR DOLIBARR PARAMETERS
# =========================================================================

ALLOWED_PRODUCT_SORT_FIELDS: frozenset[str] = frozenset(
    {
        "rowid",
        "ref",
        "label",
        "description",
        "type",
        "status",
        "price",
        "price_ttc",
        "tva_tx",
        "stock_reel",
        "date_creation",
        "date_modification",
    }
)

ALLOWED_SORT_ORDERS: frozenset[str] = frozenset({"ASC", "DESC"})

PRODUCT_TYPE_MAP = {"PRODUCT": 0, "SERVICE": 1}


# =========================================================================
# PARAMETER DATACLASSES
# =========================================================================


@dataclass(frozen=True, slots=True)
class ListProductsParams:
    """Parámetros para list_products."""

    limit: int = 20
    page: int = 1
    product_type: Literal["PRODUCT", "SERVICE"] | None = None
    status: int | None = None
    sort_field: Literal[
        "rowid",
        "ref",
        "label",
        "description",
        "type",
        "status",
        "price",
        "price_ttc",
        "tva_tx",
        "stock_reel",
        "date_creation",
        "date_modification",
    ] = "label"
    sort_order: Literal["ASC", "DESC"] = "ASC"

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 100:
            raise ValueError("El parámetro 'limit' debe estar entre 1 y 100")
        if self.page < 1:
            raise ValueError("El parámetro 'page' debe ser >= 1")
        if self.sort_order not in ALLOWED_SORT_ORDERS:
            raise ValueError(f"sort_order debe ser ASC o DESC, recibido: {self.sort_order}")
        if self.sort_field not in ALLOWED_PRODUCT_SORT_FIELDS:
            raise ValueError(f"sort_field no permitido: {self.sort_field}")
        if self.status is not None and self.status < 0:
            raise ValueError("El parámetro 'status' debe ser >= 0")

    def to_dolibarr_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": self.limit,
            "page": self.page,
            "sortfield": self.sort_field,
            "sortorder": self.sort_order,
        }
        if self.product_type is not None:
            params["type"] = PRODUCT_TYPE_MAP[self.product_type]
        if self.status is not None:
            params["status"] = self.status
        return params


@dataclass(frozen=True, slots=True)
class SearchProductsParams:
    """Parámetros para search_products."""

    query: str
    limit: int = 20
    page: int = 1
    product_type: Literal["PRODUCT", "SERVICE"] | None = None
    status: int | None = None
    sort_field: Literal[
        "rowid",
        "ref",
        "label",
        "description",
        "type",
        "status",
        "price",
        "price_ttc",
        "tva_tx",
        "stock_reel",
        "date_creation",
        "date_modification",
    ] = "label"
    sort_order: Literal["ASC", "DESC"] = "ASC"

    def __post_init__(self) -> None:
        if not self.query or not self.query.strip():
            raise ValueError("El parámetro 'query' no puede estar vacío")
        if len(self.query) > 200:
            raise ValueError("El parámetro 'query' es demasiado largo (máx 200 caracteres)")
        if self.limit < 1 or self.limit > 100:
            raise ValueError("El parámetro 'limit' debe estar entre 1 y 100")
        if self.page < 1:
            raise ValueError("El parámetro 'page' debe ser >= 1")
        if self.sort_order not in ALLOWED_SORT_ORDERS:
            raise ValueError(f"sort_order debe ser ASC o DESC, recibido: {self.sort_order}")
        if self.sort_field not in ALLOWED_PRODUCT_SORT_FIELDS:
            raise ValueError(f"sort_field no permitido: {self.sort_field}")
        if self.status is not None and self.status < 0:
            raise ValueError("El parámetro 'status' debe ser >= 0")


@dataclass(frozen=True, slots=True)
class GetProductParams:
    """Parámetros para get_product."""

    product_id: int | None = None
    ref: str | None = None

    def __post_init__(self) -> None:
        if self.product_id is None and self.ref is None:
            raise ValueError("Se requiere product_id o ref")
        if self.product_id is not None and self.product_id <= 0:
            raise ValueError("El parámetro 'product_id' debe ser > 0")
        if self.ref is not None and not self.ref.strip():
            raise ValueError("El parámetro 'ref' no puede estar vacío")


@dataclass(frozen=True, slots=True)
class CountProductsParams:
    """Parámetros para count_products."""

    product_type: Literal["PRODUCT", "SERVICE"] | None = None
    status: int | None = None

    def __post_init__(self) -> None:
        if self.status is not None and self.status < 0:
            raise ValueError("El parámetro 'status' debe ser >= 0")

    def to_dolibarr_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": 1, "pagination_data": True}
        sqlfilters_parts: list[str] = []
        if self.product_type is not None:
            sqlfilters_parts.append(f"t.type:={PRODUCT_TYPE_MAP[self.product_type]}")
        if self.status is not None:
            sqlfilters_parts.append(f"t.status:={self.status}")
        if sqlfilters_parts:
            params["sqlfilters"] = " AND ".join(sqlfilters_parts)
        return params


# =========================================================================
# PRODUCT TOOLS
# =========================================================================


class ListProductsTool(Tool):
    """
    Tool para listar productos/servicios de Dolibarr con paginación y filtros.

    Permiso requerido: product.read
    """

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="list_products",
            description="Listar productos/servicios de Dolibarr con paginación y filtros opcionales",
            parameters_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "product_type": {"type": "string", "enum": ["PRODUCT", "SERVICE"]},
                    "status": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Filtrar por status (0=borrador, 1=activo, etc.)",
                    },
                    "sort_field": {
                        "type": "string",
                        "enum": sorted(ALLOWED_PRODUCT_SORT_FIELDS),
                        "default": "label",
                    },
                    "sort_order": {"type": "string", "enum": sorted(ALLOWED_SORT_ORDERS), "default": "ASC"},
                },
                "additionalProperties": False,
            },
            required_permissions=frozenset(["product.read"]),
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
            list_params = ListProductsParams(**params)
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
                raw_products = await client.list_products(**list_params.to_dolibarr_params())

                # Transformar a resumen
                currency = getattr(company_context, "currency", "EUR") or "EUR"
                products = [dolibarr_to_product_summary(p, currency=currency) for p in raw_products]

                return ToolResult.ok(
                    data={
                        "products": [
                            {
                                "id": p.id,
                                "ref": p.ref,
                                "label": p.label,
                                "type": p.type,
                                "status": p.status,
                                "price": str(p.price),
                                "price_ttc": str(p.price_ttc),
                                "vat_rate": str(p.vat_rate),
                                "currency": p.currency,
                                "stock_reel": str(p.stock_reel) if p.stock_reel is not None else None,
                                "desiredstock": str(p.desiredstock) if p.desiredstock is not None else None,
                                "seuil_stock_alerte": str(p.seuil_stock_alerte)
                                if p.seuil_stock_alerte is not None
                                else None,
                                "default_warehouse": p.default_warehouse,
                                "barcode": p.barcode,
                            }
                            for p in products
                        ],
                        "count": len(products),
                        "limit": list_params.limit,
                        "page": list_params.page,
                        "has_more": len(products) == list_params.limit,
                    },
                    metadata={
                        "instance_id": company_context.instance_id,
                        "dolibarr_user_id": user_context.dolibarr_user_id,
                    },
                )

        except DolibarrException as e:
            return ToolResult.error(
                error_code="DOLIBARR_ERROR",
                error_message="No he podido consultar Dolibarr en este momento",
                metadata={
                    "endpoint": e.endpoint,
                    "status_code": e.status_code,
                },
            )
        except Exception:
            return ToolResult.error(
                error_code="INTERNAL_ERROR",
                error_message="Error interno procesando la solicitud",
            )


class SearchProductsTool(Tool):
    """
    Tool para buscar productos/servicios de Dolibarr por texto libre.

    Permiso requerido: product.read
    """

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="search_products",
            description="Buscar productos/servicios por referencia, nombre o descripción",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Término de búsqueda (referencia, nombre, descripción)",
                        "minLength": 1,
                        "maxLength": 200,
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "page": {"type": "integer", "minimum": 1, "default": 1},
                    "product_type": {"type": "string", "enum": ["PRODUCT", "SERVICE"]},
                    "status": {"type": "integer", "minimum": 0, "description": "Filtrar por status"},
                    "sort_field": {
                        "type": "string",
                        "enum": sorted(ALLOWED_PRODUCT_SORT_FIELDS),
                        "default": "label",
                    },
                    "sort_order": {"type": "string", "enum": sorted(ALLOWED_SORT_ORDERS), "default": "ASC"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            required_permissions=frozenset(["product.read"]),
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
            search_params = SearchProductsParams(**params)
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
                raw_products = await client.search_products(
                    query=search_params.query,
                    limit=search_params.limit,
                    page=search_params.page,
                    type=PRODUCT_TYPE_MAP[search_params.product_type] if search_params.product_type else None,
                    status=search_params.status,
                    sortfield=search_params.sort_field,
                    sortorder=search_params.sort_order,
                )

                # Transformar a resumen
                currency = getattr(company_context, "currency", "EUR") or "EUR"
                products = [dolibarr_to_product_summary(p, currency=currency) for p in raw_products]

                return ToolResult.ok(
                    data={
                        "products": [
                            {
                                "id": p.id,
                                "ref": p.ref,
                                "label": p.label,
                                "type": p.type,
                                "status": p.status,
                                "price": str(p.price),
                                "price_ttc": str(p.price_ttc),
                                "vat_rate": str(p.vat_rate),
                                "currency": p.currency,
                                "stock_reel": str(p.stock_reel) if p.stock_reel is not None else None,
                                "desiredstock": str(p.desiredstock) if p.desiredstock is not None else None,
                                "seuil_stock_alerte": str(p.seuil_stock_alerte)
                                if p.seuil_stock_alerte is not None
                                else None,
                                "default_warehouse": p.default_warehouse,
                                "barcode": p.barcode,
                            }
                            for p in products
                        ],
                        "count": len(products),
                        "limit": search_params.limit,
                        "page": search_params.page,
                        "has_more": len(products) == search_params.limit,
                    },
                    metadata={
                        "instance_id": company_context.instance_id,
                        "dolibarr_user_id": user_context.dolibarr_user_id,
                    },
                )

        except DolibarrException as e:
            return ToolResult.error(
                error_code="DOLIBARR_ERROR",
                error_message="No he podido consultar Dolibarr en este momento",
                metadata={
                    "endpoint": e.endpoint,
                    "status_code": e.status_code,
                },
            )
        except Exception:
            return ToolResult.error(
                error_code="INTERNAL_ERROR",
                error_message="Error interno procesando la solicitud",
            )


class GetProductTool(Tool):
    """
    Tool para obtener detalle de un producto/servicio por ID o referencia.

    Permiso requerido: product.read
    """

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="get_product",
            description="Obtener detalle completo de un producto/servicio por su ID o referencia exacta",
            parameters_schema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "minimum": 1, "description": "ID del producto en Dolibarr"},
                    "ref": {"type": "string", "maxLength": 100, "description": "Referencia exacta del producto"},
                },
                "additionalProperties": False,
            },
            required_permissions=frozenset(["product.read"]),
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
            get_params = GetProductParams(**params)
        except Exception as e:
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message=f"Parámetros inválidos: {e}",
            )

        # Crear cliente Dolibarr para ESTA instancia
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # Obtener producto por ID o referencia
                if get_params.product_id is not None:
                    raw_product = await client.get_product(get_params.product_id)
                else:
                    # Buscar por referencia exacta
                    raw_product = await client.get_product_by_ref(get_params.ref)
                    if raw_product is None:
                        return ToolResult.error(
                            error_code="NOT_FOUND",
                            error_message="Producto no encontrado",
                        )

                # Transformar a detalle completo
                currency = getattr(company_context, "currency", "EUR") or "EUR"
                product = dolibarr_to_product_detail(raw_product, currency=currency)

                return ToolResult.ok(
                    data={
                        "product": {
                            "id": product.id,
                            "ref": product.ref,
                            "label": product.label,
                            "type": product.type,
                            "status": product.status,
                            "description": product.description,
                            "price": str(product.price),
                            "price_ttc": str(product.price_ttc),
                            "price_min": str(product.price_min) if product.price_min is not None else None,
                            "price_base_type": product.price_base_type,
                            "vat_rate": str(product.vat_rate),
                            "currency": product.currency,
                            "stock_reel": str(product.stock_reel) if product.stock_reel is not None else None,
                            "desiredstock": str(product.desiredstock) if product.desiredstock is not None else None,
                            "seuil_stock_alerte": str(product.seuil_stock_alerte)
                            if product.seuil_stock_alerte is not None
                            else None,
                            "default_warehouse": product.default_warehouse,
                            "weight": str(product.weight) if product.weight is not None else None,
                            "weight_units": product.weight_units,
                            "length": str(product.length) if product.length is not None else None,
                            "surface": str(product.surface) if product.surface is not None else None,
                            "volume": str(product.volume) if product.volume is not None else None,
                            "units": product.units,
                            "barcode": product.barcode,
                            "supplier_info": product.supplier_info,
                            "extrafields": product.extrafields,
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
                    error_message="Producto no encontrado",
                )
            return ToolResult.error(
                error_code="DOLIBARR_ERROR",
                error_message="No he podido consultar Dolibarr en este momento",
                metadata={
                    "endpoint": e.endpoint,
                    "status_code": e.status_code,
                },
            )
        except Exception:
            return ToolResult.error(
                error_code="INTERNAL_ERROR",
                error_message="Error interno procesando la solicitud",
            )


class CountProductsTool(Tool):
    """
    Tool para contar productos/servicios de Dolibarr.

    Permiso requerido: product.read
    """

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="count_products",
            description="Contar total de productos/servicios con filtros opcionales",
            parameters_schema={
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "enum": ["PRODUCT", "SERVICE"]},
                    "status": {"type": "integer", "minimum": 0, "description": "Filtrar por status"},
                },
                "additionalProperties": False,
            },
            required_permissions=frozenset(["product.read"]),
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
            count_params = CountProductsParams(**params)
        except Exception as e:
            return ToolResult.error(
                error_code="INVALID_PARAMS",
                error_message=f"Parámetros inválidos: {e}",
            )

        # Crear cliente Dolibarr para ESTA instancia
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # Usar pagination_data para conteo eficiente
                result = await client.list_products(**count_params.to_dolibarr_params())

                if isinstance(result, dict) and "pagination" in result:
                    total_count = result["pagination"].get("total", 0)
                else:
                    # Fallback si pagination_data no soportado
                    raw_products = result if isinstance(result, list) else result.get("data", [])
                    total_count = len(raw_products)
                    if total_count == 1:  # limit=1 was used
                        total_count = 0
                        page = 1
                        page_size = 100
                        while True:
                            dolibarr_params = count_params.to_dolibarr_params()
                            dolibarr_params["limit"] = page_size
                            dolibarr_params["page"] = page
                            products = await client.list_products(**dolibarr_params)
                            if not products:
                                break
                            total_count += len(products)
                            if len(products) < page_size:
                                break
                            page += 1

                return ToolResult.ok(
                    data={"count": total_count},
                    metadata={
                        "instance_id": company_context.instance_id,
                        "dolibarr_user_id": user_context.dolibarr_user_id,
                    },
                )

        except DolibarrException as e:
            return ToolResult.error(
                error_code="DOLIBARR_ERROR",
                error_message="No he podido consultar Dolibarr en este momento",
                metadata={
                    "endpoint": e.endpoint,
                    "status_code": e.status_code,
                },
            )
        except Exception:
            return ToolResult.error(
                error_code="INTERNAL_ERROR",
                error_message="Error interno procesando la solicitud",
            )


# =========================================================================
# REGISTRO AUTOMÁTICO
# =========================================================================


def register_core_product_tools() -> None:
    """Registrar tools de productos en el registry global."""
    tool_registry.register_core_tool(ListProductsTool())
    tool_registry.register_core_tool(SearchProductsTool())
    tool_registry.register_core_tool(GetProductTool())
    tool_registry.register_core_tool(CountProductsTool())
