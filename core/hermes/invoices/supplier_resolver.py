"""
Supplier Resolver - Dolibarr supplier lookup/creation.

Ported from Transvega Animal:
- services/integration-api/app/services/invoice_integration_service.py: _ensure_supplier, find_supplier_or_thirdparty

Adapted for Gestor-IA:
- Uses user-scoped DolibarrClient (CompanyContext.create_dolibarr_client_for_user)
- NO admin API key fallback
- Returns structured SupplierResolutionResult
- Respects Dolibarr 401/403 as auth/permission errors
"""

from __future__ import annotations

import structlog
from typing import Any

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.integrations.dolibarr.client import DolibarrClient, DolibarrException
from .models import (
    SupplierResolutionResult,
    SupplierResolutionStatus,
    normalize_tax_id,
)

logger = structlog.get_logger()


class SupplierResolver:
    """
    Resolves suppliers in Dolibarr using user's API key.

    Flow:
    1. Search by normalized tax_id (CIF/NIF/VAT)
    2. If found and is supplier -> return
    3. If found but not supplier -> enable fournisseur flag (preserve client)
    4. If not found -> return NOT_FOUND (creation happens via Command Layer)

    ALL operations use user's Dolibarr API key - NO admin fallback.
    """

    def __init__(self, company_context: CompanyContext, user_context: UserContext):
        self.company_context = company_context
        self.user_context = user_context

    async def resolve(self, tax_id: str, name: str | None = None, address: str | None = None) -> SupplierResolutionResult:
        """
        Find or enable supplier by tax_id.

        Args:
            tax_id: Normalized CIF/NIF/VAT
            name: Supplier name (from extraction)
            address: Supplier address (from extraction)

        Returns:
            SupplierResolutionResult with status, dolibarr_id, and candidates
        """
        normalized_tax_id = normalize_tax_id(tax_id)
        if not normalized_tax_id:
            return SupplierResolutionResult(
                status=SupplierResolutionStatus.NOT_FOUND,
                error="Tax ID vacío",
            )

        # Create user-scoped Dolibarr client
        dolibarr = self.company_context.create_dolibarr_client_for_user(
            self.user_context.telegram_user_id
        )

        try:
            async with dolibarr as client:
                # Step 1: Find by tax_id
                party = await client.find_thirdparty_by_tax_id(normalized_tax_id)

                if party:
                    is_supplier = party.get("fournisseur") == 1 or party.get("supplier") == 1
                    party_id = party.get("id") or party.get("rowid")

                    if is_supplier:
                        logger.info(
                            "supplier_found",
                            instance_id=self.company_context.instance_id,
                            supplier_id=party_id,
                            tax_id_present=bool(normalized_tax_id),
                        )
                        return SupplierResolutionResult(
                            status=SupplierResolutionStatus.FOUND,
                            supplier_dolibarr_id=party_id,
                            supplier_data=party,
                        )

                    # Exists but not a supplier - enable fournisseur flag
                    logger.info(
                        "supplier_enable_started",
                        instance_id=self.company_context.instance_id,
                        thirdparty_id=party_id,
                    )
                    try:
                        await client.update_thirdparty(party_id, {
                            "fournisseur": 1,
                            "client": party.get("client", 1),  # Preserve client status
                        })
                        updated = await client.get_thirdparty(party_id)
                        logger.info(
                            "supplier_enable_completed",
                            instance_id=self.company_context.instance_id,
                            thirdparty_id=party_id,
                        )
                        return SupplierResolutionResult(
                            status=SupplierResolutionStatus.FOUND,
                            supplier_dolibarr_id=party_id,
                            supplier_data=updated,
                        )
                    except DolibarrException as e:
                        if e.status_code in (401, 403):
                            return SupplierResolutionResult(
                                status=SupplierResolutionStatus.NOT_FOUND,
                                error="Sin permisos para habilitar proveedor",
                            )
                        raise

                # Step 2: Not found - search by name as fallback (for ambiguous cases)
                if name:
                    candidates = await self._search_by_name(client, name, normalized_tax_id)
                    if candidates:
                        # Multiple candidates - ambiguous
                        if len(candidates) > 1:
                            logger.info(
                                "supplier_ambiguous",
                                instance_id=self.company_context.instance_id,
                                tax_id_present=bool(normalized_tax_id),
                                candidate_count=len(candidates),
                            )
                            return SupplierResolutionResult(
                                status=SupplierResolutionStatus.AMBIGUOUS,
                                candidates=candidates[:5],  # Limit to 5
                            )
                        # Single candidate - could be match
                        candidate = candidates[0]
                        candidate_tax_id = normalize_tax_id(candidate.get("vat_number", "") or candidate.get("tva_intra", ""))
                        if candidate_tax_id == normalized_tax_id:
                            # Tax ID matches - treat as found
                            candidate_id = candidate.get("id") or candidate.get("rowid")
                            is_supplier = candidate.get("fournisseur") == 1 or candidate.get("supplier") == 1
                            if not is_supplier:
                                await client.update_thirdparty(candidate_id, {"fournisseur": 1})
                                candidate = await client.get_thirdparty(candidate_id)
                            return SupplierResolutionResult(
                                status=SupplierResolutionStatus.FOUND,
                                supplier_dolibarr_id=candidate_id,
                                supplier_data=candidate,
                            )
                        else:
                            # Different tax_id - ambiguous
                            return SupplierResolutionResult(
                                status=SupplierResolutionStatus.AMBIGUOUS,
                                candidates=candidates[:5],
                            )

                # Step 3: Not found
                logger.info(
                    "supplier_not_found",
                    instance_id=self.company_context.instance_id,
                    tax_id_present=bool(normalized_tax_id),
                )
                return SupplierResolutionResult(
                    status=SupplierResolutionStatus.NOT_FOUND,
                )

        except DolibarrException as e:
            if e.status_code == 401:
                logger.warning(
                    "supplier_lookup_auth_failed",
                    instance_id=self.company_context.instance_id,
                    error="401 Unauthorized",
                )
                return SupplierResolutionResult(
                    status=SupplierResolutionStatus.NOT_FOUND,
                    error="Error de autenticación en Dolibarr",
                )
            elif e.status_code == 403:
                logger.warning(
                    "supplier_lookup_permission_denied",
                    instance_id=self.company_context.instance_id,
                    error="403 Forbidden",
                )
                return SupplierResolutionResult(
                    status=SupplierResolutionStatus.NOT_FOUND,
                    error="Sin permisos para consultar proveedores",
                )
            else:
                logger.error(
                    "supplier_lookup_error",
                    instance_id=self.company_context.instance_id,
                    status_code=e.status_code,
                    error=str(e),
                )
                return SupplierResolutionResult(
                    status=SupplierResolutionStatus.NOT_FOUND,
                    error=f"Error consultando Dolibarr: {e.status_code}",
                )

    async def _search_by_name(
        self,
        client: DolibarrClient,
        name: str,
        exclude_tax_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search thirdparties by name (fuzzy match)."""
        try:
            # Use search with sqlfilters for name matching
            # Dolibarr sqlfilters: name:like:'%name%'
            escaped_name = name.replace("'", "''").replace("%", "\\%").replace("_", "\\_")
            sqlfilters = f"name:like:'%{escaped_name}%'"

            parties = await client.list_thirdparties(
                limit=limit * 2,  # Fetch more to filter
                sqlfilters=sqlfilters,
            )

            # Filter: exclude exact tax_id match (already handled), sort by relevance
            candidates = []
            name_lower = name.lower()
            for party in parties:
                party_tax_id = normalize_tax_id(party.get("vat_number", "") or party.get("tva_intra", ""))
                if party_tax_id == exclude_tax_id:
                    continue

                # Simple relevance: exact name match first
                party_name = party.get("name", "").lower()
                if name_lower == party_name:
                    candidates.insert(0, party)
                else:
                    candidates.append(party)

            return candidates[:limit]

        except DolibarrException:
            return []

    async def check_duplicate_invoice(self, supplier_tax_id: str, invoice_number: str) -> bool:
        """
        Check if supplier invoice already exists in Dolibarr.

        Returns True if duplicate found, False if not found.
        Raises on integration errors (don't fail closed).
        """
        normalized_tax_id = normalize_tax_id(supplier_tax_id)
        if not normalized_tax_id or not invoice_number:
            return False

        dolibarr = self.company_context.create_dolibarr_client_for_user(
            self.user_context.telegram_user_id
        )

        try:
            async with dolibarr as client:
                # Find supplier first
                supplier = await client.find_thirdparty_by_tax_id(normalized_tax_id)
                if not supplier:
                    return False

                supplier_id = supplier.get("id") or supplier.get("rowid")
                if not supplier_id:
                    return False

                # List supplier invoices and check for duplicate number
                invoices = await client.list_supplier_invoices(
                    thirdparty_id=supplier_id,
                    limit=500,
                )

                invoice_number_upper = invoice_number.upper()
                for invoice in invoices:
                    ref = (invoice.get("ref") or "").upper()
                    ref_supplier = (invoice.get("ref_supplier") or "").upper()
                    if ref == invoice_number_upper or ref_supplier == invoice_number_upper:
                        logger.info(
                            "duplicate_invoice_found",
                            instance_id=self.company_context.instance_id,
                            supplier_id=supplier_id,
                            invoice_number=invoice_number,
                        )
                        return True

                return False

        except DolibarrException as e:
            logger.error(
                "duplicate_check_failed",
                instance_id=self.company_context.instance_id,
                tax_id_present=bool(normalized_tax_id),
                invoice_number=invoice_number,
                error=str(e),
            )
            # Integration error - don't fail closed, raise to prevent accidental creation
            raise

    async def create_supplier(
        self,
        tax_id: str,
        name: str,
        address: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> SupplierResolutionResult:
        """
        Create new supplier in Dolibarr using user's API key.

        This should be called from CreateThirdpartyHandler (Command Layer),
        not directly from invoice processing.
        """
        normalized_tax_id = normalize_tax_id(tax_id)

        dolibarr = self.company_context.create_dolibarr_client_for_user(
            self.user_context.telegram_user_id
        )

        try:
            async with dolibarr as client:
                supplier_data = {
                    "name": name,
                    "vat_number": normalized_tax_id,
                    "fournisseur": 1,
                    "client": 0,
                }
                if address:
                    supplier_data["address"] = address
                if email:
                    supplier_data["email"] = email
                if phone:
                    supplier_data["phone"] = phone

                result = await client.create_thirdparty(supplier_data)
                supplier_id = result.get("id")

                if not supplier_id:
                    return SupplierResolutionResult(
                        status=SupplierResolutionStatus.NOT_FOUND,
                        error="No se recibió ID del proveedor creado",
                    )

                supplier = await client.get_thirdparty(supplier_id)
                return SupplierResolutionResult(
                    status=SupplierResolutionStatus.FOUND,
                    supplier_dolibarr_id=supplier_id,
                    supplier_data=supplier,
                )

        except DolibarrException as e:
            if e.status_code == 409:
                # Duplicate - try to find existing
                return await self.resolve(tax_id, name)
            return SupplierResolutionResult(
                status=SupplierResolutionStatus.NOT_FOUND,
                error=f"Error creando proveedor: {e.status_code}",
            )