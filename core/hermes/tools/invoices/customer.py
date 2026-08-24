"""
Tools para facturas de cliente en Dolibarr - READ ONLY.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.query.models import InvoiceSortField, InvoiceStatus, SortOrder
from core.hermes.tools.base import Tool, ToolDefinition, ToolResult, tool_registry
from core.integrations.dolibarr.client import DolibarrException
from core.integrations.dolibarr.mappers import dolibarr_to_customer_invoice

from .common import (
    ALLOWED_INVOICE_SORT_FIELDS,
    ALLOWED_INVOICE_SORT_ORDERS,
    build_sqlfilters,
    date_to_timestamp,
    escape_sql_like,
    map_invoice_status_to_dolibarr,
)

# =========================================================================
# CUSTOMER INVOICE PARAMETERS
# =========================================================================


@dataclass(frozen=True, slots=True)
class ListCustomerInvoicesParams:
    """Parámetros para list_customer_invoices."""

    limit: int = 20
    page: int = 1
    status: InvoiceStatus | None = None
    thirdparty_id: int | None = None
    thirdparty_name: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    sort_field: InvoiceSortField = InvoiceSortField.DATE
    sort_order: SortOrder = SortOrder.DESC

    def __post_init__(self) -> None:
        if self.limit < 1 or self.limit > 100:
            raise ValueError("El parámetro 'limit' debe estar entre 1 y 100")
        if self.page < 1:
            raise ValueError("El parámetro 'page' debe ser >= 1")
        if self.sort_order not in ALLOWED_INVOICE_SORT_ORDERS:
            raise ValueError(f"sort_order debe ser ASC o DESC, recibido: {self.sort_order}")
        if self.sort_field.value not in ALLOWED_INVOICE_SORT_FIELDS:
            raise ValueError(f"sort_field no permitido: {self.sort_field.value}")

    def to_dolibarr_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": self.limit,
            "page": self.page,
            "sortfield": self.sort_field.value,
            "sortorder": self.sort_order.value,
        }
        if self.status is not None:
            params["status"] = map_invoice_status_to_dolibarr(self.status.value)
        if self.thirdparty_id is not None:
            params["thirdparty_ids"] = str(self.thirdparty_id)
        if self.date_from is not None:
            params["date_from"] = date_to_timestamp(self.date_from)
        if self.date_to is not None:
            params["date_to"] = date_to_timestamp(self.date_to, end_of_day=True)
        if self.due_from is not None:
            params["date_lim_reglement_from"] = date_to_timestamp(self.due_from)
        if self.due_to is not None:
            params["date_lim_reglement_to"] = date_to_timestamp(self.due_to, end_of_day=True)

        # Build sqlfilters for thirdparty_name search
        if self.thirdparty_name:
            escaped = escape_sql_like(self.thirdparty_name)
            sqlfilters = f"t.soc_name:like:'%{escaped}%'"
            params["sqlfilters"] = sqlfilters
        # No need for t.client:=1 filter - /invoices endpoint already returns only customer invoices

        return params


@dataclass(frozen=True, slots=True)
class SearchCustomerInvoicesParams:
    """Parámetros para search_customer_invoices."""

    query: str
    limit: int = 20
    page: int = 1
    status: InvoiceStatus | None = None
    thirdparty_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    sort_field: InvoiceSortField = InvoiceSortField.DATE
    sort_order: SortOrder = SortOrder.DESC

    def __post_init__(self) -> None:
        if not self.query or not self.query.strip():
            raise ValueError("El parámetro 'query' no puede estar vacío")
        if len(self.query) > 200:
            raise ValueError("El parámetro 'query' es demasiado largo (máx 200 caracteres)")
        if self.limit < 1 or self.limit > 100:
            raise ValueError("El parámetro 'limit' debe estar entre 1 y 100")
        if self.page < 1:
            raise ValueError("El parámetro 'page' debe ser >= 1")
        if self.sort_order not in ALLOWED_INVOICE_SORT_ORDERS:
            raise ValueError(f"sort_order debe ser ASC o DESC, recibido: {self.sort_order}")
        if self.sort_field.value not in ALLOWED_INVOICE_SORT_FIELDS:
            raise ValueError(f"sort_field no permitido: {self.sort_field.value}")

    def to_dolibarr_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": self.limit,
            "page": self.page,
            "sortfield": self.sort_field.value,
            "sortorder": self.sort_order.value,
        }

        # Build sqlfilters for search
        sqlfilters_parts: list[str] = []

        # Search query - search across ref, thirdparty name, total_ttc
        escaped = escape_sql_like(self.query)
        search_conditions = [
            f"t.ref:like:'%{escaped}%'",
            f"t.soc_name:like:'%{escaped}%'",
            f"t.total_ttc:like:'%{escaped}%'",
        ]
        sqlfilters_parts.append(f"({' OR '.join(search_conditions)})")

        # No need for t.client:=1 filter - /invoices endpoint already returns only customer invoices

        if self.status is not None:
            sqlfilters_parts.append(f"t.status:={map_invoice_status_to_dolibarr(self.status.value)}")
        if self.thirdparty_id is not None:
            sqlfilters_parts.append(f"t.fk_soc:={self.thirdparty_id}")
        if self.date_from is not None:
            sqlfilters_parts.append(f"t.date:>={date_to_timestamp(self.date_from)}")
        if self.date_to is not None:
            sqlfilters_parts.append(f"t.date:<={date_to_timestamp(self.date_to, end_of_day=True)}")
        if self.due_from is not None:
            sqlfilters_parts.append(f"t.date_lim_reglement:>={date_to_timestamp(self.due_from)}")
        if self.due_to is not None:
            sqlfilters_parts.append(f"t.date_lim_reglement:<={date_to_timestamp(self.due_to, end_of_day=True)}")

        params["sqlfilters"] = build_sqlfilters(sqlfilters_parts)
        return params


@dataclass(frozen=True, slots=True)
class GetCustomerInvoiceParams:
    """Parámetros para get_customer_invoice."""

    invoice_id: int

    def __post_init__(self) -> None:
        if self.invoice_id <= 0:
            raise ValueError("El parámetro 'invoice_id' debe ser > 0")


@dataclass(frozen=True, slots=True)
class CountCustomerInvoicesParams:
    """Parámetros para count_customer_invoices."""

    status: InvoiceStatus | None = None
    thirdparty_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None

    def __post_init__(self) -> None:
        pass  # Validations in tool execute

    def to_dolibarr_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": 1, "pagination_data": True}
        sqlfilters_parts: list[str] = ["t.client:=1"]

        if self.status is not None:
            sqlfilters_parts.append(f"t.status:={map_invoice_status_to_dolibarr(self.status.value)}")
        if self.thirdparty_id is not None:
            sqlfilters_parts.append(f"t.fk_soc:={self.thirdparty_id}")
        if self.date_from is not None:
            sqlfilters_parts.append(f"t.date:>={date_to_timestamp(self.date_from)}")
        if self.date_to is not None:
            sqlfilters_parts.append(f"t.date:<={date_to_timestamp(self.date_to, end_of_day=True)}")
        if self.due_from is not None:
            sqlfilters_parts.append(f"t.date_lim_reglement:>={date_to_timestamp(self.due_from)}")
        if self.due_to is not None:
            sqlfilters_parts.append(f"t.date_lim_reglement:<={date_to_timestamp(self.due_to, end_of_day=True)}")

        params["sqlfilters"] = build_sqlfilters(sqlfilters_parts)
        return params


# =========================================================================
# CUSTOMER INVOICE TOOLS
# =========================================================================


class ListCustomerInvoicesTool(Tool):
    """Tool para listar facturas de cliente de Dolibarr. Permiso: customer_invoice.read"""

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="list_customer_invoices",
            description="Listar facturas de cliente de Dolibarr con paginación y filtros",
            parameters_schema={
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "status": {"type": "string", "enum": ["draft", "validated", "paid", "cancelled"]},
                    "thirdparty_id": {"type": "integer", "minimum": 1},
                    "thirdparty_name": {"type": "string", "maxLength": 200},
                    "date_from": {"type": "string", "format": "date"},
                    "date_to": {"type": "string", "format": "date"},
                    "due_from": {"type": "string", "format": "date"},
                    "due_to": {"type": "string", "format": "date"},
                    "sort_field": {"type": "string", "enum": sorted(ALLOWED_INVOICE_SORT_FIELDS), "default": "date"},
                    "sort_order": {"type": "string", "enum": sorted(ALLOWED_INVOICE_SORT_ORDERS), "default": "DESC"},
                },
                "additionalProperties": False,
            },
            required_permissions=frozenset(["customer_invoice.read"]),
            is_core=True,
        )
        super().__init__(definition)

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        **params: Any,
    ) -> ToolResult:
        try:
            list_params = ListCustomerInvoicesParams(**params)
        except Exception as e:
            return ToolResult.error(error_code="INVALID_PARAMS", error_message=f"Parámetros inválidos: {e}")

        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                raw_invoices = await client.list_invoices(**list_params.to_dolibarr_params())

                invoices = [dolibarr_to_customer_invoice(inv) for inv in raw_invoices]

                return ToolResult.ok(
                    data={
                        "invoices": invoices,
                        "count": len(invoices),
                        "limit": list_params.limit,
                        "offset": list_params.offset,
                        "has_more": len(invoices) == list_params.limit,
                    },
                    metadata={
                        "instance_id": company_context.instance_id,
                        "dolibarr_user_id": user_context.dolibarr_user_id,
                    },
                )

        except DolibarrException as e:
            return ToolResult.error(
                error_code="DOLIBARR_ERROR",
                error_message="No he podido consultar las facturas en este momento",
                metadata={"endpoint": e.endpoint, "status_code": e.status_code},
            )
        except Exception:
            return ToolResult.error(error_code="INTERNAL_ERROR", error_message="Error interno procesando la solicitud")


class SearchCustomerInvoicesTool(Tool):
    """Tool para buscar facturas de cliente de Dolibarr. Permiso: customer_invoice.read"""

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="search_customer_invoices",
            description="Buscar facturas de cliente por referencia, nombre de cliente, importe o fecha",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "description": "Término de búsqueda (referencia, cliente, importe)",
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "offset": {"type": "integer", "minimum": 0, "default": 0},
                    "status": {"type": "string", "enum": ["draft", "validated", "paid", "cancelled"]},
                    "thirdparty_id": {"type": "integer", "minimum": 1},
                    "date_from": {"type": "string", "format": "date"},
                    "date_to": {"type": "string", "format": "date"},
                    "due_from": {"type": "string", "format": "date"},
                    "due_to": {"type": "string", "format": "date"},
                    "sort_field": {"type": "string", "enum": sorted(ALLOWED_INVOICE_SORT_FIELDS), "default": "date"},
                    "sort_order": {"type": "string", "enum": sorted(ALLOWED_INVOICE_SORT_ORDERS), "default": "DESC"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            required_permissions=frozenset(["customer_invoice.read"]),
            is_core=True,
        )
        super().__init__(definition)

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        **params: Any,
    ) -> ToolResult:
        try:
            search_params = SearchCustomerInvoicesParams(**params)
        except Exception as e:
            return ToolResult.error(error_code="INVALID_PARAMS", error_message=f"Parámetros inválidos: {e}")

        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                raw_invoices = await client.list_invoices(**search_params.to_dolibarr_params())

                invoices = [dolibarr_to_customer_invoice(inv) for inv in raw_invoices]

                return ToolResult.ok(
                    data={
                        "invoices": invoices,
                        "count": len(invoices),
                        "limit": search_params.limit,
                        "offset": search_params.offset,
                        "has_more": len(invoices) == search_params.limit,
                    },
                    metadata={
                        "instance_id": company_context.instance_id,
                        "dolibarr_user_id": user_context.dolibarr_user_id,
                    },
                )

        except DolibarrException as e:
            return ToolResult.error(
                error_code="DOLIBARR_ERROR",
                error_message="No he podido consultar las facturas en este momento",
                metadata={"endpoint": e.endpoint, "status_code": e.status_code},
            )
        except Exception:
            return ToolResult.error(error_code="INTERNAL_ERROR", error_message="Error interno procesando la solicitud")


class GetCustomerInvoiceTool(Tool):
    """Tool para obtener detalle de una factura de cliente por ID. Permiso: customer_invoice.read"""

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="get_customer_invoice",
            description="Obtener detalle completo de una factura de cliente por su ID",
            parameters_schema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "minimum": 1, "description": "ID de la factura en Dolibarr"}
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
            required_permissions=frozenset(["customer_invoice.read"]),
            is_core=True,
        )
        super().__init__(definition)

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        **params: Any,
    ) -> ToolResult:
        try:
            get_params = GetCustomerInvoiceParams(**params)
        except Exception as e:
            return ToolResult.error(error_code="INVALID_PARAMS", error_message=f"Parámetros inválidos: {e}")

        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                raw_invoice = await client.get_invoice(get_params.invoice_id)

                invoice = dolibarr_to_customer_invoice(raw_invoice)

                return ToolResult.ok(
                    data={"invoice": invoice},
                    metadata={
                        "instance_id": company_context.instance_id,
                        "dolibarr_user_id": user_context.dolibarr_user_id,
                    },
                )

        except DolibarrException as e:
            if e.status_code == 404:
                return ToolResult.error(error_code="NOT_FOUND", error_message="Factura no encontrada")
            return ToolResult.error(
                error_code="DOLIBARR_ERROR",
                error_message="No he podido consultar Dolibarr en este momento",
                metadata={"endpoint": e.endpoint, "status_code": e.status_code},
            )
        except Exception:
            return ToolResult.error(error_code="INTERNAL_ERROR", error_message="Error interno procesando la solicitud")


class CountCustomerInvoicesTool(Tool):
    """Tool para contar facturas de cliente de Dolibarr. Permiso: customer_invoice.read"""

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="count_customer_invoices",
            description="Contar total de facturas de cliente con filtros opcionales",
            parameters_schema={
                "type": "object",
                "properties": {
                    "status": {"type": "string", "enum": ["draft", "validated", "paid", "cancelled"]},
                    "thirdparty_id": {"type": "integer", "minimum": 1},
                    "date_from": {"type": "string", "format": "date"},
                    "date_to": {"type": "string", "format": "date"},
                    "due_from": {"type": "string", "format": "date"},
                    "due_to": {"type": "string", "format": "date"},
                },
                "additionalProperties": False,
            },
            required_permissions=frozenset(["customer_invoice.read"]),
            is_core=True,
        )
        super().__init__(definition)

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        **params: Any,
    ) -> ToolResult:
        try:
            count_params = CountCustomerInvoicesParams(**params)
        except Exception as e:
            return ToolResult.error(error_code="INVALID_PARAMS", error_message=f"Parámetros inválidos: {e}")

        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # Use pagination_data to get total count efficiently
                result = await client.list_invoices(**count_params.to_dolibarr_params())

                if isinstance(result, dict) and "pagination" in result:
                    total_count = result["pagination"].get("total", 0)
                else:
                    # Fallback if pagination_data not supported
                    raw_invoices = result if isinstance(result, list) else result.get("data", [])
                    total_count = len(raw_invoices)
                    if total_count == 1:  # limit=1 was used
                        total_count = 0
                        page = 1
                        page_size = 100
                        while True:
                            dolibarr_params = count_params.to_dolibarr_params()
                            dolibarr_params["limit"] = page_size
                            dolibarr_params["page"] = page
                            invoices = await client.list_invoices(**dolibarr_params)
                            if not invoices:
                                break
                            total_count += len(invoices)
                            if len(invoices) < page_size:
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
                metadata={"endpoint": e.endpoint, "status_code": e.status_code},
            )
        except Exception:
            return ToolResult.error(error_code="INTERNAL_ERROR", error_message="Error interno procesando la solicitud")


def register_core_customer_invoice_tools() -> None:
    """Registrar tools de facturas de cliente en el registry global."""
    tool_registry.register_core_tool(ListCustomerInvoicesTool())
    tool_registry.register_core_tool(SearchCustomerInvoicesTool())
    tool_registry.register_core_tool(GetCustomerInvoiceTool())
    tool_registry.register_core_tool(CountCustomerInvoicesTool())
