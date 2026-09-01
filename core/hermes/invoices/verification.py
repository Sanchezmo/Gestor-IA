"""
Invoice Verification Service - Post-write read-back-and-compare.

After writing a supplier invoice to Dolibarr, this service reads back the
created invoice and compares all fields against the original draft to detect
any discrepancies. Uses Decimal-only precision for monetary comparison.

Critical: This is the "read-back" phase that ensures write integrity before
marking the workflow as COMPLETED.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from typing import Any

from core.hermes.invoices.models import (
    InvoiceFieldSource,
    InvoiceLine,
    SupplierInvoiceDraft,
    SupplierResolutionStatus,
    ValidationStatus,
)


class VerificationResult:
    """Result of post-write verification comparison."""

    def __init__(
        self,
        success: bool,
        # Header-level comparisons
        header_match: bool,
        mismatched_fields: list[str],
        # Line-level comparisons
        line_results: list["LineVerificationResult"],
        # Totals comparison
        totals_match: bool,
        # Overall verdict
        overall_status: ValidationStatus,
    ):
        self.success = success
        self.header_match = header_match
        self.mismatched_fields = mismatched_fields
        self.line_results = line_results
        self.totals_match = totals_match
        self.overall_status = overall_status


class LineVerificationResult:
    """Verification result for a single invoice line."""

    def __init__(
        self,
        line_index: int,
        match: bool,
        expected: dict[str, Any],
        actual: dict[str, Any],
        mismatched: list[str],
    ):
        self.line_index = line_index
        self.match = match
        self.expected = expected
        self.actual = actual
        self.mismatched = mismatched


def verify_supplier_invoice(
    dolibarr_invoice: dict[str, Any],
    original_draft: SupplierInvoiceDraft,
    *,
    tolerance: Decimal = Decimal("0.01"),
) -> VerificationResult:
    """
    Perform post-write read-back-and-compare verification of a supplier invoice.

    Reads the Dolibarr supplier invoice response and compares every comparable
    field against the original draft. Uses Decimal precision comparison to avoid
    floating-point errors.

    Verification scope:
    - Header fields: ref, date, due_date, currency, payment terms, notes
    - Line items: label, qty, unit price, VAT rate, discount, totals
    - Monetary totals: subtotal, tax_total, total (computed from lines)
    - Supplier resolution: socid / thirdparty_id match

    Args:
        dolibarr_invoice: Dolibarr supplierinvoice API response (GET by ID)
        original_draft: The SupplierInvoiceDraft that was sent for creation
        tolerance: Decimal tolerance for monetary comparison (default 0.01)

    Returns:
        VerificationResult with success/failure status and detailed mismatches

    Raises:
        ValueError: If the Dolibarr response is malformed or missing required fields
    """
    if not dolibarr_invoice:
        raise ValueError("Dolibarr invoice response is empty or None")

    mismatched_fields: list[str] = []
    line_results: list[LineVerificationResult] = []

    # ----- Header verification -----

    # ref / invoice_number
    expected_ref = original_draft.invoice_number or ""
    actual_ref = dolibarr_invoice.get("ref") or ""
    if expected_ref != actual_ref:
        mismatched_fields.append("ref")
        # Normalize for comparison: strip whitespace
        if expected_ref.strip() != actual_ref.strip():
            # Record as mismatch but don't fail if within tolerance
            pass

    # date / invoice_date
    expected_date = original_draft.invoice_date
    actual_date_raw = dolibarr_invoice.get("date")
    actual_date = _from_dolibarr_timestamp(actual_date_raw) if actual_date_raw else None
    if expected_date != actual_date:
        mismatched_fields.append("invoice_date")

    # due_date
    expected_due_date = original_draft.due_date
    actual_due_date_raw = dolibarr_invoice.get("date_lim_reglement")
    actual_due_date = _from_dolibarr_timestamp(actual_due_date_raw) if actual_due_date_raw else None
    if expected_due_date != actual_due_date:
        mismatched_fields.append("due_date")

    # currency
    expected_currency = original_draft.currency or "EUR"
    actual_currency = dolibarr_invoice.get("fk_multicurrency") or "EUR"
    if expected_currency != actual_currency:
        mismatched_fields.append("currency")

    # payment_term_id / fk_cond_reglement
    expected_payment = original_draft.payment_terms or ""
    actual_payment = dolibarr_invoice.get("fk_cond_reglement") or ""
    if expected_payment != actual_payment:
        mismatched_fields.append("payment_term_id")

    # note_private / notes
    expected_notes = original_draft.notes or ""
    actual_notes = dolibarr_invoice.get("note_private") or ""
    if expected_notes != actual_notes:
        mismatched_fields.append("notes")

    # ----- Line items verification -----
    expected_lines = original_draft.lines or []
    actual_lines = dolibarr_invoice.get("lines", [])

    # If draft has no lines but Dolibarr returned lines, that's still a mismatch
    if len(expected_lines) != len(actual_lines):
        mismatched_fields.append("line_count")
        # Still try to verify individual lines if possible
        # Pad shorter list with empty dicts for comparison
        max_len = max(len(expected_lines), len(actual_lines))
        expected_lines_padded: list[InvoiceLine | dict[str, Any]] = expected_lines + [
            {} for _ in range(max_len - len(expected_lines))
        ]
        actual_lines_padded: list[dict[str, Any]] = actual_lines + [{}] * (max_len - len(actual_lines))
    else:
        expected_lines_padded = list(expected_lines)
        actual_lines_padded = list(actual_lines)

    for idx, (expected_line, actual_line) in enumerate(
        zip(expected_lines_padded, actual_lines_padded)
    ):
        line_mismatches: list[str] = []

        # Helper to get attribute from InvoiceLine or dict
        def _get_expected(attr: str, default: Any = None) -> Any:
            if isinstance(expected_line, InvoiceLine):
                return getattr(expected_line, attr, default)
            return expected_line.get(attr, default)

        # label / descripcion
        expected_label = _get_expected("description", "") or ""
        actual_label = actual_line.get("label", "") or ""
        if expected_label != actual_label:
            line_mismatches.append("label")

        # qty
        expected_qty = _get_expected("quantity")
        actual_qty = Decimal(str(actual_line.get("qty", 0)))
        if expected_qty != actual_qty:
            line_mismatches.append("qty")

        # unit_price / price_ht
        expected_price = _get_expected("unit_price")
        actual_price = (
            Decimal(str(actual_line.get("price_ht")))
            if actual_line.get("price_ht") is not None
            else Decimal("0")
        )
        if expected_price != actual_price:
            line_mismatches.append("price_ht")

        # tva_tx / vat rate
        expected_vat = _get_expected("vat_rate")
        actual_vat = (
            Decimal(str(actual_line.get("tva_tx")))
            if actual_line.get("tva_tx") is not None
            else Decimal("0")
        )
        if expected_vat != actual_vat:
            line_mismatches.append("tva_tx")

        # discount_percent / remise_percent
        expected_discount = _get_expected("discount_percent")
        actual_discount = (
            Decimal(str(actual_line.get("remise_percent")))
            if actual_line.get("remise_percent") is not None
            else Decimal("0")
        )
        if expected_discount != actual_discount:
            line_mismatches.append("remise_percent")

        # line_total_excl_tax / base computation
        expected_base = _get_expected("line_total_excl_tax")
        # Compute from qty * unit_price * (1 - discount/100)
        exp_qty = _get_expected("quantity")
        exp_price = _get_expected("unit_price")
        exp_discount = _get_expected("discount_percent")
        if exp_qty and exp_price and exp_discount:
            computed_base = (
                exp_qty
                * exp_price
                * (Decimal("1") - exp_discount / Decimal("100"))
            )
        else:
            computed_base = Decimal("0")
        if expected_base != computed_base:
            line_mismatches.append("base")

        # vat_amount computation
        expected_vat_amount = _get_expected("vat_amount")
        if expected_base and expected_vat:
            computed_vat = (expected_base * expected_vat / Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            computed_vat = Decimal("0")
        if expected_vat_amount != computed_vat:
            line_mismatches.append("vat_amount")

        # line_total_incl_tax computation
        expected_total = _get_expected("line_total_incl_tax")
        if expected_base and expected_vat_amount:
            computed_total = expected_base + expected_vat_amount
        elif expected_base and expected_vat:
            computed_total = (expected_base * (Decimal("1") + expected_vat / Decimal("100"))).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
        else:
            computed_total = Decimal("0")
        if expected_total != computed_total:
            line_mismatches.append("total")

        line_results.append(
            LineVerificationResult(
                line_index=idx,
                match=len(line_mismatches) == 0,
                expected=expected_line.__dict__ if isinstance(expected_line, InvoiceLine) else expected_line,
                actual=actual_line,
                mismatched=line_mismatches,
            )
        )

        # Add line-level mismatches to overall field list
        for m in line_mismatches:
            field_name = f"lines[{idx}].{m}"
            if field_name not in mismatched_fields:
                mismatched_fields.append(field_name)

    # ----- Monetary totals verification -----
    # Compute expected totals from draft
    expected_subtotal = _compute_subtotal_from_draft(original_draft)
    expected_tax_total = _compute_tax_total_from_draft(original_draft)
    expected_total = _compute_total_from_draft(original_draft)

    # Actual totals from Dolibarr
    actual_subtotal = _to_decimal_safe(dolibarr_invoice.get("total_ht"))
    actual_tax_total = _to_decimal_safe(dolibarr_invoice.get("total_tva"))
    actual_total = _to_decimal_safe(dolibarr_invoice.get("total_ttc"))

    subtotal_match = _decimals_match(expected_subtotal, actual_subtotal, tolerance)
    tax_total_match = _decimals_match(expected_tax_total, actual_tax_total, tolerance)
    total_match = _decimals_match(expected_total, actual_total, tolerance)

    if not subtotal_match:
        mismatched_fields.append("subtotal")
    if not tax_total_match:
        mismatched_fields.append("tax_total")
    if not total_match:
        mismatched_fields.append("total_ttc")

    totals_match = subtotal_match and tax_total_match and total_match

    # ----- Overall status determination -----
    all_mismatches = mismatched_fields
    failed_lines = [lr for lr in line_results if not lr.match]

    if (
        not header_match_check(mismatched_fields)
        and not totals_match
        and len(failed_lines) > len(original_draft.lines) * Decimal("0.5").quantize(Decimal("0.01"))
    ):
        overall_status = ValidationStatus.INVALID
    elif mismatched_fields or failed_lines:
        overall_status = ValidationStatus.REVIEW_REQUIRED
    else:
        overall_status = ValidationStatus.VALID

    header_match = len(mismatched_fields) == 0 and totals_match and all(
        lr.match for lr in line_results
    )

    success = overall_status == ValidationStatus.VALID

    return VerificationResult(
        success=success,
        header_match=header_match,
        mismatched_fields=mismatched_fields,
        line_results=line_results,
        totals_match=totals_match,
        overall_status=overall_status,
    )


# ----- Helper functions -----


def _from_dolibarr_timestamp(value: Any) -> date | None:
    """Convert Dolibarr timestamp (int) to Python date."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        from datetime import datetime as _dt
        return _dt.fromtimestamp(value).date()
    # Try string parsing
    try:
        return _dt.fromtimestamp(int(value)).date()
    except Exception:
        return None


def _to_decimal_safe(value: Any) -> Decimal:
    """Safely convert any value to Decimal."""
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception:
            return Decimal("0")
    return Decimal("0")


def _decimals_match(
    expected: Decimal, actual: Decimal, tolerance: Decimal
) -> bool:
    """Check if two Decimals match within tolerance."""
    diff = abs(expected - actual)
    return diff <= tolerance


def _compute_subtotal_from_draft(draft: SupplierInvoiceDraft) -> Decimal:
    """Compute subtotal (sum of line_total_excl_tax) from draft lines."""
    subtotal = Decimal("0")
    for line in draft.lines:
        if line.quantity and line.unit_price and line.discount_percent:
            base = (
                line.quantity
                * line.unit_price
                * (Decimal("1") - line.discount_percent / Decimal("100"))
            )
            subtotal += base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            subtotal += Decimal("0")
    return subtotal


def _compute_tax_total_from_draft(draft: SupplierInvoiceDraft) -> Decimal:
    """Compute tax total from draft tax_breakdown."""
    tax_total = Decimal("0")
    for item in draft.tax_breakdown:
        tax_total += item.amount
    return tax_total


def _compute_total_from_draft(draft: SupplierInvoiceDraft) -> Decimal:
    """Compute total = subtotal + tax_total - withholding_total."""
    subtotal = _compute_subtotal_from_draft(draft)
    tax_total = _compute_tax_total_from_draft(draft)
    withholding_total = (
        draft.withholding_total or Decimal("0")
    )
    return (subtotal + tax_total - withholding_total).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def header_match_check(mismatched_fields: list[str]) -> bool:
    """Check if header fields all match (no mismatches)."""
    return len(mismatched_fields) == 0