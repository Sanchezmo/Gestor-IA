"""
Business Insights V1 - Financial Read-Only Insights.

Paquete que expone los servicios de insights financieros read-only
para facturas de cliente y proveedor.
"""

from __future__ import annotations

from typing import Any

from core.hermes.insights.customer_finance import CustomerFinanceInsightService
from core.hermes.insights.models import (
    # Catalog
    INSIGHT_TOOLS_CATALOG,
    # Argument models
    CustomerInvoiceSummaryArgs,
    CustomerInvoiceSummaryByThirdpartyArgs,
    CustomerInvoiceSummaryByThirdpartyResult,
    # Result models
    CustomerInvoiceSummaryResult,
    CustomerOutstandingByThirdpartyArgs,
    CustomerOutstandingByThirdpartyResult,
    CustomerOutstandingSummaryArgs,
    CustomerOutstandingSummaryResult,
    FinancialPeriod,
    FinancialSummaryResult,
    # Actions
    InsightAction,
    SupplierInvoiceSummaryArgs,
    SupplierInvoiceSummaryByThirdpartyArgs,
    SupplierInvoiceSummaryByThirdpartyResult,
    SupplierInvoiceSummaryResult,
    SupplierOutstandingByThirdpartyArgs,
    SupplierOutstandingByThirdpartyResult,
    SupplierOutstandingSummaryArgs,
    SupplierOutstandingSummaryResult,
    ThirdPartyOutstandingItem,
    # Formatters
    _format_money,
    format_customer_invoice_summary_by_thirdparty_for_telegram,
    format_customer_invoice_summary_for_telegram,
    format_customer_outstanding_by_thirdparty_for_telegram,
    format_customer_outstanding_summary_for_telegram,
    format_financial_summary_for_telegram,
    format_supplier_invoice_summary_by_thirdparty_for_telegram,
    format_supplier_invoice_summary_for_telegram,
    format_supplier_outstanding_by_thirdparty_for_telegram,
    format_supplier_outstanding_summary_for_telegram,
    get_insight_tools_catalog_for_prompt,
)
from core.hermes.insights.supplier_finance import SupplierFinanceInsightService

# Servicios
_customer_finance_service: CustomerFinanceInsightService | None = None
_supplier_finance_service: SupplierFinanceInsightService | None = None


def get_customer_finance_service() -> CustomerFinanceInsightService:
    """Obtener instancia singleton del servicio de finanzas de cliente."""
    global _customer_finance_service
    if _customer_finance_service is None:
        _customer_finance_service = CustomerFinanceInsightService()
    return _customer_finance_service


def get_supplier_finance_service() -> SupplierFinanceInsightService:
    """Obtener instancia singleton del servicio de finanzas de proveedor."""
    global _supplier_finance_service
    if _supplier_finance_service is None:
        _supplier_finance_service = SupplierFinanceInsightService()
    return _supplier_finance_service


async def execute_customer_insight(
    company_context,
    user_context,
    action: str,
    args: dict,
) -> Any:
    """Ejecutar un insight de cliente."""
    from core.hermes.tools import ToolResult
    
    service = get_customer_finance_service()
    method_map = {
        "customer_invoice_summary": service.customer_invoice_summary,
        "customer_outstanding_summary": service.customer_outstanding_summary,
        "customer_outstanding_by_thirdparty": service.customer_outstanding_by_thirdparty,
        "customer_invoice_summary_by_thirdparty": service.customer_invoice_summary_by_thirdparty,
    }
    if action not in method_map:
        raise ValueError(f"Acción de cliente no soportada: {action}")
    try:
        return await method_map[action](company_context, user_context, args)
    except RuntimeError as e:
        # Tool execution error (permission denied, Dolibarr error, etc.)
        error_msg = str(e)
        if "Permiso requerido" in error_msg:
            return ToolResult.error(
                error_code="PERMISSION_DENIED",
                error_message=error_msg,
            )
        return ToolResult.error(
            error_code="TOOL_EXECUTION_ERROR",
            error_message=error_msg,
        )


async def execute_supplier_insight(
    company_context,
    user_context,
    action: str,
    args: dict,
) -> Any:
    """Ejecutar un insight de proveedor."""
    from core.hermes.tools import ToolResult
    
    service = get_supplier_finance_service()
    method_map = {
        "supplier_invoice_summary": service.supplier_invoice_summary,
        "supplier_outstanding_summary": service.supplier_outstanding_summary,
        "supplier_outstanding_by_thirdparty": service.supplier_outstanding_by_thirdparty,
        "supplier_invoice_summary_by_thirdparty": service.supplier_invoice_summary_by_thirdparty,
    }
    if action not in method_map:
        raise ValueError(f"Acción de proveedor no soportada: {action}")
    try:
        return await method_map[action](company_context, user_context, args)
    except RuntimeError as e:
        # Tool execution error (permission denied, Dolibarr error, etc.)
        error_msg = str(e)
        if "Permiso requerido" in error_msg:
            return ToolResult.error(
                error_code="PERMISSION_DENIED",
                error_message=error_msg,
            )
        return ToolResult.error(
            error_code="TOOL_EXECUTION_ERROR",
            error_message=error_msg,
        )


__all__ = [
    # Services
    "CustomerFinanceInsightService",
    "SupplierFinanceInsightService",
    "get_customer_finance_service",
    "get_supplier_finance_service",
    "execute_customer_insight",
    "execute_supplier_insight",
    # Models
    "InsightAction",
    "FinancialPeriod",
    "CustomerInvoiceSummaryArgs",
    "CustomerOutstandingSummaryArgs",
    "CustomerOutstandingByThirdpartyArgs",
    "CustomerInvoiceSummaryByThirdpartyArgs",
    "SupplierInvoiceSummaryArgs",
    "SupplierOutstandingSummaryArgs",
    "SupplierOutstandingByThirdpartyArgs",
    "SupplierInvoiceSummaryByThirdpartyArgs",
    "CustomerInvoiceSummaryResult",
    "CustomerOutstandingSummaryResult",
    "CustomerOutstandingByThirdpartyResult",
    "CustomerInvoiceSummaryByThirdpartyResult",
    "SupplierInvoiceSummaryResult",
    "SupplierOutstandingSummaryResult",
    "SupplierOutstandingByThirdpartyResult",
    "SupplierInvoiceSummaryByThirdpartyResult",
    "FinancialSummaryResult",
    "ThirdPartyOutstandingItem",
    # Catalog
    "INSIGHT_TOOLS_CATALOG",
    "get_insight_tools_catalog_for_prompt",
    # Formatters
    "_format_money",
    "format_financial_summary_for_telegram",
    "format_customer_invoice_summary_for_telegram",
    "format_customer_outstanding_summary_for_telegram",
    "format_supplier_invoice_summary_for_telegram",
    "format_supplier_outstanding_summary_for_telegram",
    "format_customer_outstanding_by_thirdparty_for_telegram",
    "format_supplier_outstanding_by_thirdparty_for_telegram",
    "format_customer_invoice_summary_by_thirdparty_for_telegram",
    "format_supplier_invoice_summary_by_thirdparty_for_telegram",
]
