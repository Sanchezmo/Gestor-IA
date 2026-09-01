"""
Supplier Invoice Creator - Write-phase supplier resolution (create/enable).

Handles the supplier resolution outcomes from the confirmation boundary:
- FOUND_SUPPLIER: Supplier exists in Dolibarr, use existing ID
- FOUND_NOT_SUPPLIER (enable): Thirdparty exists but not marked as supplier;
  enable supplier flag
- NOT_FOUND: No supplier exists; create new thirdparty with supplier flag
- AMBIGUOUS: Multiple candidates; block and require manual resolution

This is the supplier resolution step that runs after user confirmation
(PENDING_CONFIRMATION -> CONFIRMING) and before invoice creation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional, Union

from core.hermes.identity import UserContext
from core.hermes.invoices.models import (
    InvoiceLine,
    SupplierInfo,
    SupplierResolutionStatus,
    SupplierResolutionStatus as SRStatus,
)
from core.hermes.invoices.mapper import map_supplier_invoice_draft_to_dolibarr


SupplierResolutionOutcome = Union[
    "SupplierFound",
    "SupplierNotFound",
    "SupplierEnable",
    "SupplierAmbiguous",
]


@dataclass(frozen=True, slots=True)
class SupplierFound:
    """Supplier found in Dolibarr (already exists as supplier)."""

    supplier_dolibarr_id: int
    status: SupplierResolutionStatus = SupplierResolutionStatus.FOUND
    supplier_data: dict[str, Any] | None = None
    candidates: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class SupplierNotFound:
    """Supplier not found in Dolibarr at all."""

    reason: str = "No thirdparty matching the supplier query found in Dolibarr"
    suggested_action: str = "create_new"
    status: SupplierResolutionStatus = SupplierResolutionStatus.NOT_FOUND


@dataclass(frozen=True, slots=True)
class SupplierEnable:
    """Thirdparty found but not marked as supplier; needs enabling."""

    supplier_dolibarr_id: int
    current_data: dict[str, Any]
    enable_payload: dict[str, Any]
    status: SupplierResolutionStatus = SupplierResolutionStatus.FOUND_NOT_SUPPLIER


@dataclass(frozen=True, slots=True)
class SupplierAmbiguous:
    """Multiple supplier candidates found; block operation."""

    candidates: list[dict[str, Any]]
    status: SupplierResolutionStatus = SupplierResolutionStatus.AMBIGUOUS
    reason: str = "Multiple thirdparties match the supplier query"


class SupplierInvoiceCreator:
    """
    Creator that handles supplier resolution for invoice creation.

    Given a supplier query (name or VAT/tax ID), this creator:
    1. Searches Dolibarr for existing thirdparties
    2. Determines the resolution outcome (found, not-found, enable, ambiguous)
    3. Performs the necessary actions (create, enable, or block)
    4. Returns the Dolibarr supplier ID to use for invoice creation
    """

    def __init__(self, dolibarr_client: Any) -> None:
        self.dolibarr = dolibarr_client

    def resolve_supplier(
        self,
        query: str,
        *,
        user_context: UserContext | None = None,
    ) -> SupplierResolutionOutcome:
        """
        Resolve a supplier from a text query (name or CIF/NIF).

        Searches Dolibarr thirdparties and returns the resolution outcome.

        Args:
            query: Supplier name or tax ID (NIF/CIF) to search for
            user_context: Optional user context for logging/auditing

        Returns:
            One of SupplierFound, SupplierNotFound, SupplierEnable, SupplierAmbiguous
        """
        if not query or not query.strip():
            return SupplierNotFound(
                reason="Supplier query is empty or None"
            )

        normalized_query = query.strip()

        # Step 1: Try searching by tax ID (NIF/CIF)
        tax_match = self._find_by_tax_id(normalized_query)
        if tax_match:
            return tax_match

        # Step 2: Try searching by name
        name_match = self._find_by_name(normalized_query)
        if name_match:
            return name_match

        # Step 3: No match found at all
        return SupplierNotFound(
            reason=f"No thirdparty matching '{normalized_query}' found in Dolibarr"
        )

    def _find_by_tax_id(self, tax_id: str) -> SupplierResolutionOutcome | None:
        """Search Dolibarr for a thirdparty by normalized tax ID (NIF/CIF)."""
        try:
            parties = self.dolibarr.find_thirdparty_by_tax_id(tax_id)
        except Exception:
            return None

        if not parties:
            return None

        # Single match - check if it's already marked as supplier
        if len(parties) == 1:
            party = parties[0]
            is_supplier = party.get("fournisseur", False) or party.get("supplier", False)
            if is_supplier:
                # Found an existing supplier
                return SupplierFound(
                    supplier_dolibarr_id=party.get("id"),
                    supplier_data=self._party_to_dict(party),
                )
            else:
                # Thirdparty exists but NOT marked as supplier -> needs enabling
                return SupplierEnable(
                    supplier_dolibarr_id=party.get("id"),
                    current_data=self._party_to_dict(party),
                    enable_payload=self._make_enable_payload(party),
                )

        # Multiple matches - ambiguous
        candidates = [self._party_to_dict(p) for p in parties]
        return SupplierAmbiguous(
            candidates=candidates,
            reason=f"Multiple thirdparties match tax ID '{tax_id}' in Dolibarr",
        )

    def _find_by_name(self, name: str) -> SupplierResolutionOutcome | None:
        """Search Dolibarr for a thirdparty by name."""
        try:
            # Search thirdparties with supplier filter
            parties = self.dolibarr.list_thirdparties(
                limit=50,
                sqlfilters=f"t.name:='{name}' OR t.nom:='{name}'",
            )
        except Exception:
            return None

        if not parties:
            return None

        # Filter to those marked as supplier
        supplier_parties = [
            p for p in parties
            if p.get("fournisseur", False) or p.get("supplier", False)
        ]

        if len(supplier_parties) == 1:
            party = supplier_parties[0]
            return SupplierFound(
                supplier_dolibarr_id=party.get("id"),
                supplier_data=self._party_to_dict(party),
            )

        if len(supplier_parties) > 1:
            # Multiple suppliers with similar names - ambiguous
            all_candidates = [self._party_to_dict(p) for p in parties]
            return SupplierAmbiguous(
                candidates=all_candidates,
                reason=f"Multiple suppliers match name '{name}' in Dolibarr",
            )

        # Thirdparty found but not marked as supplier
        if not supplier_parties and parties:
            party = parties[0]
            return SupplierEnable(
                supplier_dolibarr_id=party.get("id"),
                current_data=self._party_to_dict(party),
                enable_payload=self._make_enable_payload(party),
            )

        return None

    def _party_to_dict(self, party: dict[str, Any]) -> dict[str, Any]:
        """Convert a Dolibarr thirdparty dict to a serializable form."""
        return {
            "id": party.get("id"),
            "name": party.get("name") or party.get("nom", ""),
            "ref": party.get("ref"),
            "fournisseur": party.get("fournisseur"),
            "supplier": party.get("supplier"),
            "vat_number": party.get("vat_number") or party.get("vatnumber"),
            "email": party.get("email"),
            "phone": party.get("phone"),
        }

    def _make_enable_payload(self, party: dict[str, Any]) -> dict[str, Any]:
        """Create payload to enable supplier flag on an existing thirdparty."""
        return {
            "fournisseur": 1,
            # Preserve existing fields; only set supplier flag
        }

    def create_thirdparty_supplier(
        self,
        supplier_name: str,
        tax_id: str | None = None,
        user_context: UserContext | None = None,
    ) -> dict[str, Any]:
        """
        Create a new thirdparty in Dolibarr with supplier flag enabled.

        Args:
            supplier_name: Name of the supplier
            tax_id: Optional NIF/CIF tax identifier
            user_context: Optional user context for logging/auditing

        Returns:
            Created thirdparty dict from Dolibarr API
        """
        data: dict[str, Any] = {
            "name": supplier_name,
            "fournisseur": 1,
            "client": 0,
        }

        if tax_id:
            data["vat_number"] = tax_id

        try:
            result = self.dolibarr.create_thirdparty(data)
            return dict(result) if result else {}
        except Exception as e:
            raise RuntimeError(
                f"Failed to create thirdparty supplier '{supplier_name}': {e}"
            )

    def enable_existing_thirdparty(
        self,
        thirdparty_id: int,
        user_context: UserContext | None = None,
    ) -> dict[str, Any]:
        """
        Enable the supplier flag on an existing thirdparty.

        Args:
            thirdparty_id: Dolibarr thirdparty (rowid/ID)
            user_context: Optional user context for logging/auditing

        Returns:
            Updated thirdparty dict from Dolibarr API
        """
        try:
            result = self.dolibarr.update_thirdparty(thirdparty_id, {"fournisseur": 1})
            return dict(result) if result else {}
        except Exception as e:
            raise RuntimeError(
                f"Failed to enable thirdparty ID {thirdparty_id}: {e}"
            )