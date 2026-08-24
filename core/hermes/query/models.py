from __future__ import annotations

"""
Modelos Pydantic para Structured Output del Intent Interpreter.

Estos modelos definen el contrato estricto que Ollama debe cumplir.
No se permite extra="allow" - solo campos explícitos.
"""

from datetime import date
from enum import StrEnum
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
# ENUMS PARA FACTURAS (INVOICES)
# =========================================================================


class InvoiceAction(StrEnum):
    """Acciones soportadas para facturas."""

    # Customer invoices
    LIST_CUSTOMER_INVOICES = "list_customer_invoices"
    SEARCH_CUSTOMER_INVOICES = "search_customer_invoices"
    GET_CUSTOMER_INVOICE = "get_customer_invoice"
    COUNT_CUSTOMER_INVOICES = "count_customer_invoices"
    SUM_CUSTOMER_INVOICES = "sum_customer_invoices"

    # Supplier invoices
    LIST_SUPPLIER_INVOICES = "list_supplier_invoices"
    SEARCH_SUPPLIER_INVOICES = "search_supplier_invoices"
    GET_SUPPLIER_INVOICE = "get_supplier_invoice"
    COUNT_SUPPLIER_INVOICES = "count_supplier_invoices"
    SUM_SUPPLIER_INVOICES = "sum_supplier_invoices"


class InvoicePartyType(StrEnum):
    """Tipo de factura a filtrar."""

    CUSTOMER = "customer"
    SUPPLIER = "supplier"


class InvoiceStatus(StrEnum):
    """Estados normalizados de facturas."""

    DRAFT = "draft"
    VALIDATED = "validated"
    PAID = "paid"
    CANCELLED = "cancelled"


class InvoiceSortField(StrEnum):
    """Campos ordenables permitidos para facturas (allowlist)."""

    ROWID = "rowid"
    REF = "ref"
    DATE = "date"
    DATE_LIM_REGLEMENT = "date_lim_reglement"
    TOTAL_TTC = "total_ttc"
    THIRDPARTY_NAME = "soc_name"
    STATUS = "status"


# =========================================================================
# ENUMS PARA BUSINESS INSIGHTS
# =========================================================================


class InsightAction(StrEnum):
    """Acciones de Business Insights (Financial Read-Only)."""

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
# MODELOS DE ARGUMENTOS POR ACCIÓN - TERCEROS
# =========================================================================


class ListThirdpartiesArgs(BaseModel):
    """Argumentos para list_thirdparties."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    party_type: ThirdpartyPartyType = ThirdpartyPartyType.ALL
    sort_field: SortField = SortField.NAME
    sort_order: SortOrder = SortOrder.ASC


class SearchThirdpartiesArgs(BaseModel):
    """Argumentos para search_thirdparties."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=200)
    party_type: ThirdpartyPartyType = ThirdpartyPartyType.ALL
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    sort_field: SortField = SortField.NAME
    sort_order: SortOrder = SortOrder.ASC


class GetThirdpartyArgs(BaseModel):
    """Argumentos para get_thirdparty."""

    model_config = ConfigDict(extra="forbid")

    thirdparty_id: int = Field(..., gt=0)


class CountThirdpartiesArgs(BaseModel):
    """Argumentos para count_thirdparties."""

    model_config = ConfigDict(extra="forbid")

    party_type: ThirdpartyPartyType = ThirdpartyPartyType.ALL


# =========================================================================
# MODELOS DE ARGUMENTOS POR ACCIÓN - FACTURAS
# =========================================================================


class ListCustomerInvoicesArgs(BaseModel):
    """Argumentos para list_customer_invoices."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    status: InvoiceStatus | None = None
    thirdparty_id: int | None = Field(default=None, gt=0)
    thirdparty_name: str | None = Field(default=None, max_length=200)
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    sort_field: InvoiceSortField = InvoiceSortField.DATE
    sort_order: SortOrder = SortOrder.DESC


class SearchCustomerInvoicesArgs(BaseModel):
    """Argumentos para search_customer_invoices."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    status: InvoiceStatus | None = None
    thirdparty_id: int | None = Field(default=None, gt=0)
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    sort_field: InvoiceSortField = InvoiceSortField.DATE
    sort_order: SortOrder = SortOrder.DESC


class GetCustomerInvoiceArgs(BaseModel):
    """Argumentos para get_customer_invoice."""

    model_config = ConfigDict(extra="forbid")

    invoice_id: int = Field(..., gt=0)


class CountCustomerInvoicesArgs(BaseModel):
    """Argumentos para count_customer_invoices."""

    model_config = ConfigDict(extra="forbid")

    status: InvoiceStatus | None = None
    thirdparty_id: int | None = Field(default=None, gt=0)
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None


class ListSupplierInvoicesArgs(BaseModel):
    """Argumentos para list_supplier_invoices."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    status: InvoiceStatus | None = None
    thirdparty_id: int | None = Field(default=None, gt=0)
    thirdparty_name: str | None = Field(default=None, max_length=200)
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    sort_field: InvoiceSortField = InvoiceSortField.DATE
    sort_order: SortOrder = SortOrder.DESC


class SearchSupplierInvoicesArgs(BaseModel):
    """Argumentos para search_supplier_invoices."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    status: InvoiceStatus | None = None
    thirdparty_id: int | None = Field(default=None, gt=0)
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    sort_field: InvoiceSortField = InvoiceSortField.DATE
    sort_order: SortOrder = SortOrder.DESC


class GetSupplierInvoiceArgs(BaseModel):
    """Argumentos para get_supplier_invoice."""

    model_config = ConfigDict(extra="forbid")

    invoice_id: int = Field(..., gt=0)


class CountSupplierInvoicesArgs(BaseModel):
    """Argumentos para count_supplier_invoices."""

    model_config = ConfigDict(extra="forbid")

    status: InvoiceStatus | None = None
    thirdparty_id: int | None = Field(default=None, gt=0)
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None


from core.hermes.insights.models import InsightArgs


# =========================================================================
# UNION DE ARGUMENTOS
# =========================================================================


ThirdpartyArgs = ListThirdpartiesArgs | SearchThirdpartiesArgs | GetThirdpartyArgs | CountThirdpartiesArgs

InvoiceArgs = (
    ListCustomerInvoicesArgs
    | SearchCustomerInvoicesArgs
    | GetCustomerInvoiceArgs
    | CountCustomerInvoicesArgs
    | ListSupplierInvoicesArgs
    | SearchSupplierInvoicesArgs
    | GetSupplierInvoiceArgs
    | CountSupplierInvoicesArgs
)


# =========================================================================
# INTENT ESTRUCTURADO PRINCIPAL
# =========================================================================


class StructuredIntent(BaseModel):
    """
    Intent estructurado validado por Pydantic.

    Este es el contrato que Ollama debe cumplir via structured output.
    No se permite extra="allow" - solo campos definidos explícitamente.
    """

    model_config = ConfigDict(extra="forbid")

    action: ThirdpartyAction | InvoiceAction | InsightAction
    arguments: ThirdpartyArgs | InvoiceArgs | InsightArgs

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

        # Validación cruzada básica - Thirdparty
        if action == ThirdpartyAction.GET:
            if "thirdparty_id" not in v:
                raise ValueError("GET requiere thirdparty_id")
        elif action == ThirdpartyAction.SEARCH:
            if "query" not in v or not v["query"]:
                raise ValueError("SEARCH requiere query no vacía")

        # Validación cruzada básica - Invoice Customer
        if action == InvoiceAction.GET_CUSTOMER_INVOICE:
            if "invoice_id" not in v:
                raise ValueError("GET_CUSTOMER_INVOICE requiere invoice_id")
        elif action == InvoiceAction.SEARCH_CUSTOMER_INVOICES:
            if "query" not in v or not v["query"]:
                raise ValueError("SEARCH_CUSTOMER_INVOICES requiere query no vacía")

        # Validación cruzada básica - Invoice Supplier
        if action == InvoiceAction.GET_SUPPLIER_INVOICE:
            if "invoice_id" not in v:
                raise ValueError("GET_SUPPLIER_INVOICE requiere invoice_id")
        elif action == InvoiceAction.SEARCH_SUPPLIER_INVOICES:
            if "query" not in v or not v["query"]:
                raise ValueError("SEARCH_SUPPLIER_INVOICES requiere query no vacía")

        # Validación cruzada básica - Insight Customer
        if action == InsightAction.CUSTOMER_INVOICE_SUMMARY_BY_THIRDPARTY:
            if "thirdparty_id" not in v:
                raise ValueError("CUSTOMER_INVOICE_SUMMARY_BY_THIRDPARTY requiere thirdparty_id")

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

    model_config = ConfigDict(extra="forbid")

    status: InterpretationStatus
    intent: StructuredIntent | None = None
    clarification_message: str | None = None
    error_message: str | None = None
    fallback_used: bool = False
    interpreter_used: str = "unknown"

    @model_validator(mode="after")
    def _validate_status_consistency(self) -> IntentInterpretation:
        """Validar consistencia entre status e intent."""
        if self.status == InterpretationStatus.MATCHED:
            if self.intent is None:
                raise ValueError("MATCHED requiere intent no nulo")
            if self.clarification_message is not None:
                raise ValueError("MATCHED no debe tener clarification_message")
        elif self.status == InterpretationStatus.NO_MATCH:
            if self.intent is not None:
                raise ValueError("NO_MATCH debe tener intent = None")
            if self.clarification_message is not None:
                raise ValueError("NO_MATCH no debe tener clarification_message")
        elif self.status == InterpretationStatus.NEEDS_CLARIFICATION:
            if self.intent is not None:
                raise ValueError("NEEDS_CLARIFICATION debe tener intent = None")
            if not self.clarification_message:
                raise ValueError("NEEDS_CLARIFICATION requiere clarification_message")
        elif self.status == InterpretationStatus.INVALID_OUTPUT:
            if self.intent is not None:
                raise ValueError("INVALID_OUTPUT debe tener intent = None")
        elif self.status == InterpretationStatus.PROVIDER_ERROR:
            if self.intent is not None:
                raise ValueError("PROVIDER_ERROR debe tener intent = None")
        return self

    def is_actionable(self) -> bool:
        """Verificar si el resultado puede ejecutarse como Tool."""
        return self.status == InterpretationStatus.MATCHED and self.intent is not None


# =========================================================================
# CATÁLOGO DE TOOLS PARA PROMPT
# =========================================================================


class ToolSchema(BaseModel):
    """Esquema reducido de una Tool para el prompt del LLM."""

    model_config = ConfigDict(extra="forbid")

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

    # Thirdparty actions
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

    # Customer Invoice actions
    elif action == InvoiceAction.LIST_CUSTOMER_INVOICES:
        list_args = cast(ListCustomerInvoicesArgs, args)
        return "list_customer_invoices", {
            "limit": list_args.limit,
            "offset": list_args.offset,
            "status": list_args.status.value if list_args.status else None,
            "thirdparty_id": list_args.thirdparty_id,
            "thirdparty_name": list_args.thirdparty_name,
            "date_from": list_args.date_from.isoformat() if list_args.date_from else None,
            "date_to": list_args.date_to.isoformat() if list_args.date_to else None,
            "due_from": list_args.due_from.isoformat() if list_args.due_from else None,
            "due_to": list_args.due_to.isoformat() if list_args.due_to else None,
            "sort_field": list_args.sort_field.value,
            "sort_order": list_args.sort_order.value,
        }

    elif action == InvoiceAction.SEARCH_CUSTOMER_INVOICES:
        search_args = cast(SearchCustomerInvoicesArgs, args)
        return "search_customer_invoices", {
            "query": search_args.query,
            "limit": search_args.limit,
            "offset": search_args.offset,
            "status": search_args.status.value if search_args.status else None,
            "thirdparty_id": search_args.thirdparty_id,
            "date_from": search_args.date_from.isoformat() if search_args.date_from else None,
            "date_to": search_args.date_to.isoformat() if search_args.date_to else None,
            "due_from": search_args.due_from.isoformat() if search_args.due_from else None,
            "due_to": search_args.due_to.isoformat() if search_args.due_to else None,
            "sort_field": search_args.sort_field.value,
            "sort_order": search_args.sort_order.value,
        }

    elif action == InvoiceAction.GET_CUSTOMER_INVOICE:
        get_args = cast(GetCustomerInvoiceArgs, args)
        return "get_customer_invoice", {
            "invoice_id": get_args.invoice_id,
        }

    elif action == InvoiceAction.COUNT_CUSTOMER_INVOICES:
        count_args = cast(CountCustomerInvoicesArgs, args)
        return "count_customer_invoices", {
            "status": count_args.status.value if count_args.status else None,
            "thirdparty_id": count_args.thirdparty_id,
            "date_from": count_args.date_from.isoformat() if count_args.date_from else None,
            "date_to": count_args.date_to.isoformat() if count_args.date_to else None,
            "due_from": count_args.due_from.isoformat() if count_args.due_from else None,
            "due_to": count_args.due_to.isoformat() if count_args.due_to else None,
        }

    # Supplier Invoice actions
    elif action == InvoiceAction.LIST_SUPPLIER_INVOICES:
        list_args = cast(ListSupplierInvoicesArgs, args)
        return "list_supplier_invoices", {
            "limit": list_args.limit,
            "offset": list_args.offset,
            "status": list_args.status.value if list_args.status else None,
            "thirdparty_id": list_args.thirdparty_id,
            "thirdparty_name": list_args.thirdparty_name,
            "date_from": list_args.date_from.isoformat() if list_args.date_from else None,
            "date_to": list_args.date_to.isoformat() if list_args.date_to else None,
            "due_from": list_args.due_from.isoformat() if list_args.due_from else None,
            "due_to": list_args.due_to.isoformat() if list_args.due_to else None,
            "sort_field": list_args.sort_field.value,
            "sort_order": list_args.sort_order.value,
        }

    elif action == InvoiceAction.SEARCH_SUPPLIER_INVOICES:
        search_args = cast(SearchSupplierInvoicesArgs, args)
        return "search_supplier_invoices", {
            "query": search_args.query,
            "limit": search_args.limit,
            "offset": search_args.offset,
            "status": search_args.status.value if search_args.status else None,
            "thirdparty_id": search_args.thirdparty_id,
            "date_from": search_args.date_from.isoformat() if search_args.date_from else None,
            "date_to": search_args.date_to.isoformat() if search_args.date_to else None,
            "due_from": search_args.due_from.isoformat() if search_args.due_from else None,
            "due_to": search_args.due_to.isoformat() if search_args.due_to else None,
            "sort_field": search_args.sort_field.value,
            "sort_order": search_args.sort_order.value,
        }

    elif action == InvoiceAction.GET_SUPPLIER_INVOICE:
        get_args = cast(GetSupplierInvoiceArgs, args)
        return "get_supplier_invoice", {
            "invoice_id": get_args.invoice_id,
        }

    elif action == InvoiceAction.COUNT_SUPPLIER_INVOICES:
        count_args = cast(CountSupplierInvoicesArgs, args)
        return "count_supplier_invoices", {
            "status": count_args.status.value if count_args.status else None,
            "thirdparty_id": count_args.thirdparty_id,
            "date_from": count_args.date_from.isoformat() if count_args.date_from else None,
            "date_to": count_args.date_to.isoformat() if count_args.date_to else None,
            "due_from": count_args.due_from.isoformat() if count_args.due_from else None,
            "due_to": count_args.due_to.isoformat() if count_args.due_to else None,
        }

    # Customer Insight actions
    elif action == InsightAction.CUSTOMER_INVOICE_SUMMARY:
        args_insight = cast(CustomerInvoiceSummaryArgs, args)
        return "customer_invoice_summary", {
            "period": args_insight.period.value,
            "date_from": args_insight.date_from.isoformat() if args_insight.date_from else None,
            "date_to": args_insight.date_to.isoformat() if args_insight.date_to else None,
            "status": args_insight.status,
            "thirdparty_id": args_insight.thirdparty_id,
        }

    elif action == InsightAction.CUSTOMER_OUTSTANDING_SUMMARY:
        args_insight = cast(CustomerOutstandingSummaryArgs, args)
        return "customer_outstanding_summary", {
            "period": args_insight.period.value,
            "date_from": args_insight.date_from.isoformat() if args_insight.date_from else None,
            "date_to": args_insight.date_to.isoformat() if args_insight.date_to else None,
            "status": args_insight.status,
        }

    elif action == InsightAction.CUSTOMER_OUTSTANDING_BY_THIRDPARTY:
        args_insight = cast(CustomerOutstandingByThirdpartyArgs, args)
        return "customer_outstanding_by_thirdparty", {
            "period": args_insight.period.value,
            "date_from": args_insight.date_from.isoformat() if args_insight.date_from else None,
            "date_to": args_insight.date_to.isoformat() if args_insight.date_to else None,
            "status": args_insight.status,
            "limit": args_insight.limit,
        }

    elif action == InsightAction.CUSTOMER_INVOICE_SUMMARY_BY_THIRDPARTY:
        args_insight = cast(CustomerInvoiceSummaryByThirdpartyArgs, args)
        return "customer_invoice_summary_by_thirdparty", {
            "period": args_insight.period.value,
            "date_from": args_insight.date_from.isoformat() if args_insight.date_from else None,
            "date_to": args_insight.date_to.isoformat() if args_insight.date_to else None,
            "thirdparty_id": args_insight.thirdparty_id,
            "status": args_insight.status,
        }

    # Supplier Insight actions
    elif action == InsightAction.SUPPLIER_INVOICE_SUMMARY:
        args_insight = cast(SupplierInvoiceSummaryArgs, args)
        return "supplier_invoice_summary", {
            "period": args_insight.period.value,
            "date_from": args_insight.date_from.isoformat() if args_insight.date_from else None,
            "date_to": args_insight.date_to.isoformat() if args_insight.date_to else None,
            "status": args_insight.status,
            "thirdparty_id": args_insight.thirdparty_id,
        }

    elif action == InsightAction.SUPPLIER_OUTSTANDING_SUMMARY:
        args_insight = cast(SupplierOutstandingSummaryArgs, args)
        return "supplier_outstanding_summary", {
            "period": args_insight.period.value,
            "date_from": args_insight.date_from.isoformat() if args_insight.date_from else None,
            "date_to": args_insight.date_to.isoformat() if args_insight.date_to else None,
            "status": args_insight.status,
        }

    elif action == InsightAction.SUPPLIER_OUTSTANDING_BY_THIRDPARTY:
        args_insight = cast(SupplierOutstandingByThirdpartyArgs, args)
        return "supplier_outstanding_by_thirdparty", {
            "period": args_insight.period.value,
            "date_from": args_insight.date_from.isoformat() if args_insight.date_from else None,
            "date_to": args_insight.date_to.isoformat() if args_insight.date_to else None,
            "status": args_insight.status,
            "limit": args_insight.limit,
        }

    elif action == InsightAction.SUPPLIER_INVOICE_SUMMARY_BY_THIRDPARTY:
        args_insight = cast(SupplierInvoiceSummaryByThirdpartyArgs, args)
        return "supplier_invoice_summary_by_thirdparty", {
            "period": args_insight.period.value,
            "date_from": args_insight.date_from.isoformat() if args_insight.date_from else None,
            "date_to": args_insight.date_to.isoformat() if args_insight.date_to else None,
            "thirdparty_id": args_insight.thirdparty_id,
            "status": args_insight.status,
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


INVOICE_TOOLS_CATALOG: list[ToolSchema] = [
    ToolSchema(
        name="list_customer_invoices",
        description="Listar facturas de cliente con paginación y filtros",
        arguments_schema=ListCustomerInvoicesArgs.model_json_schema(),
    ),
    ToolSchema(
        name="search_customer_invoices",
        description="Buscar facturas de cliente por referencia, cliente, importe o fecha",
        arguments_schema=SearchCustomerInvoicesArgs.model_json_schema(),
    ),
    ToolSchema(
        name="get_customer_invoice",
        description="Obtener detalle completo de una factura de cliente por ID",
        arguments_schema=GetCustomerInvoiceArgs.model_json_schema(),
    ),
    ToolSchema(
        name="count_customer_invoices",
        description="Contar total de facturas de cliente con filtros opcionales",
        arguments_schema=CountCustomerInvoicesArgs.model_json_schema(),
    ),
    ToolSchema(
        name="list_supplier_invoices",
        description="Listar facturas de proveedor con paginación y filtros",
        arguments_schema=ListSupplierInvoicesArgs.model_json_schema(),
    ),
    ToolSchema(
        name="search_supplier_invoices",
        description="Buscar facturas de proveedor por referencia, proveedor, importe o fecha",
        arguments_schema=SearchSupplierInvoicesArgs.model_json_schema(),
    ),
    ToolSchema(
        name="get_supplier_invoice",
        description="Obtener detalle completo de una factura de proveedor por ID",
        arguments_schema=GetSupplierInvoiceArgs.model_json_schema(),
    ),
    ToolSchema(
        name="count_supplier_invoices",
        description="Contar total de facturas de proveedor con filtros opcionales",
        arguments_schema=CountSupplierInvoicesArgs.model_json_schema(),
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
    for tool in INVOICE_TOOLS_CATALOG:
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
# FORMATTERS PARA TELEGRAM - FACTURAS
# =========================================================================
# FORMATTERS PARA TELEGRAM - TERCEROS
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
