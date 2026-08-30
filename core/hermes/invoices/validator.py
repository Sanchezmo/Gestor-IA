"""
Deterministic Supplier Invoice Validator.

Ported from Transvega Animal:
- agents/invoice_processing/agent.py: _deterministic_checks, _normalize_tax_data

Adapted for Gestor-IA:
- Pure functions, no side effects
- Decimal-only calculations
- Returns structured ValidationResult
- Separates HARD ERRORS from REVIEW WARNINGS
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from .models import (
    SupplierInvoiceDraft,
    ValidationResult,
    ValidationStatus,
    InvoiceLine,
    TaxBreakdownItem,
    WithholdingBreakdownItem,
    InvoiceFieldSource,
)


# =========================================================================
# TAX DATA NORMALIZATION (ported from Transvega _normalize_tax_data)
# =========================================================================

def normalize_tax_data(draft: SupplierInvoiceDraft) -> SupplierInvoiceDraft:
    """
    Normalize tax data before validation.

    Ensures that:
    1. If tax items have rate but no amount, compute amount from line-level data
    2. If line-level vat_rate exists but tax_breakdown is empty, create from lines
    3. Ensure tax items have 'amount' and 'rate' fields for validation
    4. Validate tax_total consistency with computed tax amounts

    This runs BEFORE validation to normalize the data structure.
    """
    # Work with mutable copies
    lines = list(draft.lines)
    tax_breakdown = list(draft.tax_breakdown)
    withholding_breakdown = list(draft.withholding_breakdown)

    # --- Step 1: Compute tax amounts from line items (group by vat_rate) ---
    tax_by_rate: dict[Decimal, Decimal] = defaultdict(Decimal)  # rate -> total tax amount
    base_by_rate: dict[Decimal, Decimal] = defaultdict(Decimal)  # rate -> total tax base

    for line in lines:
        # Line already has computed line_total_excl_tax and vat_amount
        if line.vat_rate > 0 and line.line_total_excl_tax is not None and line.vat_amount is not None:
            tax_by_rate[line.vat_rate] += line.vat_amount
            base_by_rate[line.vat_rate] += line.line_total_excl_tax

    # --- Step 2: Normalize existing tax_breakdown ---
    normalized_tax_breakdown = []

    if tax_breakdown:
        for tax in tax_breakdown:
            rate = tax.rate
            amount = tax.amount
            base = tax.base

            # If tax has rate but no amount (or amount is 0), try to compute from line data
            if rate is not None and rate > 0 and (amount is None or amount == 0):
                # Prefer base from tax item, then from aggregated line data
                if base is None and rate in base_by_rate:
                    base = base_by_rate[rate]

                if base is not None:
                    amount = (base * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                elif rate in tax_by_rate:
                    # Fallback: use aggregated tax amount from lines
                    amount = tax_by_rate[rate]
                elif draft.tax_total is not None and draft.tax_total > 0 and len(tax_breakdown) == 1:
                    # Single tax item and we have tax_total: use it
                    amount = draft.tax_total
                elif len(tax_breakdown) == 1 and draft.subtotal is not None and draft.subtotal > 0:
                    # Single tax rate and we have subtotal: use subtotal as base
                    amount = (draft.subtotal * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            # Ensure amount field exists (default to 0.0 if still missing)
            if amount is None:
                amount = Decimal("0")

            # Ensure base field exists
            if base is None and rate in base_by_rate:
                base = base_by_rate[rate]

            normalized_tax_breakdown.append(TaxBreakdownItem(
                rate=rate,
                base=base or Decimal("0"),
                amount=amount,
                source=tax.source,
            ))

    # --- Step 3: If tax_breakdown is empty but we have line-level tax data, create from lines ---
    elif lines and tax_by_rate:
        normalized_tax_breakdown = [
            TaxBreakdownItem(
                rate=rate,
                base=base_by_rate[rate],
                amount=amount,
                source=InvoiceFieldSource.INFERRED,
            )
            for rate, amount in tax_by_rate.items()
        ]

    # --- Step 4: If still no tax_breakdown but tax_total exists, create from tax_total ---
    elif draft.tax_total is not None and draft.tax_total > 0:
        # Try to infer rate from lines if possible
        inferred_rate = Decimal("0")
        if lines:
            rates = {line.vat_rate for line in lines if line.vat_rate > 0}
            if len(rates) == 1:
                inferred_rate = rates.pop()

        normalized_tax_breakdown = [
            TaxBreakdownItem(
                rate=inferred_rate,
                base=draft.subtotal or Decimal("0"),
                amount=draft.tax_total,
                source=InvoiceFieldSource.INFERRED if inferred_rate == 0 else InvoiceFieldSource.KNOWN,
            )
        ]

    # --- Step 5: Normalize withholding breakdown ---
    normalized_withholding = []
    for wh in withholding_breakdown:
        rate = wh.rate
        amount = wh.amount
        base = wh.base

        if rate is not None and rate > 0 and (amount is None or amount == 0):
            if base is None:
                # Withholding base is typically the taxable base (subtotal)
                base = draft.subtotal or Decimal("0")
            amount = (base * rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if amount is None:
            amount = Decimal("0")

        normalized_withholding.append(WithholdingBreakdownItem(
            rate=rate,
            base=base or Decimal("0"),
            amount=amount,
            source=wh.source,
        ))

    # --- Step 6: Ensure tax_total consistency with computed tax amounts ---
    computed_tax_total = sum(t.amount for t in normalized_tax_breakdown)
    computed_withholding_total = sum(w.amount for w in normalized_withholding)

    # Only update tax_total if it was missing or zero, or if it matches closely
    final_tax_total = draft.tax_total
    if draft.tax_total is None or draft.tax_total == 0 or abs(draft.tax_total - computed_tax_total) < Decimal("0.02"):
        final_tax_total = computed_tax_total

    final_withholding_total = draft.withholding_total
    if draft.withholding_total is None or draft.withholding_total == 0 or abs(draft.withholding_total - computed_withholding_total) < Decimal("0.02"):
        final_withholding_total = computed_withholding_total

    # Recompute subtotal from lines if missing
    final_subtotal = draft.subtotal
    if final_subtotal is None and lines:
        final_subtotal = sum(line.line_total_excl_tax or Decimal("0") for line in lines)
        if final_subtotal is not None:
            final_subtotal = final_subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Recompute total if missing
    final_total = draft.total
    if final_total is None and final_subtotal is not None and final_tax_total is not None:
        final_total = final_subtotal + final_tax_total - (final_withholding_total or Decimal("0"))
        final_total = final_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    # Return new draft with normalized data
    return SupplierInvoiceDraft(
        document_hash=draft.document_hash,
        document_filename=draft.document_filename,
        document_mime_type=draft.document_mime_type,
        document_size_bytes=draft.document_size_bytes,
        page_count=draft.page_count,
        classification=draft.classification,
        classification_confidence=draft.classification_confidence,
        classification_signals=draft.classification_signals,
        supplier=draft.supplier,
        invoice_number=draft.invoice_number,
        invoice_number_source=draft.invoice_number_source,
        invoice_date=draft.invoice_date,
        invoice_date_source=draft.invoice_date_source,
        due_date=draft.due_date,
        due_date_source=draft.due_date_source,
        currency=draft.currency,
        payment_terms=draft.payment_terms,
        payment_method=draft.payment_method,
        notes=draft.notes,
        lines=lines,
        tax_breakdown=normalized_tax_breakdown,
        withholding_breakdown=normalized_withholding,
        subtotal=final_subtotal,
        subtotal_source=draft.subtotal_source if draft.subtotal is not None else InvoiceFieldSource.INFERRED,
        tax_total=final_tax_total,
        tax_total_source=draft.tax_total_source if draft.tax_total is not None else InvoiceFieldSource.INFERRED,
        withholding_total=final_withholding_total,
        withholding_total_source=draft.withholding_total_source if draft.withholding_total is not None else InvoiceFieldSource.INFERRED,
        total=final_total,
        total_source=draft.total_source if draft.total is not None else InvoiceFieldSource.INFERRED,
        supplier_resolution_status=draft.supplier_resolution_status,
        supplier_dolibarr_id=draft.supplier_dolibarr_id,
        supplier_candidates=draft.supplier_candidates,
        validation_status=draft.validation_status,
        validation_errors=draft.validation_errors,
        validation_warnings=draft.validation_warnings,
        extraction_confidence=draft.extraction_confidence,
        extraction_model=draft.extraction_model,
        extraction_raw_text_chars=draft.extraction_raw_text_chars,
        inference_count=draft.inference_count,
        instance_id=draft.instance_id,
        received_at=draft.received_at,
        correlation_id=draft.correlation_id,
    )


# =========================================================================
# DETERMINISTIC VALIDATION (ported from Transvega _deterministic_checks)
# =========================================================================

# Hard errors that block confirmation
HARD_ERROR_CODES = {
    "invoice_total_mismatch",
    "line_subtotal_mismatch",
    "currency_unsupported",
    "date_missing",
    "no_lines",
    "tax_breakdown_incomplete",
}

# Review warnings that allow confirmation but flag for attention
REVIEW_WARNING_CODES = {
    "tax_breakdown_missing",
    "tax_rate_missing",
    "withholding_breakdown_missing",
    "supplier_tax_id_missing",
}


def validate_invoice(draft: SupplierInvoiceDraft) -> ValidationResult:
    """
    Perform deterministic validations on a supplier invoice draft.

    Returns ValidationResult with:
    - status: VALID, REVIEW_REQUIRED, or INVALID
    - errors: list of hard errors (block confirmation)
    - warnings: list of review warnings (flag for attention)
    """
    errors = []
    warnings = []

    # =========================================================================
    # STRUCTURAL CHECKS (run on original draft, before normalization)
    # These check what was actually extracted, not what we can infer
    # =========================================================================

    # --- H) Check lines exist (structural) ---
    if not draft.lines:
        errors.append({
            "code": "no_lines",
            "check": "lines",
            "message": "La factura debe tener al menos una línea",
        })

    # --- F) Check date (structural) ---
    if not draft.invoice_date:
        errors.append({
            "code": "date_missing",
            "check": "date",
            "message": "Fecha de factura obligatoria",
        })

    # --- E) Check currency (structural) ---
    if draft.currency.upper() != "EUR":
        errors.append({
            "code": "currency_unsupported",
            "check": "currency",
            "expected": "EUR",
            "actual": draft.currency,
            "message": f"Moneda {draft.currency} no soportada (solo EUR)",
        })

    # --- G) Check supplier tax ID (structural) ---
    if not draft.has_supplier():
        warnings.append({
            "code": "supplier_tax_id_missing",
            "check": "supplier",
            "message": "Proveedor sin CIF/NIF identificado",
        })

    # --- Tax breakdown missing check (structural - based on what was extracted) ---
    # Check if tax_breakdown was originally missing AND tax_total exists
    if not draft.tax_breakdown and (draft.tax_total or Decimal("0")) > 0:
        warnings.append({
            "code": "tax_breakdown_missing",
            "check": "tax_breakdown",
            "tax_total": str(draft.tax_total.quantize(Decimal("0.01"))) if draft.tax_total else "0.00",
            "breakdown_total": "0.00",
            "severity": "warning",
            "message": "Falta desglose de IVA por tipos (el total IVA sí existe)",
        })

    # =========================================================================
    # NORMALIZE TAX DATA (fills in computable fields)
    # =========================================================================
    normalized = normalize_tax_data(draft)

    # =========================================================================
    # MATHEMATICAL CHECKS (run on normalized data)
    # =========================================================================

    # --- A) Check line net amounts sum to subtotal ---
    line_net_sum = Decimal("0")
    for line in normalized.lines:
        if line.line_total_excl_tax is not None:
            line_net_sum += line.line_total_excl_tax

    if normalized.subtotal is not None and abs(line_net_sum - normalized.subtotal) > Decimal("0.01"):
        errors.append({
            "code": "line_subtotal_mismatch",
            "check": "line_subtotal",
            "expected": str(normalized.subtotal.quantize(Decimal("0.01"))),
            "actual": str(line_net_sum.quantize(Decimal("0.01"))),
            "message": f"La suma de bases de líneas ({line_net_sum:.2f}) no coincide con subtotal ({normalized.subtotal:.2f})",
        })

    # --- B) Check global total: subtotal + tax_total - withholding = total ---
    withholding_total = normalized.withholding_total or Decimal("0")

    if normalized.subtotal is not None and normalized.tax_total is not None and normalized.total is not None:
        expected_total = normalized.subtotal + normalized.tax_total - withholding_total
        if abs(expected_total - normalized.total) > Decimal("0.01"):
            errors.append({
                "code": "invoice_total_mismatch",
                "check": "invoice_total",
                "subtotal": str(normalized.subtotal.quantize(Decimal("0.01"))),
                "tax_total": str(normalized.tax_total.quantize(Decimal("0.01"))),
                "withholding_total": str(withholding_total.quantize(Decimal("0.01"))),
                "expected": str(expected_total.quantize(Decimal("0.01"))),
                "actual": str(normalized.total.quantize(Decimal("0.01"))),
                "message": f"Total no cuadra: base {normalized.subtotal:.2f} + IVA {normalized.tax_total:.2f} - Ret. {withholding_total:.2f} = {expected_total:.2f}, pero total es {normalized.total:.2f}",
            })

    # --- C) Check tax breakdown consistency ---
    if normalized.tax_breakdown:
        tax_breakdown_sum = sum(t.amount for t in normalized.tax_breakdown)
        if normalized.tax_total is not None and abs(tax_breakdown_sum - normalized.tax_total) > Decimal("0.01"):
            errors.append({
                "code": "tax_breakdown_incomplete",
                "check": "tax_breakdown",
                "tax_total": str(normalized.tax_total.quantize(Decimal("0.01"))),
                "breakdown_total": str(tax_breakdown_sum.quantize(Decimal("0.01"))),
                "message": f"Desglose IVA suma {tax_breakdown_sum:.2f} pero tax_total es {normalized.tax_total:.2f}",
            })

        # Check for invalid/missing tax rates when tax_total > 0
        if (normalized.tax_total or Decimal("0")) > 0:
            invalid_rates = [t for t in normalized.tax_breakdown if t.rate is None or t.rate <= 0]
            if invalid_rates:
                warnings.append({
                    "code": "tax_rate_missing",
                    "check": "tax_breakdown",
                    "tax_total": str(normalized.tax_total.quantize(Decimal("0.01"))),
                    "invalid_count": len(invalid_rates),
                    "message": f"{len(invalid_rates)} tipo(s) de IVA sin rate válido",
                })
    else:
        # No taxes breakdown at all after normalization - structural check already caught missing
        pass

    # --- D) Check withholding breakdown if applicable ---
    if (normalized.withholding_total or Decimal("0")) > 0 and not normalized.withholding_breakdown:
        warnings.append({
            "code": "withholding_breakdown_missing",
            "check": "withholding_breakdown",
            "withholding_total": str(normalized.withholding_total.quantize(Decimal("0.01"))),
            "message": "Hay retenciones pero sin desglose",
        })

    # Classify errors
    hard_errors = [e for e in errors if e.get("code") in HARD_ERROR_CODES]
    review_warnings = [e for e in errors if e.get("code") in REVIEW_WARNING_CODES] + warnings

    # Determine overall status
    if hard_errors:
        status = ValidationStatus.INVALID
    elif review_warnings:
        status = ValidationStatus.REVIEW_REQUIRED
    else:
        status = ValidationStatus.VALID

    return ValidationResult(
        status=status,
        errors=hard_errors,
        warnings=review_warnings,
    )


# =========================================================================
# INFERENCE HELPERS (for when extraction provides partial data)
# =========================================================================

def infer_missing_totals(draft: SupplierInvoiceDraft) -> SupplierInvoiceDraft:
    """
    Infer missing totals when mathematically safe.

    Safe inferences:
    - If subtotal and total known -> tax_total = total - subtotal (if no withholding)
    - If subtotal and tax_total known -> total = subtotal + tax_total - withholding
    - If total and tax_total known -> subtotal = total - tax_total + withholding

    NOT safe:
    - Inferring tax breakdown by rate from only global totals
    - Inferring withholding without explicit data
    """
    subtotal = draft.subtotal
    tax_total = draft.tax_total
    withholding_total = draft.withholding_total or Decimal("0")
    total = draft.total

    updates = {}
    new_inference_count = draft.inference_count

    # Case 1: base + total -> tax (no withholding or withholding known)
    if subtotal is not None and total is not None and tax_total is None:
        inferred_tax = total - subtotal + withholding_total
        if inferred_tax >= 0:
            updates["tax_total"] = inferred_tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            updates["tax_total_source"] = InvoiceFieldSource.INFERRED
            new_inference_count += 1

    # Case 2: base + tax -> total
    if subtotal is not None and tax_total is not None and total is None:
        inferred_total = subtotal + tax_total - withholding_total
        updates["total"] = inferred_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        updates["total_source"] = InvoiceFieldSource.INFERRED
        new_inference_count += 1

    # Case 3: total + tax -> base
    if total is not None and tax_total is not None and subtotal is None:
        inferred_base = total - tax_total + withholding_total
        if inferred_base >= 0:
            updates["subtotal"] = inferred_base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            updates["subtotal_source"] = InvoiceFieldSource.INFERRED
            new_inference_count += 1

    if updates:
        return SupplierInvoiceDraft(
            document_hash=draft.document_hash,
            document_filename=draft.document_filename,
            document_mime_type=draft.document_mime_type,
            document_size_bytes=draft.document_size_bytes,
            page_count=draft.page_count,
            classification=draft.classification,
            classification_confidence=draft.classification_confidence,
            classification_signals=draft.classification_signals,
            supplier=draft.supplier,
            invoice_number=draft.invoice_number,
            invoice_number_source=draft.invoice_number_source,
            invoice_date=draft.invoice_date,
            invoice_date_source=draft.invoice_date_source,
            due_date=draft.due_date,
            due_date_source=draft.due_date_source,
            currency=draft.currency,
            payment_terms=draft.payment_terms,
            payment_method=draft.payment_method,
            notes=draft.notes,
            lines=draft.lines,
            tax_breakdown=draft.tax_breakdown,
            withholding_breakdown=draft.withholding_breakdown,
            subtotal=updates.get("subtotal", draft.subtotal),
            subtotal_source=updates.get("subtotal_source", draft.subtotal_source),
            tax_total=updates.get("tax_total", draft.tax_total),
            tax_total_source=updates.get("tax_total_source", draft.tax_total_source),
            withholding_total=draft.withholding_total,
            withholding_total_source=draft.withholding_total_source,
            total=updates.get("total", draft.total),
            total_source=updates.get("total_source", draft.total_source),
            supplier_resolution_status=draft.supplier_resolution_status,
            supplier_dolibarr_id=draft.supplier_dolibarr_id,
            supplier_candidates=draft.supplier_candidates,
            validation_status=draft.validation_status,
            validation_errors=draft.validation_errors,
            validation_warnings=draft.validation_warnings,
            extraction_confidence=draft.extraction_confidence,
            extraction_model=draft.extraction_model,
            extraction_raw_text_chars=draft.extraction_raw_text_chars,
            inference_count=new_inference_count,
            instance_id=draft.instance_id,
            received_at=draft.received_at,
            correlation_id=draft.correlation_id,
        )

    return draft