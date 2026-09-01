"""
Mappers for Supplier Invoice Processing - Draft to Dolibarr payload.

Converts SupplierInvoiceDraft (from extraction/normalization) into
Dolibarr API payload format for supplier invoice creation and line items.

Ported from core/integrations/dolibarr/mappers.py supplier_invoice_to_dolibarr()
adapted to the Gestor-IA domain model (SupplierInvoiceDraft, InvoiceLine, etc.).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List

from core.hermes.invoices.models import (
    InvoiceLine,
    InvoiceFieldSource,
    SupplierInvoiceDraft,
    SupplierResolutionStatus,
    ValidationStatus,
    DocumentClassification,
)


def map_supplier_invoice_draft_to_dolibarr(
    data: SupplierInvoiceDraft,
) -> dict[str, Any]:
    """
    Convert a SupplierInvoiceDraft to a Dolibarr supplier invoice payload.

    Maps the draft header fields to Dolibarr supplierinvoice API format.
    Handles supplier resolution (thirdparty_id / socid), dates, currency,
    payment terms, and notes.

    Args:
        data: SupplierInvoiceDraft with extracted and normalized invoice data

    Returns:
        Dict ready for Dolibarr supplierinvoices POST endpoint
    """
    # Build base payload from draft header
    payload: dict[str, Any] = {}

    # Supplier resolution: use socid (Dolibarr ID) if we have a Dolibarr supplier ID,
    # otherwise the caller will need to resolve/create the thirdparty first.
    # The draft may have supplier_dolibarr_id set if supplier was already found/created.
    if data.supplier_dolibarr_id is not None:
        payload["socid"] = data.supplier_dolibarr_id

    # Invoice header field mapping (our snake_case -> Dolibarr camelCase)
    header_mapping: dict[str, str] = {
        "invoice_number": "ref",
        "invoice_date": "date",
        "due_date": "date_lim_reglement",
        "currency": "fk_multicurrency",
        "payment_term_id": "fk_cond_reglement",
        "notes": "note_private",
    }

    for our_key, dolibarr_key in header_mapping.items():
        value = getattr(data, our_key, None)
        if value is not None:
            # Ensure date fields are timestamps for Dolibarr
            if dolibarr_key in ("date", "date_lim_reglement") and isinstance(value, date):
                value = int(datetime.combine(value, datetime.min.time()).timestamp())
            payload[dolibarr_key] = value

    # serie (optional reference) - use invoice_number as ref if no Dolibarr supplier ID
    if data.invoice_number and not data.supplier_dolibarr_id:
        payload["ref"] = data.invoice_number

    # Payment terms (optional)
    if data.payment_terms:
        payload["fk_cond_reglement"] = data.payment_terms

    # Currency (default EUR)
    if data.currency and data.currency != "EUR":
        payload["fk_multicurrency"] = data.currency

    # Notes
    if data.notes:
        payload["note_private"] = data.notes

    # ============================================================
    # Map lines (InvoiceLine -> Dolibarr supplierinvoice lines)
    # ============================================================
    lines_payload: list[dict[str, Any]] = []

    for line in data.lines:
        line_payload: dict[str, Any] = {
            "label": line.description,
            "qty": float(line.quantity) if line.quantity else 1,
            "price_ht": float(line.unit_price) if line.unit_price else Decimal("0"),
            "tva_tx": float(line.vat_rate) if line.vat_rate else Decimal("0"),
            "remise_percent": float(line.discount_percent) if line.discount_percent else Decimal("0"),
        }

        # Product reference if available
        if line.product_ref:
            line_payload["fk_product"] = line.product_ref

        lines_payload.append(line_payload)

    if lines_payload:
        payload["lines"] = lines_payload

    return payload


def map_dolibarr_supplier_invoice_to_draft(data: dict[str, Any]) -> SupplierInvoiceDraft:
    """
    Convert a Dolibarr supplier invoice response to a SupplierInvoiceDraft.

    Inverse of map_supplier_invoice_draft_to_dolibarr.

    Args:
        data: Dolibarr supplier invoice API response

    Returns:
        SupplierInvoiceDraft with normalized fields
    """
    from datetime import datetime as _dt

    # Normalize thirdparty_id from Dolibarr socid
    thirdparty_id = data.get("socid") or data.get("fk_soc")

    # Map dates from timestamp to date
    date_val = _dt.fromtimestamp(data.get("date", 0)).date() if data.get("date") else None
    due_date_val = _dt.fromtimestamp(data.get("date_lim_reglement", 0)).date() if data.get("date_lim_reglement") else None

    # Map monetary values using _to_decimal pattern
    def _to_decimal_safe(value: Any) -> Decimal:
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

    subtotal = _to_decimal_safe(data.get("total_ht"))
    tax_total = _to_decimal_safe(data.get("total_tva"))
    total = _to_decimal_safe(data.get("total_ttc"))
    paid_amount = _to_decimal_safe(data.get("total_paid") or data.get("paid_amount") or data.get("amount_paid"))

    return SupplierInvoiceDraft(
        # Document identification
        document_hash=data.get("document_hash", ""),
        document_filename=data.get("document_filename", ""),
        document_mime_type=data.get("document_mime_type", ""),
        document_size_bytes=data.get("document_size_bytes", 0),
        page_count=data.get("page_count", 1),

        # Classification
        classification=data.get("classification", DocumentClassification.UNKNOWN)
        if not hasattr(data, "classification")
        else data.classification,
        classification_confidence=data.get(
            "classification_confidence", Decimal("0")
        )
        if not hasattr(data, "classification_confidence")
        else data.classification_confidence,
        classification_signals=data.get("classification_signals", [])
        if not hasattr(data, "classification_signals")
        else data.classification_signals,

        # Supplier (extracted)
        supplier=data.get("supplier") if not isinstance(data, SupplierInvoiceDraft) else None,

        # Invoice header
        invoice_number=data.get("ref") or data.get("invoice_number"),
        invoice_date=date_val,
        due_date=due_date_val,
        currency=data.get("currency", "EUR"),
        payment_terms=data.get("fk_cond_reglement"),
        notes=data.get("note_private"),

        # Lines will be populated separately from Dolibarr line items
        lines=data.get("lines", []),

        # Tax breakdown
        tax_breakdown=data.get("tax_breakdown", []),

        # Withholding breakdown
        withholding_breakdown=data.get("withholding_breakdown", []),

        # Totals
        subtotal=subtotal,
        subtotal_source=InvoiceFieldSource.KNOWN,
        tax_total=tax_total,
        tax_total_source=InvoiceFieldSource.KNOWN,
        withholding_total=data.get("withholding_total"),
        withholding_total_source=InvoiceFieldSource.KNOWN,
        total=total,
        total_source=InvoiceFieldSource.KNOWN,

        # Supplier resolution
        supplier_resolution_status=SupplierResolutionStatus.FOUND,
        supplier_dolibarr_id=data.get("supplier_dolibarr_id"),
        supplier_candidates=[],

        # Validation
        validation_status=ValidationStatus.VALID,
        validation_errors=[],
        validation_warnings=[],

        # Extraction metadata
        extraction_confidence=data.get("extraction_confidence", Decimal("0")),
        extraction_model=data.get("extraction_model"),
        extraction_raw_text_chars=data.get("extraction_raw_text_chars", 0),
        inference_count=data.get("inference_count", 0),

        # Processing metadata
        instance_id=data.get("instance_id", ""),
        received_at=data.get("received_at", ""),
        correlation_id=data.get("correlation_id") if data.get("correlation_id") else None,  # type: ignore[arg-type]
    )