"""
Tools para facturas de proveedor en Dolibarr - READ ONLY.
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
from core.integrations.dolibarr.mappers import dolibarr_to_supplier_invoice_summary

from .common import (
    ALLOWED_INVOICE_SORT_FIELDS,
    ALLOWED_INVOICE_SORT_ORDERS,
    build_sqlfilters,
    date_to_timestamp,
    escape_sql_like,
    map_invoice_status_to_dolibarr,
)

# =========================================================================
# SUPPLIER INVOICE PARAMETERS
# =========================================================================


@dataclass(frozen=True, slots=True)
class ListSupplierInvoicesParams:
    """Parámetros para list_supplier_invoices."""

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
            sqlfilters = f"t.soc_name:like:'%{escaped}%' AND t.fournisseur:=1"
            params["sqlfilters"] = sqlfilters
        else:
            # Always filter for supplier invoices (fournisseur=1)
            params["sqlfilters"] = "t.fournisseur:=1"

        return params


@dataclass(frozen=True, slots=True)
class SearchSupplierInvoicesParams:
    """Parámetros para search_supplier_invoices."""

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

        # Always filter for supplier invoices
        sqlfilters_parts.append("t.fournisseur:=1")

        if self.status is not None:
            sqlfilters_parts.append(f"t.status:={map_invoice_status_to_dolibarr(self.status.value)}")
        if self.thirdparty_id is not None:
            sqlfilters_parts.append(f"t.socid:={self.thirdparty_id}")
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
class GetSupplierInvoiceParams:
    """Parámetros para get_supplier_invoice."""

    invoice_id: int

    def __post_init__(self) -> None:
        if self.invoice_id <= 0:
            raise ValueError("El parámetro 'invoice_id' debe ser > 0")


@dataclass(frozen=True, slots=True)
class CountSupplierInvoicesParams:
    """Parámetros para count_supplier_invoices."""

    status: InvoiceStatus | None = None
    thirdparty_id: int | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None

    def __post_init__(self) -> None:
        pass

    def to_dolibarr_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": 1, "pagination_data": True}
        sqlfilters_parts: list[str] = ["t.fournisseur:=1"]

        if self.status is not None:
            sqlfilters_parts.append(f"t.status:={map_invoice_status_to_dolibarr(self.status.value)}")
        if self.thirdparty_id is not None:
            sqlfilters_parts.append(f"t.socid:={self.thirdparty_id}")
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
# SUPPLIER INVOICE TOOLS
# =========================================================================


class ListSupplierInvoicesTool(Tool):
    """Tool para listar facturas de proveedor de Dolibarr. Permiso: supplier_invoice.read"""

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="list_supplier_invoices",
            description="Listar facturas de proveedor de Dolibarr con paginación y filtros",
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
            required_permissions=frozenset(["supplier_invoice.read"]),
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
            list_params = ListSupplierInvoicesParams(**params)
        except Exception as e:
            return ToolResult.error(error_code="INVALID_PARAMS", error_message=f"Parámetros inválidos: {e}")

        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                raw_invoices = await client.list_supplier_invoices(**list_params.to_dolibarr_params())

                invoices = [dolibarr_to_supplier_invoice_summary(inv) for inv in raw_invoices]

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


class SearchSupplierInvoicesTool(Tool):
    """Tool para buscar facturas de proveedor de Dolibarr. Permiso: supplier_invoice.read"""

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="search_supplier_invoices",
            description="Buscar facturas de proveedor por referencia, nombre de proveedor, importe o fecha",
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 200,
                        "description": "Término de búsqueda (referencia, proveedor, importe)",
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
            required_permissions=frozenset(["supplier_invoice.read"]),
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
            search_params = SearchSupplierInvoicesParams(**params)
        except Exception as e:
            return ToolResult.error(error_code="INVALID_PARAMS", error_message=f"Parámetros inválidos: {e}")

        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                raw_invoices = await client.list_supplier_invoices(**search_params.to_dolibarr_params())

                invoices = [dolibarr_to_supplier_invoice_summary(inv) for inv in raw_invoices]

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


class GetSupplierInvoiceTool(Tool):
    """Tool para obtener detalle de una factura de proveedor por ID. Permiso: supplier_invoice.read"""

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="get_supplier_invoice",
            description="Obtener detalle completo de una factura de proveedor por su ID",
            parameters_schema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "integer", "minimum": 1, "description": "ID de la factura en Dolibarr"}
                },
                "required": ["invoice_id"],
                "additionalProperties": False,
            },
            required_permissions=frozenset(["supplier_invoice.read"]),
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
            get_params = GetSupplierInvoiceParams(**params)
        except Exception as e:
            return ToolResult.error(error_code="INVALID_PARAMS", error_message=f"Parámetros inválidos: {e}")

        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                raw_invoice = await client.get_supplier_invoice(get_params.invoice_id)

                invoice = dolibarr_to_supplier_invoice_summary(raw_invoice)

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


class CountSupplierInvoicesTool(Tool):
    """Tool para contar facturas de proveedor de Dolibarr. Permiso: supplier_invoice.read"""

    def __init__(self) -> None:
        definition = ToolDefinition(
            name="count_supplier_invoices",
            description="Contar total de facturas de proveedor con filtros opcionales",
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
            required_permissions=frozenset(["supplier_invoice.read"]),
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
            count_params = CountSupplierInvoicesParams(**params)
        except Exception as e:
            return ToolResult.error(error_code="INVALID_PARAMS", error_message=f"Parámetros inválidos: {e}")

        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # Use pagination_data to get total count efficiently
                result = await client.list_supplier_invoices(**count_params.to_dolibarr_params())

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
                            invoices = await client.list_supplier_invoices(**dolibarr_params)
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


def register_core_supplier_invoice_tools() -> None:
    """Registrar tools de facturas de proveedor en el registry global."""
    tool_registry.register_core_tool(ListSupplierInvoicesTool())
    tool_registry.register_core_tool(SearchSupplierInvoicesTool())
    tool_registry.register_core_tool(GetSupplierInvoiceTool())
    tool_registry.register_core_tool(CountSupplierInvoicesTool())
