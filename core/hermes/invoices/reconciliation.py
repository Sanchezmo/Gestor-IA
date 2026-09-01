"""
Dolibarr Reconciliation Service - ERP_RESULT_UNKNOWN reconciliation.

When a supplier invoice POST to Dolibarr times out or the response is
ambiguous, this service performs reconciliation to determine the actual
outcome. It queries Dolibarr for the invoice state and decides whether to:
- ADOPT: The invoice was created (accept the existing state)
- RETRY_SCHEDULED: Retry the operation
- BLOCKED_MANUAL: Require manual intervention
- ERROR: Mark as permanent failure

This service is the resolution path for ERP_RESULT_UNKNOWN state transitions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Any, Optional


class ReconciliationAction(Enum):
    """Actions resulting from reconciliation."""

    ADOPT = "adopt"          # Invoice already exists in Dolibarr; accept it
    RETRY_SCHEDULED = "retry_scheduled"  # Transient issue; retry later
    BLOCKED_MANUAL = "blocked_manual"    # Requires human intervention
    ERROR = "error"          # Permanent failure; cannot recover automatically


class DuplicateCheckDetail:
    """Details from a duplicate check during reconciliation."""

    def __init__(
        self,
        is_duplicate: bool,
        existing_invoice_id: int | None = None,
        existing_invoice_ref: str | None = None,
        existing_supplier_id: int | None = None,
        confidence: Decimal = Decimal("0"),
    ):
        self.is_duplicate = is_duplicate
        self.existing_invoice_id = existing_invoice_id
        self.existing_invoice_ref = existing_invoice_ref
        self.existing_supplier_id = existing_supplier_id
        self.confidence = confidence


class ReconciliationDetail:
    """Full reconciliation detail record."""

    def __init__(
        self,
        action: ReconciliationAction,
        reason: str,
        duplicate_check: DuplicateCheckDetail | None = None,
        DolibarrInvoiceFound: bool = False,
        DolibarrInvoiceId: int | None = None,
        DolibarrInvoiceRef: str | None = None,
        should_update_idempotency: bool = True,
    ):
        self.action = action
        self.reason = reason
        self.duplicate_check = duplicate_check
        self.DolibarrInvoiceFound = DolibarrInvoiceFound
        self.DolibarrInvoiceId = DolibarrInvoiceId
        self.DolibarrInvoiceRef = DolibarrInvoiceRef
        self.should_update_idempotency = should_update_idempotency


class ReconciliationOutcome:
    """Outcome of a reconciliation operation."""

    def __init__(
        self,
        action: ReconciliationAction,
        detail: ReconciliationDetail,
        # New fields for tracking
        invoice_id_after: int | None = None,
        supplier_id_after: int | None = None,
    ):
        self.action = action
        self.detail = detail
        self.invoice_id_after = invoice_id_after
        self.supplier_id_after = supplier_id_after


class ReconciliationEngine:
    """
    Engine that performs ERP_RESULT_UNKNOWN reconciliation.

    Given a Dolibarr supplier invoice ID that may or may not have been created,
    this engine queries Dolibarr to determine the actual state and returns
    the appropriate reconciliation action.
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
        Perform reconciliation for a potentially-duplicate or uncertain invoice.

        Query Dolibarr to find the invoice by reference or ID, check for
        duplicates, and determine the appropriate action.

        Args:
            invoice_ref: Supplier invoice reference number (ref field)
            invoice_id: Dolibarr invoice rowid ID
            supplier_id: Dolibarr supplier (socid) ID

        Returns:
            ReconciliationOutcome with the determined action and details
        """
        # Try to find the invoice by reference first
        found_by_ref = False
        dolibarr_invoice: dict[str, Any] | None = None

        if invoice_ref:
            try:
                dolibarr_invoice = self.dolibarr.get_supplier_invoice(invoice_id or 0)
                # Actually we need to search by ref - let's use list_invoices with filter
                # For now, use get_supplier_invoice if we have an ID
                if invoice_id:
                    dolibarr_invoice = self.dolibarr.get_supplier_invoice(invoice_id)
                    found_by_ref = True
            except Exception:
                dolibarr_invoice = None

        # If we have supplier_id and no invoice_id, try listing supplier invoices
        if not invoice_id and supplier_id:
            try:
                invoices = self.dolibarr.list_supplier_invoices(
                    thirdparty_id=supplier_id,
                    limit=50,
                )
                if invoices and isinstance(invoices, list) and len(invoices) > 0:
                    # Find the most recent/ matching one
                    for inv in invoices:
                        if inv.get("ref") == invoice_ref or inv.get("invoice_number") == invoice_ref:
                            dolibarr_invoice = inv
                            found_by_ref = True
                            break
            except Exception:
                pass

        # Build duplicate check detail
        duplicate_check: DuplicateCheckDetail | None = None
        if dolibarr_invoice:
            duplicate_check = DuplicateCheckDetail(
                is_duplicate=True,
                existing_invoice_id=dolibarr_invoice.get("id"),
                existing_invoice_ref=dolibarr_invoice.get("ref"),
                existing_supplier_id=dolibarr_invoice.get("socid"),
                confidence=Decimal("1"),
            )

        # Determine action based on what we found
        if not dolibarr_invoice:
            # Invoice not found in Dolibarr - this is unusual for ERP_RESULT_UNKNOWN
            # but could mean the POST truly failed
            return ReconciliationOutcome(
                action=ReconciliationAction.ERROR,
                detail=ReconciliationDetail(
                    action=ReconciliationAction.ERROR,
                    reason="Invoice not found in Dolibarr after timeout; "
                    "POST may have failed or Dolibarr is unavailable",
                    duplicate_check=duplicate_check,
                    DolibarrInvoiceFound=False,
                ),
            )

        # Invoice found - determine the appropriate action
        invoice_ref_from_dolibarr = dolibarr_invoice.get("ref") or ""
        invoice_id_from_dolibarr = dolibarr_invoice.get("id")

        # Check if the reference matches what we expected
        reference_matches = invoice_ref_from_dolibarr == invoice_ref if invoice_ref else True

        # If we found the invoice and the reference matches, ADOPT
        if reference_matches and invoice_id_from_dolibarr is not None:
            return ReconciliationOutcome(
                action=ReconciliationAction.ADOPT,
                detail=ReconciliationDetail(
                    action=ReconciliationAction.ADOPT,
                    reason="Invoice found in Dolibarr with matching reference; "
                    "accepting existing state. POST likely completed successfully.",
                    duplicate_check=duplicate_check,
                    DolibarrInvoiceFound=True,
                    DolibarrInvoiceId=int(invoice_id_from_dolibarr),
                    DolibarrInvoiceRef=str(invoice_ref_from_dolibarr),
                    should_update_idempotency=True,
                ),
                invoice_id_after=int(invoice_id_from_dolibarr),
                supplier_id_after=int(dolibarr_invoice.get("socid", 0)),
            )

        # Invoice found but reference doesn't match - this is ambiguous
        # Check if it's a different invoice for the same supplier
        if supplier_id and invoice_id_from_dolibarr is not None:
            # Same supplier, different invoice - RETRY_SCHEDULED to re-examine
            return ReconciliationOutcome(
                action=ReconciliationAction.RETRY_SCHEDULED,
                detail=ReconciliationDetail(
                    action=ReconciliationAction.RETRY_SCHEDULED,
                    reason="Invoice found in Dolibarr but reference does not match. "
                    "May be a different invoice. Retry scheduled for re-examination.",
                    duplicate_check=duplicate_check,
                    DolibarrInvoiceFound=True,
                    DolibarrInvoiceId=int(invoice_id_from_dolibarr),
                    DolibarrInvoiceRef=str(invoice_ref_from_dolibarr),
                    should_update_idempotency=False,
                ),
                invoice_id_after=int(invoice_id_from_dolibarr),
                supplier_id_after=int(dolibarr_invoice.get("socid", 0)),
            )

        # Invoice found but under different supplier or ambiguous situation
        if invoice_id_from_dolibarr is not None:
            return ReconciliationOutcome(
                action=ReconciliationAction.BLOCKED_MANUAL,
                detail=ReconciliationDetail(
                    action=ReconciliationAction.BLOCKED_MANUAL,
                    reason="Invoice found in Dolibarr but situation is ambiguous "
                    "(reference mismatch, different supplier, or unexpected state). "
                    "Requires manual review.",
                    duplicate_check=duplicate_check,
                    DolibarrInvoiceFound=True,
                    DolibarrInvoiceId=int(invoice_id_from_dolibarr),
                    DolibarrInvoiceRef=str(invoice_ref_from_dolibarr),
                    should_update_idempotency=False,
                ),
                invoice_id_after=int(invoice_id_from_dolibarr),
                supplier_id_after=int(dolibarr_invoice.get("socid", 0)),
            )

        # Fallback if we couldn't query Dolibarr at all
        return ReconciliationOutcome(
            action=ReconciliationAction.ERROR,
            detail=ReconciliationDetail(
                action=ReconciliationAction.ERROR,
                reason="Could not perform reconciliation: Dolibarr client not available "
                "or query failed. Requires manual intervention.",
                duplicate_check=None,
                DolibarrInvoiceFound=False,
            ),
        )