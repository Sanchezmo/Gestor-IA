"""
Domain models for Supplier Invoice Processing.

Ported and adapted from Transvega Animal:
- agents/invoice_processing/agent.py (InvoiceData, InvoiceLine, SupplierInfo)
- services/integration-api/app/schemas/__init__.py (SupplierInvoiceLineBase, SupplierInvoiceBase)

Adapted for Gestor-IA architecture:
- Decimal-only money handling
- Multi-VAT support (tax_breakdown)
- Withholding structural support
- Confidence tracking
- Document hash for idempotency
- Inference tracking (KNOWN/INFERRED/UNKNOWN)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class InvoiceFieldSource(StrEnum):
    """Source of a field value for traceability."""
    KNOWN = "known"          # Directly extracted from document
    INFERRED = "inferred"    # Mathematically derived from other fields
    UNKNOWN = "unknown"      # Not present, not inferable


class DocumentClassification(StrEnum):
    """Document classification result."""
    SINGLE_INVOICE = "single_invoice"
    MULTI_DOCUMENT = "multi_document"
    NOT_INVOICE = "not_invoice"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    """Result of document classification."""
    document_type: DocumentClassification
    confidence: Decimal
    signals: list[str]
    page_count: int = 0
    classification_strategy: str = "heuristic"


class SupplierResolutionStatus(StrEnum):
    """Supplier lookup result."""
    FOUND = "found"
    FOUND_NOT_SUPPLIER = "found_not_supplier"  # Exists but not marked as supplier
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


class ValidationStatus(StrEnum):
    """Deterministic validation result."""
    VALID = "valid"
    REVIEW_REQUIRED = "review_required"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class SupplierInfo:
    """Supplier information extracted from invoice."""
    name: str
    tax_id: str
    address: str | None = None
    email: str | None = None
    phone: str | None = None


@dataclass(frozen=True, slots=True)
class InvoiceLine:
    """Single line in a supplier invoice."""
    description: str
    quantity: Decimal
    unit_price: Decimal           # Net unit price (excl. VAT)
    vat_rate: Decimal             # VAT rate as percentage (e.g., 21.0)
    discount_percent: Decimal = Decimal("0")
    product_ref: str | None = None
    line_total_excl_tax: Decimal | None = None   # Computed: qty * unit_price * (1 - discount%)
    vat_amount: Decimal | None = None             # Computed: line_total_excl_tax * vat_rate / 100
    line_total_incl_tax: Decimal | None = None    # Computed: line_total_excl_tax + vat_amount

    def __post_init__(self) -> None:
        # Compute derived fields if not provided
        if self.line_total_excl_tax is None:
            base = self.quantity * self.unit_price * (Decimal("1") - self.discount_percent / Decimal("100"))
            object.__setattr__(self, "line_total_excl_tax", base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        if self.vat_amount is None:
            vat = self.line_total_excl_tax * self.vat_rate / Decimal("100")
            object.__setattr__(self, "vat_amount", vat.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        if self.line_total_incl_tax is None:
            total = self.line_total_excl_tax + self.vat_amount
            object.__setattr__(self, "line_total_incl_tax", total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True, slots=True)
class TaxBreakdownItem:
    """VAT breakdown by rate."""
    rate: Decimal          # VAT rate (e.g., 21.0)
    base: Decimal          # Taxable base for this rate
    amount: Decimal        # VAT amount for this rate
    source: InvoiceFieldSource = InvoiceFieldSource.KNOWN


@dataclass(frozen=True, slots=True)
class WithholdingBreakdownItem:
    """Withholding (retención) breakdown."""
    rate: Decimal
    base: Decimal
    amount: Decimal
    source: InvoiceFieldSource = InvoiceFieldSource.KNOWN


@dataclass(frozen=True, slots=True)
class SupplierInvoiceDraft:
    """
    Normalized supplier invoice draft ready for validation and preview.

    This is the central domain model that flows through:
    Extraction -> Normalization -> Validation -> Supplier Resolution -> Preview
    """
    # Document identification
    document_hash: str                    # SHA-256 of original document
    document_filename: str
    document_mime_type: str
    document_size_bytes: int
    page_count: int = 1

    # Classification
    classification: DocumentClassification = DocumentClassification.UNKNOWN
    classification_confidence: Decimal = Decimal("0")
    classification_signals: list[str] = field(default_factory=list)

    # Supplier (extracted)
    supplier: SupplierInfo | None = None

    # Invoice header
    invoice_number: str | None = None
    invoice_number_source: InvoiceFieldSource = InvoiceFieldSource.UNKNOWN
    invoice_date: date | None = None
    invoice_date_source: InvoiceFieldSource = InvoiceFieldSource.UNKNOWN
    due_date: date | None = None
    due_date_source: InvoiceFieldSource = InvoiceFieldSource.UNKNOWN
    currency: str = "EUR"
    payment_terms: str | None = None
    payment_method: str | None = None
    notes: str | None = None

    # Lines
    lines: list[InvoiceLine] = field(default_factory=list)

    # Tax breakdown (multi-VAT)
    tax_breakdown: list[TaxBreakdownItem] = field(default_factory=list)

    # Withholding breakdown
    withholding_breakdown: list[WithholdingBreakdownItem] = field(default_factory=list)

    # Totals (Decimal, never float)
    subtotal: Decimal | None = None              # Sum of line_total_excl_tax
    subtotal_source: InvoiceFieldSource = InvoiceFieldSource.UNKNOWN
    tax_total: Decimal | None = None             # Sum of tax_breakdown amounts
    tax_total_source: InvoiceFieldSource = InvoiceFieldSource.UNKNOWN
    withholding_total: Decimal | None = None     # Sum of withholding amounts
    withholding_total_source: InvoiceFieldSource = InvoiceFieldSource.UNKNOWN
    total: Decimal | None = None                 # subtotal + tax_total - withholding_total
    total_source: InvoiceFieldSource = InvoiceFieldSource.UNKNOWN

    # Supplier resolution (post-extraction)
    supplier_resolution_status: SupplierResolutionStatus = SupplierResolutionStatus.NOT_FOUND
    supplier_dolibarr_id: int | None = None
    supplier_candidates: list[dict[str, Any]] = field(default_factory=list)

    # Validation
    validation_status: ValidationStatus = ValidationStatus.REVIEW_REQUIRED
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    validation_warnings: list[dict[str, Any]] = field(default_factory=list)

    # Extraction metadata
    extraction_confidence: Decimal = Decimal("0")
    extraction_model: str | None = None
    extraction_raw_text_chars: int = 0
    inference_count: int = 0

    # Processing metadata
    instance_id: str = ""
    received_at: str = ""  # ISO timestamp
    correlation_id: UUID = field(default_factory=uuid4)

    # =========================================================================
    # COMPUTED PROPERTIES (for preview/formatting)
    # =========================================================================

    def has_supplier(self) -> bool:
        return self.supplier is not None and bool(self.supplier.tax_id)

    def has_complete_header(self) -> bool:
        return all([
            self.invoice_number is not None,
            self.invoice_date is not None,
            self.total is not None,
        ])

    def get_inferred_fields(self) -> list[str]:
        """Return list of field names that were inferred."""
        inferred = []
        for field_name in ["invoice_number", "invoice_date", "due_date", "subtotal", "tax_total", "withholding_total", "total"]:
            source = getattr(self, f"{field_name}_source", InvoiceFieldSource.UNKNOWN)
            if source == InvoiceFieldSource.INFERRED:
                inferred.append(field_name)
        return inferred

    def get_unknown_fields(self) -> list[str]:
        """Return list of field names that are unknown."""
        unknown = []
        for field_name in ["invoice_number", "invoice_date", "due_date", "subtotal", "tax_total", "withholding_total", "total"]:
            source = getattr(self, f"{field_name}_source", InvoiceFieldSource.UNKNOWN)
            if source == InvoiceFieldSource.UNKNOWN:
                unknown.append(field_name)
        return unknown

    def get_supplier_display(self) -> str:
        """Supplier display string for preview."""
        if not self.supplier:
            return "No identificado"
        parts = [self.supplier.name]
        if self.supplier.tax_id:
            parts.append(f"({self.supplier.tax_id})")
        return " ".join(parts)

    def get_validation_display(self) -> str:
        """Validation status display for preview."""
        icons = {
            ValidationStatus.VALID: "✓",
            ValidationStatus.REVIEW_REQUIRED: "⚠",
            ValidationStatus.INVALID: "✗",
        }
        icon = icons.get(self.validation_status, "?")
        return f"{icon} {self.validation_status.value.replace('_', ' ').title()}"

    def get_supplier_resolution_display(self) -> str:
        """Supplier resolution display for preview."""
        icons = {
            SupplierResolutionStatus.FOUND: "✓",
            SupplierResolutionStatus.FOUND_NOT_SUPPLIER: "⚠",
            SupplierResolutionStatus.NOT_FOUND: "✗",
            SupplierResolutionStatus.AMBIGUOUS: "?",
        }
        icon = icons.get(self.supplier_resolution_status, "?")
        return f"{icon} {self.supplier_resolution_status.value.replace('_', ' ').title()}"


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Result of document extraction."""
    success: bool
    draft: SupplierInvoiceDraft | None = None
    error: str | None = None
    error_code: str | None = None
    requires_review: bool = False


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Result of deterministic validation."""
    status: ValidationStatus
    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SupplierResolutionResult:
    """Result of supplier lookup in Dolibarr."""
    status: SupplierResolutionStatus
    supplier_dolibarr_id: int | None = None
    supplier_data: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


# =========================================================================
# HELPER FUNCTIONS
# =========================================================================

def parse_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    """Safely parse any value to Decimal."""
    if value is None:
        return default
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        # Normalize: handle both "1.234,56" (EU) and "1,234.56" (US) formats
        # Heuristic: if last separator is comma, it's decimal separator
        # If last separator is dot, it's decimal separator
        normalized = value.strip().replace(" ", "")
        if not normalized:
            return default

        # Find last separator
        last_comma = normalized.rfind(",")
        last_dot = normalized.rfind(".")

        if last_comma > last_dot:
            # Comma is decimal separator (EU format: 1.234,56)
            normalized = normalized.replace(".", "").replace(",", ".")
        elif last_dot > last_comma:
            # Dot is decimal separator (US format: 1,234.56)
            normalized = normalized.replace(",", "")
        else:
            # No separators or only one type - assume dot is decimal
            normalized = normalized.replace(",", ".")

        try:
            return Decimal(normalized)
        except Exception:
            return default
    return default


def format_money(amount: Decimal | None, currency: str = "EUR") -> str:
    """Format Decimal amount for display."""
    if amount is None:
        return "—"
    return f"{amount:,.2f} {currency}".replace(",", "X").replace(".", ",").replace("X", ".")


def format_date(d: date | None) -> str:
    """Format date for display."""
    if d is None:
        return "—"
    return d.strftime("%d/%m/%Y")


def normalize_tax_id(tax_id: str) -> str:
    """Normalize tax ID for comparison (remove spaces, uppercase)."""
    if not tax_id:
        return ""
    return tax_id.replace(" ", "").replace("-", "").upper()
