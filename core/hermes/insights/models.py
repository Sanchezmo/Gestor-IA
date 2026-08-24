"""
Modelos Pydantic para resultados de Business Insights V1.

Estos modelos definen la estructura de los resultados de consultas financieras.
Se usan para devolver resultados estructurados y tipados desde los servicios de insights.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# =========================================================================
# ENUMS
# =========================================================================


class FinancialPeriod(StrEnum):
    """Períodos financieros predefinidos."""

    TODAY = "today"
    CURRENT_WEEK = "current_week"
    CURRENT_MONTH = "current_month"
    PREVIOUS_MONTH = "previous_month"
    CURRENT_QUARTER = "current_quarter"
    CURRENT_YEAR = "current_year"
    PREVIOUS_YEAR = "previous_year"
    CUSTOM = "custom"


class InsightAction(StrEnum):
    """Acciones de Business Insights."""

    # Customer financial insights
    CUSTOMER_INVOICE_SUMMARY = "customer_invoice_summary"
    CUSTOMER_OUTSTANDING_SUMMARY = "customer_outstanding_summary"
    CUSTOMER_OUTSTANDING_BY_THIRDPARTY = "customer_outstanding_by_thirdparty"
    CUSTOMER_INVOICE_SUMMARY_BY_THIRDPARTY = "customer_invoice_summary_by_thirdparty"

    # Supplier financial insights
    SUPPLIER_INVOICE_SUMMARY = "supplier_invoice_summary"
    SUPPLIER_OUTSTANDING_SUMMARY = "supplier_outstanding_summary"
    SUPPLIER_OUTSTANDING_BY_THIRDPARTY = "supplier_outstanding_by_thirdparty"
    SUPPLIER_INVOICE_SUMMARY_BY_THIRDPARTY = "supplier_invoice_summary_by_thirdparty"


# =========================================================================
# ARGUMENT MODELS
# =========================================================================


class FinancialPeriodArgs(BaseModel):
    """Argumentos base para consultas con período financiero."""

    model_config = ConfigDict(extra="forbid")

    period: FinancialPeriod = FinancialPeriod.CURRENT_MONTH
    date_from: date | None = None
    date_to: date | None = None

    def __post_init__(self) -> None:
        if self.period == FinancialPeriod.CUSTOM:
            if not self.date_from or not self.date_to:
                raise ValueError("CUSTOM period requiere date_from y date_to")


class CustomerInvoiceSummaryArgs(FinancialPeriodArgs):
    """Argumentos para customer_invoice_summary."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None,
        description="Filtrar por estado: draft, validated, paid, cancelled"
    )
    thirdparty_id: int | None = Field(default=None, gt=0)


class CustomerOutstandingSummaryArgs(FinancialPeriodArgs):
    """Argumentos para customer_outstanding_summary."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None,
        description="Filtrar por estado: draft, validated, paid, cancelled"
    )


class CustomerOutstandingByThirdpartyArgs(FinancialPeriodArgs):
    """Argumentos para customer_outstanding_by_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None,
        description="Filtrar por estado: draft, validated, paid, cancelled"
    )
    limit: int = Field(default=10, ge=1, le=100)


class CustomerInvoiceSummaryByThirdpartyArgs(FinancialPeriodArgs):
    """Argumentos para customer_invoice_summary_by_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    thirdparty_id: int = Field(..., gt=0)
    status: str | None = Field(
        default=None,
        description="Filtrar por estado: draft, validated, paid, cancelled"
    )


class SupplierInvoiceSummaryArgs(FinancialPeriodArgs):
    """Argumentos para supplier_invoice_summary."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None,
        description="Filtrar por estado: draft, validated, paid, cancelled"
    )
    thirdparty_id: int | None = Field(default=None, gt=0)


class SupplierOutstandingSummaryArgs(FinancialPeriodArgs):
    """Argumentos para supplier_outstanding_summary."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None,
        description="Filtrar por estado: draft, validated, paid, cancelled"
    )


class SupplierOutstandingByThirdpartyArgs(FinancialPeriodArgs):
    """Argumentos para supplier_outstanding_by_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    status: str | None = Field(
        default=None,
        description="Filtrar por estado: draft, validated, paid, cancelled"
    )
    limit: int = Field(default=10, ge=1, le=100)


class SupplierInvoiceSummaryByThirdpartyArgs(FinancialPeriodArgs):
    """Argumentos para supplier_invoice_summary_by_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    thirdparty_id: int = Field(..., gt=0)
    status: str | None = Field(
        default=None,
        description="Filtrar por estado: draft, validated, paid, cancelled"
    )


# =========================================================================
# RESULT MODELS
# =========================================================================


class FinancialSummaryResult(BaseModel):
    """Resultado base para resúmenes financieros."""

    model_config = ConfigDict(extra="forbid")

    period: dict[str, date | str] = Field(
        default_factory=dict,
        description="Período consultado: from, to"
    )
    invoice_count: int = Field(default=0, description="Número de facturas")
    subtotal: Decimal = Field(default=Decimal("0"), description="Base imponible total")
    tax: Decimal = Field(default=Decimal("0"), description="Impuestos totales")
    total: Decimal = Field(default=Decimal("0"), description="Total con impuestos")
    paid: Decimal = Field(default=Decimal("0"), description="Total pagado")
    outstanding: Decimal = Field(default=Decimal("0"), description="Total pendiente")
    currency: str = Field(default="EUR", description="Moneda")


class CustomerInvoiceSummaryResult(FinancialSummaryResult):
    """Resultado para customer_invoice_summary."""

    model_config = ConfigDict(extra="forbid")


class CustomerOutstandingSummaryResult(FinancialSummaryResult):
    """Resultado para customer_outstanding_summary."""

    model_config = ConfigDict(extra="forbid")


class SupplierInvoiceSummaryResult(FinancialSummaryResult):
    """Resultado para supplier_invoice_summary."""

    model_config = ConfigDict(extra="forbid")


class SupplierOutstandingSummaryResult(FinancialSummaryResult):
    """Resultado para supplier_outstanding_summary."""

    model_config = ConfigDict(extra="forbid")


class ThirdPartyOutstandingItem(BaseModel):
    """Item individual en ranking de terceros con importe pendiente."""

    model_config = ConfigDict(extra="forbid")

    thirdparty_id: int = Field(..., description="ID del tercero en Dolibarr")
    name: str = Field(..., description="Nombre del tercero")
    invoice_count: int = Field(default=0, description="Número de facturas")
    outstanding: Decimal = Field(default=Decimal("0"), description="Importe pendiente")


class CustomerOutstandingByThirdpartyResult(BaseModel):
    """Resultado para customer_outstanding_by_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    items: list[ThirdPartyOutstandingItem] = Field(default_factory=list)
    total_outstanding: Decimal = Field(default=Decimal("0"))
    currency: str = Field(default="EUR")


class CustomerInvoiceSummaryByThirdpartyResult(BaseModel):
    """Resultado para customer_invoice_summary_by_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    thirdparty_id: int
    thirdparty_name: str
    invoice_count: int
    subtotal: Decimal = Field(default=Decimal("0"))
    tax: Decimal = Field(default=Decimal("0"))
    total: Decimal = Field(default=Decimal("0"))
    paid: Decimal = Field(default=Decimal("0"))
    outstanding: Decimal = Field(default=Decimal("0"))
    currency: str = Field(default="EUR")


class SupplierOutstandingByThirdpartyResult(BaseModel):
    """Resultado para supplier_outstanding_by_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    items: list[ThirdPartyOutstandingItem] = Field(default_factory=list)
    total_outstanding: Decimal = Field(default=Decimal("0"))
    currency: str = Field(default="EUR")


class SupplierInvoiceSummaryByThirdpartyResult(BaseModel):
    """Resultado para supplier_invoice_summary_by_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    thirdparty_id: int
    thirdparty_name: str
    invoice_count: int
    subtotal: Decimal = Field(default=Decimal("0"))
    tax: Decimal = Field(default=Decimal("0"))
    total: Decimal = Field(default=Decimal("0"))
    paid: Decimal = Field(default=Decimal("0"))
    outstanding: Decimal = Field(default=Decimal("0"))
    currency: str = Field(default="EUR")


# =========================================================================
# UNION TYPES
# =========================================================================

InsightArgs = (
    CustomerInvoiceSummaryArgs
    | CustomerOutstandingSummaryArgs
    | CustomerOutstandingByThirdpartyArgs
    | CustomerInvoiceSummaryByThirdpartyArgs
    | SupplierInvoiceSummaryArgs
    | SupplierOutstandingSummaryArgs
    | SupplierOutstandingByThirdpartyArgs
    | SupplierInvoiceSummaryByThirdpartyArgs
)

InsightResult = (
    CustomerInvoiceSummaryResult
    | CustomerOutstandingSummaryResult
    | CustomerOutstandingByThirdpartyResult
    | CustomerInvoiceSummaryByThirdpartyResult
    | SupplierInvoiceSummaryResult
    | SupplierOutstandingSummaryResult
    | SupplierOutstandingByThirdpartyResult
    | SupplierInvoiceSummaryByThirdpartyResult
)


# =========================================================================
# TOOL SCHEMAS FOR PROMPT
# =========================================================================


class InsightToolSchema(BaseModel):
    """Esquema reducido de una Insight Tool para el prompt del LLM."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    arguments_schema: dict[str, Any]


INSIGHT_TOOLS_CATALOG: list[InsightToolSchema] = [
    InsightToolSchema(
        name="customer_invoice_summary",
        description="Resumen de facturas de cliente en un período (totales, pagado, pendiente)",
        arguments_schema=CustomerInvoiceSummaryArgs.model_json_schema(),
    ),
    InsightToolSchema(
        name="customer_outstanding_summary",
        description="Resumen de lo que nos deben los clientes (pendiente de cobro) en un período",
        arguments_schema=CustomerOutstandingSummaryArgs.model_json_schema(),
    ),
    InsightToolSchema(
        name="customer_outstanding_by_thirdparty",
        description="Ranking de clientes que más nos deben (pendiente de cobro) en un período",
        arguments_schema=CustomerOutstandingByThirdpartyArgs.model_json_schema(),
    ),
    InsightToolSchema(
        name="customer_invoice_summary_by_thirdparty",
        description="Resumen de facturas de un cliente específico en un período",
        arguments_schema=CustomerInvoiceSummaryByThirdpartyArgs.model_json_schema(),
    ),
    InsightToolSchema(
        name="supplier_invoice_summary",
        description="Resumen de facturas de proveedor en un período (totales, pagado, pendiente)",
        arguments_schema=SupplierInvoiceSummaryArgs.model_json_schema(),
    ),
    InsightToolSchema(
        name="supplier_outstanding_summary",
        description="Resumen de lo que debemos a proveedores (pendiente de pago) en un período",
        arguments_schema=SupplierOutstandingSummaryArgs.model_json_schema(),
    ),
    InsightToolSchema(
        name="supplier_outstanding_by_thirdparty",
        description="Ranking de proveedores a los que más debemos (pendiente de pago) en un período",
        arguments_schema=SupplierOutstandingByThirdpartyArgs.model_json_schema(),
    ),
    InsightToolSchema(
        name="supplier_invoice_summary_by_thirdparty",
        description="Resumen de facturas de un proveedor específico en un período",
        arguments_schema=SupplierInvoiceSummaryByThirdpartyArgs.model_json_schema(),
    ),
]


def get_insight_tools_catalog_for_prompt() -> str:
    """Generar representación del catálogo de insights para el system prompt."""
    lines = ["Insight Tools disponibles (solo estas):"]
    for tool in INSIGHT_TOOLS_CATALOG:
        lines.append(f"- {tool.name}: {tool.description}")
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


def _format_money(amount: Decimal | float | int | str) -> str:
    """Formatear importe monetario para Telegram (formato ES)."""
    d = Decimal(str(amount))
    return f"{d:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def format_financial_summary_for_telegram(result: FinancialSummaryResult, period_label: str, party_label: str) -> str:
    """Formatear resumen financiero para Telegram."""
    lines = [
        f"📊 *{party_label} - {period_label}*",
        f"Facturas: {result.invoice_count}",
        f"Base imponible: {result.subtotal}",
        f"Impuestos: {result.tax}",
        f"Total: {result.total}",
        f"Pagado: {result.paid}",
        f"Pendiente: {result.outstanding}",
    ]
    return "\n".join(lines)


def format_customer_invoice_summary_for_telegram(result: CustomerInvoiceSummaryResult) -> str:
    """Formatear customer_invoice_summary para Telegram."""
    return format_financial_summary_for_telegram(result, "Mes actual", "Facturas de cliente")


def format_customer_outstanding_summary_for_telegram(result: CustomerOutstandingSummaryResult) -> str:
    """Formatear customer_outstanding_summary para Telegram."""
    return format_financial_summary_for_telegram(result, "Mes actual", "Pendiente de cobro (clientes)")


def format_supplier_invoice_summary_for_telegram(result: SupplierInvoiceSummaryResult) -> str:
    """Formatear supplier_invoice_summary para Telegram."""
    return format_financial_summary_for_telegram(result, "Mes actual", "Facturas de proveedor")


def format_supplier_outstanding_summary_for_telegram(result: SupplierOutstandingSummaryResult) -> str:
    """Formatear supplier_outstanding_summary para Telegram."""
    return format_financial_summary_for_telegram(result, "Mes actual", "Pendiente de pago (proveedores)")


def format_outstanding_by_thirdparty_for_telegram(
    result: CustomerOutstandingByThirdpartyResult | SupplierOutstandingByThirdpartyResult,
    party_label: str
) -> str:
    """Formatear ranking de terceros por importe pendiente para Telegram."""
    if not result.items:
        return f"No hay {party_label.lower()} con importe pendiente."

    lines = [f"🏆 *Top {party_label} por importe pendiente*"]
    for i, item in enumerate(result.items, 1):
        lines.append(
            f"{i}. {item.name} (ID: {item.thirdparty_id})\n"
            f"   Facturas: {item.invoice_count} | Pendiente: {item.outstanding}"
        )
    lines.append(f"\nTotal pendiente: {result.total_outstanding}")
    return "\n".join(lines)


def format_customer_outstanding_by_thirdparty_for_telegram(result: CustomerOutstandingByThirdpartyResult) -> str:
    """Formatear customer_outstanding_by_thirdparty para Telegram."""
    return format_outstanding_by_thirdparty_for_telegram(result, "Clientes")


def format_supplier_outstanding_by_thirdparty_for_telegram(result: SupplierOutstandingByThirdpartyResult) -> str:
    """Formatear supplier_outstanding_by_thirdparty para Telegram."""
    return format_outstanding_by_thirdparty_for_telegram(result, "Proveedores")


def format_customer_invoice_summary_by_thirdparty_for_telegram(result: CustomerInvoiceSummaryByThirdpartyResult) -> str:
    """Formatear customer_invoice_summary_by_thirdparty para Telegram."""
    lines = [
        f"📊 *Facturas de {result.thirdparty_name} (ID: {result.thirdparty_id})*",
        f"Facturas: {result.invoice_count}",
        f"Base imponible: {_format_money(result.subtotal)}",
        f"Impuestos: {_format_money(result.tax)}",
        f"Total: {_format_money(result.total)}",
        f"Pagado: {_format_money(result.paid)}",
        f"Pendiente: {_format_money(result.outstanding)}",
    ]
    return "\n".join(lines)


def format_supplier_invoice_summary_by_thirdparty_for_telegram(result: SupplierInvoiceSummaryByThirdpartyResult) -> str:
    """Formatear supplier_invoice_summary_by_thirdparty para Telegram."""
    lines = [
        f"📊 *Facturas de proveedor {result.thirdparty_name} (ID: {result.thirdparty_id})*",
        f"Facturas: {result.invoice_count}",
        f"Base imponible: {_format_money(result.subtotal)}",
        f"Impuestos: {_format_money(result.tax)}",
        f"Total: {_format_money(result.total)}",
        f"Pagado: {_format_money(result.paid)}",
        f"Pendiente: {_format_money(result.outstanding)}",
    ]
    return "\n".join(lines)