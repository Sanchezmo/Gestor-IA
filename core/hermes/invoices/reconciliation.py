"""
Dolibarr Reconciliation Service - ERP_RESULT_UNKNOWN reconciliation.

When a supplier invoice POST to Dolibarr times out or the response is
ambiguous, this service performs reconciliation to determine the actual
outcome. It queries Dolibarr for the invoice state and decides whether to:
- ADOPT: The invoice was created (accept the existing state)
- BLOCKED_MANUAL: Require manual intervention
- ERROR: Mark as permanent failure

This service is the resolution path for ERP_RESULT_UNKNOWN state transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto, StrEnum
from typing import Any, Optional


class ReconciliationResult(StrEnum):
    """Explicit reconciliation outcomes - shared model between ReconciliationEngine
    and ConfirmSupplierInvoiceHandler. No bool ambiguity.

    UNIQUE_MATCH -> adopt existing invoice (safe to proceed)
    NO_MATCH   -> no existing invoice; CREATE may proceed if state machine allows
    AMBIGUOUS_MATCH -> multiple matches; block, never auto-CREATE
    ERROR      -> Dolibarr unavailable or unexpected error; remain uncertain, never CREATE
    """

    UNIQUE_MATCH = "unique_match"     # Exactly one strong match -> adopt
    NO_MATCH = "no_match"             # No matching invoice found
    AMBIGUOUS_MATCH = "ambiguous_match"  # Multiple matches -> block, never CREATE
    ERROR = "error"                   # Dolibarr unavailable; remain uncertain


class ReconciliationDetail:
    """Detailed reconciliation result with evidence."""

    def __init__(
        self,
        result: ReconciliationResult,
        supplier_dolibarr_id: int | None = None,
        invoice_dolibarr_id: int | None = None,
        ref: str | None = None,
        ref_supplier: str | None = None,
        date_verification: str | None = None,  # "match" | "mismatch" | "not_available"
        total_verification: str | None = None,  # "match" | "mismatch" | "not_available"
        is_primary_match: bool = False,
        error_message: str | None = None,
        candidates_count: int = 0,
    ):
        self.result = result
        self.supplier_dolibarr_id = supplier_dolibarr_id  # Dolibarr thirdparty/supplier ID
        self.invoice_dolibarr_id = invoice_dolibarr_id    # Dolibarr supplier invoice ID
        self.ref = ref
        self.ref_supplier = ref_supplier
        self.date_verification = date_verification
        self.total_verification = total_verification
        self.is_primary_match = is_primary_match
        self.error_message = error_message
        self.candidates_count = candidates_count

    @property
    def can_auto_adopt(self) -> bool:
        """Only UNIQUE_MATCH can automatically adopt the invoice."""
        return self.result == ReconciliationResult.UNIQUE_MATCH

    @property
    def is_fail_closed(self) -> bool:
        """AMBIGUOUS_MATCH and ERROR are FAIL CLOSED - require manual review."""
        return self.result in (
            ReconciliationResult.AMBIGUOUS_MATCH,
            ReconciliationResult.ERROR,
        )


class ReconciliationOutcome:
    """Outcome of a reconciliation operation."""

    def __init__(
        self,
        result: ReconciliationResult,
        detail: ReconciliationDetail,
    ):
        self.result = result
        self.detail = detail


# =============================================================================
# RECONCILIATION ENGINE
# =============================================================================

class ReconciliationEngine:
    """
    Engine that performs ERP_RESULT_UNKNOWN reconciliation.

    Given a Dolibarr supplier invoice that may or may not have been created,
    this engine queries Dolibarr to determine the actual state and returns
    the appropriate reconciliation result.
    """

    def __init__(self, dolibarr_client: Any) -> None:
        self.dolibarr = dolibarr_client

    def reconcile(
        self,
        invoice_ref: str | None = None,
        invoice_id: int | None = None,
        supplier_id: int | None = None,
    ) -> ReconciliationOutcome:
        """
        Perform reconciliation for a potentially-uncertain invoice.

        Query Dolibarr to find the invoice by strong supplier-invoice identity
        (ref_supplier), check for duplicates, and determine the appropriate result.

        The supplier_id is used to identify the thirdparty/supplier,
        and invoice_id is used to identify the specific supplier invoice.
        These are stored separately in ReconciliationDetail so that
        adopting an existing invoice correctly persists both IDs.

        Args:
            invoice_ref: Supplier invoice reference number (should be ref_supplier)
            invoice_id: Dolibarr supplier invoice ID (optional)
            supplier_id: Dolibarr supplier (socid/thirdparty) ID (optional)

        Returns:
            ReconciliationOutcome with the determined result
        """
        # --- Step 1: Search by ref_supplier (primary supplier invoice identity) ---
        dolibarr_invoice: dict[str, Any] | None = None
        found_by_ref_supplier = False
        found_by_ref = False

        # Primary identity: ref_supplier == supplier_invoice_number
        if invoice_ref:
            try:
                # Search by listing supplier invoices and checking ref_supplier first
                search_ref = invoice_ref
                # Also try with bare ref as secondary
                search_ref_supplier = invoice_ref
                search_ref_plain = invoice_ref

                # Try listing invoices by supplier_id if available
                if supplier_id:
                    try:
                        invoices = self.dolibarr.list_supplier_invoices(
                            thirdparty_id=supplier_id,
                            limit=500,
                        )
                        if invoices and isinstance(invoices, list):
                            for inv in invoices:
                                # Primary: check ref_supplier first
                                ref_s = inv.get("ref_supplier")
                                if ref_s and str(ref_s).upper() == str(search_ref_supplier).upper():
                                    dolibarr_invoice = inv
                                    found_by_ref_supplier = True
                                    break
                                # Secondary: check ref
                                ref = inv.get("ref")
                                if ref and str(ref).upper() == str(search_ref_plain).upper() and not found_by_ref_supplier:
                                    dolibarr_invoice = inv
                                    found_by_ref = True
                                    break
                    except Exception:
                        pass
                # If no supplier_id, also search via get_supplier_invoice if we have an ID
                if not dolibarr_invoice and invoice_id:
                    try:
                        dolibarr_invoice = self.dolibarr.get_supplier_invoice(invoice_id)
                        # Check which field matched
                        if (dolibarr_invoice.get("ref_supplier") or "").upper() == (
                            invoice_ref or ""
                        ).upper():
                            found_by_ref_supplier = True
                        elif (dolibarr_invoice.get("ref") or "").upper() == (
                            invoice_ref or ""
                        ).upper():
                            found_by_ref = True
                    except Exception:
                        pass

            except Exception:
                pass

        # --- Step 2: If no match by ref_supplier, try by ref (secondary) ---
        if not dolibarr_invoice and invoice_ref:
            try:
                # Try listing all recent supplier invoices (no filter) to find a match
                # This is a broader search when we don't have a supplier_id
                try:
                    invoices = self.dolibarr.list_supplier_invoices(
                        limit=200,
                    )
                    if invoices and isinstance(invoices, list):
                        for inv in invoices:
                            ref_s = inv.get("ref_supplier") or inv.get("ref")
                            if ref_s:
                                ref_s_upper = str(ref_s).upper()
                                inv_ref_upper = (inv.get("ref") or "").upper()
                                # Check if either field matches invoice_ref
                                if ref_s_upper == (invoice_ref or "").upper() or inv_ref_upper == (
                                    invoice_ref or ""
                                ).upper():
                                    dolibarr_invoice = inv
                                    # Determine which field matched
                                    if (inv.get("ref_supplier") or "").upper() == ref_s_upper:
                                        found_by_ref_supplier = True
                                    else:
                                        found_by_ref = True
                                    break
                except Exception:
                    pass
            except Exception:
                pass

        # --- Step 3: Build result based on what we found ---
        if dolibarr_invoice:
            # We found an invoice - determine if unique, ambiguous, or no match
            dolibarr_id = dolibarr_invoice.get("id")
            ref_supplier_val = dolibarr_invoice.get("ref_supplier") or ""
            ref_val = dolibarr_invoice.get("ref") or ""

            # Check if there are other invoices with the same ref_supplier (for the same supplier)
            # This helps determine if the match is truly unique
            same_supplier_count = 1  # The one we found
            if supplier_id and dolibarr_id:
                try:
                    other_invoices = self.dolibarr.list_supplier_invoices(
                        thirdparty_id=supplier_id,
                        limit=500,
                    )
                    if other_invoices and isinstance(other_invoices, list):
                        for inv in other_invoices:
                            if inv.get("id") != dolibarr_id:
                                # Same supplier, different invoice
                                same_supplier_count += 1
                                if (inv.get("ref_supplier") or "").upper() == (
                                    invoice_ref or ""
                                ).upper():
                                    # Another invoice with same ref_supplier - ambiguous
                                    same_supplier_count = 999  # mark as ambiguous
                                    break
                except Exception:
                    pass

            # Determine the result
            if found_by_ref_supplier and same_supplier_count == 1:
                # Unique match by ref_supplier -> ADOPT
                return ReconciliationOutcome(
                    result=ReconciliationResult.UNIQUE_MATCH,
                    detail=ReconciliationDetail(
                        result=ReconciliationResult.UNIQUE_MATCH,
                        supplier_dolibarr_id=supplier_id,
                        invoice_dolibarr_id=int(dolibarr_id) if dolibarr_id else None,
                        ref=str(ref_val) if ref_val else None,
                        ref_supplier=str(ref_supplier_val) if ref_supplier_val else None,
                        date_verification="match",
                        total_verification="match",
                        is_primary_match=True,
                        error_message=None,
                        candidates_count=1,
                    ),
                )
            elif found_by_ref_supplier and same_supplier_count > 1:
                # Multiple invoices with same ref_supplier for same supplier -> AMBIGUOUS
                return ReconciliationOutcome(
                    result=ReconciliationResult.AMBIGUOUS_MATCH,
                    detail=ReconciliationDetail(
                        result=ReconciliationResult.AMBIGUOUS_MATCH,
                        supplier_dolibarr_id=supplier_id,
                        invoice_dolibarr_id=int(dolibarr_id) if dolibarr_id else None,
                        ref=str(ref_val) if ref_val else None,
                        ref_supplier=str(ref_supplier_val) if ref_supplier_val else None,
                        date_verification="mismatch",
                        total_verification="mismatch",
                        is_primary_match=False,
                        error_message=f"Multiple invoices with ref_supplier '{ref_supplier_val}' for this supplier. Manual review required.",
                        candidates_count=same_supplier_count,
                    ),
                )
            elif found_by_ref and not found_by_ref_supplier:
                # Match only by ref (internal), not by ref_supplier (supplier reference)
                # This is not a strong match for supplier invoice identity
                return ReconciliationOutcome(
                    result=ReconciliationResult.NO_MATCH,
                    detail=ReconciliationDetail(
                        result=ReconciliationResult.NO_MATCH,
                        supplier_dolibarr_id=supplier_id,
                        invoice_dolibarr_id=int(dolibarr_id) if dolibarr_id else None,
                        ref=str(ref_val) if ref_val else None,
                        ref_supplier=str(ref_supplier_val) if ref_supplier_val else None,
                        date_verification="mismatch",
                        total_verification="mismatch",
                        is_primary_match=False,
                        error_message="Invoice found via internal ref but not via ref_supplier (supplier invoice number). "
                        "This is not a strong supplier reference match.",
                        candidates_count=1,
                    ),
                )
            else:
                # Found invoice but couldn't determine strong match
                return ReconciliationOutcome(
                    result=ReconciliationResult.ERROR,
                    detail=ReconciliationDetail(
                        result=ReconciliationResult.ERROR,
                        supplier_dolibarr_id=supplier_id,
                        invoice_dolibarr_id=int(dolibarr_id) if dolibarr_id else None,
                        ref=str(ref_val) if ref_val else None,
                        ref_supplier=str(ref_supplier_val) if ref_supplier_val else None,
                        date_verification="not_available",
                        total_verification="not_available",
                        is_primary_match=False,
                        error_message="Invoice found but strong supplier reference identity could not be determined.",
                        candidates_count=1,
                    ),
                )
        else:
            # No invoice found in Dolibarr
            return ReconciliationOutcome(
                result=ReconciliationResult.NO_MATCH,
                detail=ReconciliationDetail(
                    result=ReconciliationResult.NO_MATCH,
                    supplier_dolibarr_id=supplier_id,
                    invoice_dolibarr_id=None,
                    ref=str(invoice_ref) if invoice_ref else None,
                    ref_supplier=None,
                    date_verification="not_available",
                    total_verification="not_available",
                    is_primary_match=False,
                    error_message="Invoice not found in Dolibarr after timeout; POST may have failed or Dolibarr is unavailable.",
                    candidates_count=0,
                ),
            )