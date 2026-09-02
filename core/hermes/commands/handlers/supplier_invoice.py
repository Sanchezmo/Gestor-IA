"""
Command Layer V3 - Supplier Invoice Handlers.

Handler for creating supplier invoices in Dolibarr with deterministic calculations.
Includes ConfirmSupplierInvoiceHandler for the confirmation boundary execution.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from datetime import date, timedelta

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import (
    CommandType,
    CommandPreview,
    CommandResult,
    CreateSupplierInvoiceArgs,
    InvoiceLineArgs,
    calculate_invoice_line,
    calculate_invoice_totals,
)
from core.hermes.audit import DocumentIdempotencyManager, create_document_idempotency_manager
from core.hermes.invoices.models import DocumentState
from core.hermes.invoices.mapper import map_supplier_invoice_draft_to_dolibarr
from core.hermes.invoices.supplier_creator import (
    SupplierInvoiceCreator,
    SupplierFound,
    SupplierEnable,
    SupplierNotFound,
    SupplierAmbiguous,
)
from core.hermes.invoices.verification import verify_supplier_invoice, VerificationResult
from core.hermes.invoices.reconciliation import (
    ReconciliationEngine,
    ReconciliationResult,
    ReconciliationDetail,
    ReconciliationOutcome,
)
from core.hermes.ai_registry import (
    feature_registry,
    traceability_logger,
    AIUsePolicy,
    minimisation_filter,
    regulatory_gate,
    RuntimeVersionCapture,
    AITraceRecord,
)


class CreateSupplierInvoiceHandler(CommandHandler):
    """Handler for creating supplier invoices in Dolibarr (legacy V1 style)."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_SUPPLIER_INVOICE

    @property
    def required_permission(self) -> str:
        return "supplier_invoice.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        """Validate and normalize supplier invoice payload."""
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "proveedor_query": payload.get("proveedor", "").strip(),
            "fecha": payload.get("fecha"),
            "fecha_vencimiento": payload.get("fecha_vencimiento"),
            "lineas": payload.get("lineas", []),
            "forma_pago": payload.get("forma_pago"),
            "serie": payload.get("serie"),
            "proyecto": payload.get("proyecto"),
            "notas": payload.get("notas"),
        }

        if not validated["proveedor_query"]:
            raise ValueError("Proveedor es obligatorio")
        if not validated["lineas"]:
            raise ValueError("Al menos una línea es obligatoria")

        return validated

    def _calculate_line(self, line: dict) -> dict[str, Decimal]:
        """Calcular base, IVA, total para una línea de factura proveedor."""
        qty = Decimal(str(line["cantidad"]))
        price = Decimal(str(line["precio_unitario"]))
        discount = Decimal(str(line["descuento_porcentaje"])) / Decimal("100")
        vat_rate = Decimal(str(line["iva_porcentaje"])) / Decimal("100")

        base = qty * price * (Decimal("1") - discount)
        base = base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        iva = base * vat_rate
        iva = iva.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total = base + iva

        return {
            "base": base,
            "iva": iva,
            "total": total,
            "vat_rate": Decimal(str(line["iva_porcentaje"])),
        }

    def _calculate_totals(self, lines: list[dict]) -> dict[str, Decimal]:
        """Calcular totales de factura de proveedor (sin retención)."""
        total_base = Decimal("0")
        total_iva = Decimal("0")

        for line in lines:
            calc = self._calculate_line(line)
            total_base += calc["base"]
            total_iva += calc["iva"]

        total_ttc = total_base + total_iva

        return {
            "total_base": total_base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_iva": total_iva.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_ttc": total_ttc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        }

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate human-readable preview with full breakdown."""
        line_calcs = []
        total_base = Decimal("0")
        total_iva = Decimal("0")

        for i, line in enumerate(validated_payload["lineas"], 1):
            calc = self._calculate_line(line)
            total_base += calc["base"]
            total_iva += calc["iva"]

            line_calcs.append({
                "num": i,
                "descripcion": line["descripcion"],
                "cantidad": line["cantidad"],
                "precio": Decimal(str(line["precio_unitario"])),
                "descuento": line["descuento_porcentaje"],
                "iva": line["iva_porcentaje"],
                "base": calc["base"],
                "iva_amt": calc["iva"],
                "total": calc["total"],
            })

        total_ttc = total_base + total_iva

        lines = [
            "Voy a crear factura de proveedor:",
            f"Proveedor: {validated_payload['proveedor_query']}",
        ]

        fecha = validated_payload.get("fecha") or date.today().isoformat()
        fecha_venc = validated_payload.get("fecha_vencimiento")
        if fecha_venc:
            lines.append(f"Fecha: {fecha}, Vencimiento: {fecha_venc}")
        else:
            lines.append(f"Fecha: {fecha}")

        if validated_payload.get("serie"):
            lines.append(f"Serie: {validated_payload['serie']}")
        if validated_payload.get("forma_pago"):
            lines.append(f"Forma pago: {validated_payload['forma_pago']}")
        if validated_payload.get("proyecto"):
            lines.append(f"Proyecto: {validated_payload['proyecto']}")

        lines.append("\nLíneas:")
        for lc in line_calcs:
            lines.append(
                f"{lc['num']}. {lc['descripcion']} × {lc['cantidad']} = "
                f"{lc['base']:.2f}€ + {lc['iva']:.0f}% IVA = {lc['total']:.2f}€"
            )

        lines.append(f"\nBase imponible: {total_base:.2f}€")
        lines.append(f"IVA: {total_iva:.2f}€")
        lines.append(f"TOTAL: {total_ttc:.2f}€")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        validated_payload: dict[str, Any],
        document_hash: str | None = None
    ) -> CommandResult:
        """Execute supplier invoice creation in Dolibarr."""
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # 1. Resolve supplier (search by name or VAT)
                proveedor_query = validated_payload["proveedor_query"]
                thirdparty = await client.find_thirdparty_by_tax_id(proveedor_query)
                if not thirdparty:
                    thirdparties = await client.search_thirdparties(
                        query=proveedor_query,
                        filter_supplier=True,
                        limit=1,
                    )
                    if thirdparties:
                        thirdparty = thirdparties[0]

                if not thirdparty:
                    thirdparty_result = await client.create_thirdparty({
                        "name": validated_payload["proveedor_query"],
                        "fournisseur": 1,
                    })
                    thirdparty_id = thirdparty_result.get("id")

                    # PERSIST SUPPLIER_CREATED STATE (durable)
                    idempotency_manager = create_document_idempotency_manager(instance_config=company_context.instance_config)
                    await idempotency_manager.record_completed(
                        instance_id=company_context.instance_id,
                        document_hash="",  # No document hash for supplier creation
                        supplier_tax_id=validated_payload.get("proveedor_query", ""),
                        supplier_invoice_number="",  # No invoice number for supplier creation
                        supplier_dolibarr_id=thirdparty_id,
                        final_state="SUPPLIER_CREATED",
                    )
                else:
                    thirdparty_id = thirdparty.get("id")

                if not thirdparty_id:
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_RESOLVE_FAILED",
                        error_message="No se pudo resolver/crear el proveedor",
                    )

                # 2. Prepare invoice header
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today
                fecha_venc = date.fromisoformat(validated_payload["fecha_vencimiento"]) if validated_payload.get("fecha_vencimiento") else today + timedelta(days=30)

                invoice_data = {
                    "socid": thirdparty_id,
                    "date": int(fecha.timestamp()),
                    "date_lim_reglement": int(fecha_venc.timestamp()) if validated_payload.get("fecha_vencimiento") else int((today + timedelta(days=30)).timestamp()),
                    "cond_reglement_id": validated_payload.get("cond_reglement_id"),
                    "mode_reglement_id": validated_payload.get("mode_reglement_id"),
                    "note_private": validated_payload.get("notas", ""),
                }

                if validated_payload.get("serie"):
                    invoice_data["ref"] = validated_payload["serie"]

                # 3. Create supplier invoice
                invoice = await client.create_supplier_invoice(invoice_data)
                invoice_id = invoice.get("id")
                invoice_ref = invoice.get("ref")

                if not invoice_id:
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_INVOICE_CREATE_FAILED",
                        error_message="No se pudo crear la factura de proveedor",
                    )

                # PERSIST INVOICE_CREATED STATE (durable)
                idempotency_manager = create_document_idempotency_manager(instance_config=company_context.instance_config)
                await idempotency_manager.mark_invoice_created(
                    instance_id=company_context.instance_id,
                    document_hash=document_hash or "",
                    invoice_dolibarr_id=invoice_id,
                    dolibarr_invoice_ref=invoice_ref,
                    dolibarr_invoice_id=invoice_id,
                )

                # 4. Add lines
                for line in validated_payload["lineas"]:
                    line_data = {
                        "label": line["descripcion"],
                        "qty": line["cantidad"],
                        "price_ht": line["precio_unitario"],
                        "tva_tx": line["iva_porcentaje"],
                        "remise_percent": line["descuento_porcentaje"],
                    }

                    if line.get("producto_ref"):
                        product = await client.get_product_by_ref(line["producto_ref"])
                        if product:
                            line_data["fk_product"] = product.get("id")

                    await client.add_supplier_invoice_line(invoice_id, line_data)

                # 5. Validate invoice
                await client.validate_supplier_invoice(invoice_id)

                # 6. Calculate totals for response
                totals = self._calculate_totals(validated_payload["lineas"])

                return CommandResult(
                    success=True,
                    resource_id=invoice_id,
                    resource_type="supplier_invoice",
                    data={
                        "id": invoice_id,
                        "ref": invoice_ref,
                        "proveedor": validated_payload["proveedor_query"],
                        "total_base": str(totals["total_base"]),
                        "total_iva": str(totals["total_iva"]),
                        "total_ttc": str(totals["total_ttc"]),
                    },
                )

        except Exception as e:
            if hasattr(e, "status_code") and e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{getattr(e, 'status_code', 500)}",
                error_message="No he podido crear la factura de proveedor",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "supplier_invoice",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
            "total_ttc": result.data.get("total_ttc") if result.data else None,
        }


# =========================================================================
# CONFIRM SUPPLIER INVOICE HANDLER (Confirmation Boundary Execution)
# =========================================================================

class ConfirmSupplierInvoiceHandler(CommandHandler):
    """
    Handler for confirming supplier invoice creation after user approval.

    This handler executes the full durable state machine:
    PENDING_CONFIRMATION → CONFIRMING → SUPPLIER_CREATED → INVOICE_CREATED
    → ATTACHMENT_PENDING → COMPLETED

    With error states: ERP_RESULT_UNKNOWN, FAILED_RETRYABLE, FAILED_FINAL

    All operations use user-scoped Dolibarr API key (FAIL CLOSED).
    """

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_SUPPLIER_INVOICE

    @property
    def required_permission(self) -> str:
        return "supplier_invoice.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        """Validate confirmation payload from pending command."""
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        # Payload contains the full draft data from ingestion
        validated = {
            "draft": payload.get("draft"),
            "document_hash": payload.get("document_hash"),
            "file_content": payload.get("file_content"),
            "filename": payload.get("filename"),
            "mime_type": payload.get("mime_type"),
        }

        if not validated["draft"]:
            raise ValueError("Draft data is required for confirmation")
        if not validated["document_hash"]:
            raise ValueError("Document hash is required")

        return validated

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        validated_payload: dict[str, Any],
        document_hash: str | None = None,
    ) -> CommandResult:
        """Execute the full confirmation workflow with durable state machine."""
        draft = validated_payload["draft"]
        doc_hash = validated_payload["document_hash"]
        file_content = validated_payload.get("file_content")
        filename = validated_payload.get("filename")
        mime_type = validated_payload.get("mime_type")

        # Extract key data from draft
        supplier_tax_id = draft.supplier.tax_id if draft.supplier else ""
        invoice_number = draft.invoice_number or ""

        if not supplier_tax_id:
            return CommandResult(
                success=False,
                error_code="MISSING_SUPPLIER_TAX_ID",
                error_message="Supplier tax ID missing from draft",
            )
        if not invoice_number:
            return CommandResult(
                success=False,
                error_code="MISSING_INVOICE_NUMBER",
                error_message="Invoice number missing from draft",
            )

        # Get user-scoped Dolibarr client (FAIL CLOSED)
        try:
            identity = company_context.user_context
            if not identity:
                return CommandResult(
                    success=False,
                    error_code="MISSING_USER_CONTEXT",
                    error_message="User context not available",
                )

            dolibarr = company_context.create_dolibarr_client_for_user(identity)
        except ValueError as e:
            return CommandResult(
                success=False,
                error_code="DOLIBARR_KEY_MISSING",
                error_message=str(e),
            )

        # Initialize services
        idempotency = create_document_idempotency_manager(instance_config=company_context.instance_config)
        supplier_creator = SupplierInvoiceCreator(dolibarr)

        # Capture runtime versions for AI compliance
        git_sha = RuntimeVersionCapture.capture_git_sha()
        runtime_versions = RuntimeVersionCapture.capture(
            gestor_ia_version="1.0.0",  # TODO: from package metadata
            workflow_version="supplier-invoice-v1",
            ai_config_version="1.0",
            provider_version=f"ollama:{company_context.instance_config.ai.ollama_model}",
            policy_version="1.0",
            git_sha=git_sha,
        )

        async with dolibarr as client:
            try:
                # ============================================================
                # STEP 1: Revalidation Gate (CONFIRMING)
                # ============================================================
                await idempotency.update_milestone(
                    instance_id=company_context.instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=invoice_number,
                    new_state="CONFIRMING",
                )

                # Re-validate duplicate check (durable + Dolibarr)
                existing_durable = await idempotency.get_operation(
                    instance_id=company_context.instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=invoice_number,
                )

                if existing_durable and existing_durable.final_state in (
                    "COMPLETED", "INVOICE_CREATED", "SUPPLIER_CREATED",
                    "ATTACHMENT_PENDING", "ERP_RESULT_UNKNOWN"
                ):
                    return CommandResult(
                        success=False,
                        error_code="DUPLICATE_INVOICE",
                        error_message=f"Invoice already exists in state: {existing_durable.final_state}",
                    )

                # Re-validate Dolibarr duplicate using available client methods
                # Duplicate identity: (instance_id, supplier_tax_id, supplier_invoice_number)
                # Lookup by supplier_tax_id -> find thirdparty -> list invoices -> check ref/ref_supplier
                try:
                    supplier = await client.find_thirdparty_by_tax_id(supplier_tax_id)
                    if not supplier:
                        # No thirdparty found with this tax_id; no duplicate possible
                        duplicate_exists = False
                    else:
                        supplier_id = supplier.get("id") or supplier.get("rowid")
                        if not supplier_id:
                            duplicate_exists = False
                        else:
                            invoices = await client.list_supplier_invoices(
                                thirdparty_id=supplier_id,
                                limit=500,
                            )
                            invoice_number_upper = invoice_number.upper()
                            duplicate_exists = False
                            for inv in invoices:
                                ref = (inv.get("ref") or "").upper()
                                ref_supplier = (inv.get("ref_supplier") or "").upper()
                                if ref == invoice_number_upper or ref_supplier == invoice_number_upper:
                                    duplicate_exists = True
                                    break
                    if duplicate_exists:
                        return CommandResult(
                            success=False,
                            error_code="DUPLICATE_INVOICE_DOLIBARR",
                            error_message="Invoice already exists in Dolibarr",
                        )
                except Exception as e:
                    # Integration error during duplicate check - FAIL CLOSED:
                    # DO NOT CREATE the invoice if we cannot verify duplication.
                    # Preserve a retry-safe state and return a safe error.
                    return CommandResult(
                        success=False,
                        error_code="DUPLICATE_CHECK_FAILED",
                        error_message="Duplicate check integration error; "
                        "invoice CREATE blocked to prevent duplicate. "
                        "Retry after resolving Dolibarr availability.",
                    )

                # ============================================================
                # STEP 2: Supplier Resolution (SUPPLIER_CREATED / INVOICE_CREATED)
                # ============================================================
                supplier_query = f"{draft.supplier.name} {draft.supplier.tax_id}".strip()
                supplier_outcome = await supplier_creator.resolve_supplier(supplier_query)

                supplier_dolibarr_id: int | None = None

                if isinstance(supplier_outcome, SupplierFound):
                    supplier_dolibarr_id = supplier_outcome.supplier_dolibarr_id

                elif isinstance(supplier_outcome, SupplierEnable):
                    # Enable existing thirdparty as supplier
                    try:
                        await supplier_creator.enable_existing_thirdparty(
                            supplier_outcome.supplier_dolibarr_id
                        )
                        supplier_dolibarr_id = supplier_outcome.supplier_dolibarr_id
                    except Exception as e:
                        await idempotency.mark_failed_retryable(
                            instance_id=company_context.instance_id,
                            supplier_tax_id=supplier_tax_id,
                            supplier_invoice_number=invoice_number,
                        )
                        return CommandResult(
                            success=False,
                            error_code="SUPPLIER_ENABLE_FAILED",
                            error_message=f"Failed to enable supplier: {e}",
                        )

                elif isinstance(supplier_outcome, SupplierNotFound):
                    # Create new supplier
                    try:
                        created = await supplier_creator.create_thirdparty_supplier(
                            supplier_name=draft.supplier.name,
                            tax_id=draft.supplier.tax_id,
                        )
                        supplier_dolibarr_id = created.get("id")
                    except Exception as e:
                        await idempotency.mark_failed_retryable(
                            instance_id=company_context.instance_id,
                            supplier_tax_id=supplier_tax_id,
                            supplier_invoice_number=invoice_number,
                        )
                        return CommandResult(
                            success=False,
                            error_code="SUPPLIER_CREATE_FAILED",
                            error_message=f"Failed to create supplier: {e}",
                        )

                elif isinstance(supplier_outcome, SupplierAmbiguous):
                    # AMBIGUOUS blocks write
                    await idempotency.mark_failed_final(
                        instance_id=company_context.instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=invoice_number,
                    )
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_AMBIGUOUS",
                        error_message=f"Multiple suppliers match query: {supplier_outcome.reason}",
                    )

                if not supplier_dolibarr_id:
                    await idempotency.mark_failed_retryable(
                        instance_id=company_context.instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=invoice_number,
                    )
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_ID_MISSING",
                        error_message="Could not determine supplier Dolibarr ID",
                    )

                # Persist SUPPLIER_CREATED milestone
                await idempotency.mark_supplier_created(
                    instance_id=company_context.instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=invoice_number,
                    supplier_dolibarr_id=supplier_dolibarr_id,
                )

                # ============================================================
                # STEP 3: Create Supplier Invoice (INVOICE_CREATED)
                # ============================================================
                invoice_payload = map_supplier_invoice_draft_to_dolibarr(draft)
                invoice_payload["socid"] = supplier_dolibarr_id

                try:
                    invoice = await client.create_supplier_invoice(invoice_payload)
                except Exception as e:
                    # Check if it's a timeout (POST sent but no response)
                    is_timeout = "timeout" in str(e).lower() or "timed out" in str(e).lower()

                    if is_timeout:
                        # ERP_RESULT_UNKNOWN - don't retry blindly
                        await idempotency.mark_erp_result_unknown(
                            instance_id=company_context.instance_id,
                            supplier_tax_id=supplier_tax_id,
                            supplier_invoice_number=invoice_number,
                        )
                        return CommandResult(
                            success=False,
                            error_code="ERP_RESULT_UNKNOWN",
                            error_message="POST to Dolibarr timed out. Invoice may have been created. Reconciliation required.",
                        )

                    await idempotency.mark_failed_retryable(
                        instance_id=company_context.instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=invoice_number,
                    )
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_INVOICE_CREATE_FAILED",
                        error_message=f"Failed to create supplier invoice: {e}",
                    )

                invoice_id = invoice.get("id")
                invoice_ref = invoice.get("ref")

                if not invoice_id:
                    await idempotency.mark_failed_retryable(
                        instance_id=company_context.instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=invoice_number,
                    )
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_INVOICE_CREATE_FAILED",
                        error_message="No invoice ID returned from Dolibarr",
                    )

                # Persist INVOICE_CREATED milestone
                await idempotency.mark_invoice_created(
                    instance_id=company_context.instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=invoice_number,
                    supplier_dolibarr_id=supplier_dolibarr_id,
                    invoice_dolibarr_id=invoice_id,
                    dolibarr_invoice_ref=invoice_ref,
                    dolibarr_invoice_id=invoice_id,
                )

                # ============================================================
                # STEP 4: Add Invoice Lines
                # ============================================================
                for line in draft.lines:
                    line_data = {
                        "label": line.description,
                        "qty": float(line.quantity),
                        "price_ht": float(line.unit_price),
                        "tva_tx": float(line.vat_rate),
                        "remise_percent": float(line.discount_percent),
                    }

                    if line.product_ref:
                        product = await client.get_product_by_ref(line.product_ref)
                        if product:
                            line_data["fk_product"] = product.get("id")

                    await client.add_supplier_invoice_line(invoice_id, line_data)

                # ============================================================
                # STEP 5: Validate Invoice
                # ============================================================
                await client.validate_supplier_invoice(invoice_id)

                # ============================================================
                # STEP 6: Post-Write Verification (Mandatory)
                # ============================================================
                created_invoice = await client.get_supplier_invoice(invoice_id)

                verification: VerificationResult = verify_supplier_invoice(
                    dolibarr_invoice=created_invoice,
                    original_draft=draft,
                )

                if not verification.success:
                    # Verification failed - mismatch detected
                    await idempotency.mark_failed_final(
                        instance_id=company_context.instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=invoice_number,
                    )
                    return CommandResult(
                        success=False,
                        error_code="POST_WRITE_VERIFICATION_FAILED",
                        error_message=f"Post-write verification failed: {verification.mismatched_fields}",
                    )

                # ============================================================
                # STEP 7: Attachment Upload (ATTACHMENT_PENDING)
                # ============================================================
                if file_content and filename:
                    await idempotency.mark_attachment_pending(
                        instance_id=company_context.instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=invoice_number,
                    )

                    attachment_success = False
                    for attempt in range(3):
                        try:
                            await client.upload_document(
                                resource_type="supplierinvoices",
                                resource_id=invoice_id,
                                file_data=file_content,
                                filename=filename,
                            )
                            attachment_success = True
                            break
                        except Exception:
                            if attempt == 2:
                                break
                            # Wait before retry (exponential backoff)
                            import asyncio
                            await asyncio.sleep(2 ** attempt)

                    if not attachment_success:
                        # Stay in ATTACHMENT_PENDING for retry
                        return CommandResult(
                            success=False,
                            error_code="ATTACHMENT_UPLOAD_FAILED",
                            error_message="Failed to upload attachment after retries. Invoice created in Dolibarr.",
                            data={"invoice_id": invoice_id, "invoice_ref": invoice_ref},
                        )

                    # Mark attachment uploaded
                    await idempotency.mark_attachment_uploaded(
                        instance_id=company_context.instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=invoice_number,
                    )
                else:
                    # No attachment to upload
                    await idempotency.mark_completed(
                        instance_id=company_context.instance_id,
                        supplier_tax_id=supplier_tax_id,
                        supplier_invoice_number=invoice_number,
                    )

                # ============================================================
                # STEP 8: COMPLETED
                # ============================================================
                # Log AI trace correlation
                if traceability_logger:
                    await traceability_logger.log_execution(
                        operation_id=doc_hash,
                        instance_id=company_context.instance_id,
                        feature_id="supplier_invoice_extraction",
                        provider="ollama",
                        model=company_context.instance_config.ai.ollama_model,
                        policy=AIUsePolicy.LOCAL_ONLY,
                        local_or_cloud="local",
                        success=True,
                        latency_ms=0,  # Would be captured at extraction time
                        correlation_id=doc_hash,
                    )

                return CommandResult(
                    success=True,
                    resource_id=str(invoice_id),
                    resource_type="supplier_invoice",
                    data={
                        "id": invoice_id,
                        "ref": invoice_ref,
                        "supplier_id": supplier_dolibarr_id,
                        "verification_status": verification.overall_status.value,
                    },
                )

            except Exception as e:
                # Unexpected error - mark as retryable
                await idempotency.mark_failed_retryable(
                    instance_id=company_context.instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=invoice_number,
                )
                return CommandResult(
                    success=False,
                    error_code="INTERNAL_ERROR",
                    error_message=f"Confirmation failed: {e}",
                )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "supplier_invoice",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
            "total_ttc": result.data.get("total_ttc") if result.data else None,
            "confirmation_workflow": True,
        }


# =========================================================================
# RECONCILIATION HANDLER (for ERP_RESULT_UNKNOWN recovery)
# =========================================================================

class ReconcileSupplierInvoiceHandler(CommandHandler):
    """Handler to reconcile ERP_RESULT_UNKNOWN state by querying Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_SUPPLIER_INVOICE

    @property
    def required_permission(self) -> str:
        return "supplier_invoice.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        return {
            "document_hash": payload.get("document_hash"),
            "supplier_tax_id": payload.get("supplier_tax_id"),
            "invoice_number": payload.get("invoice_number"),
        }

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        validated_payload: dict[str, Any],
        document_hash: str | None = None,
    ) -> CommandResult:
        """Reconcile a potentially uncertain invoice state."""
        supplier_tax_id = validated_payload.get("supplier_tax_id")
        invoice_number = validated_payload.get("invoice_number")
        doc_hash = validated_payload.get("document_hash")

        if not supplier_tax_id or not invoice_number:
            return CommandResult(
                success=False,
                error_code="MISSING_PARAMETERS",
                error_message="supplier_tax_id and invoice_number required",
            )

        try:
            dolibarr = company_context.create_dolibarr_client_for_user(
                company_context.user_context
            )
        except ValueError as e:
            return CommandResult(
                success=False,
                error_code="DOLIBARR_KEY_MISSING",
                error_message=str(e),
            )

        idempotency = create_document_idempotency_manager(instance_config=company_context.instance_config)
        reconciliation_engine = ReconciliationEngine(dolibarr)

        async with dolibarr as client:
            # Get current durable state
            current_state = await idempotency.get_state(
                instance_id=company_context.instance_id,
                supplier_tax_id=supplier_tax_id,
                supplier_invoice_number=invoice_number,
            )

            if current_state != "ERP_RESULT_UNKNOWN":
                return CommandResult(
                    success=False,
                    error_code="INVALID_STATE_FOR_RECONCILIATION",
                    error_message=f"Current state is {current_state}, expected ERP_RESULT_UNKNOWN",
                )

            # Perform reconciliation
            outcome: ReconciliationOutcome = reconciliation_engine.reconcile(
                invoice_ref=invoice_number,
                supplier_id=None,  # Would need supplier lookup
            )

            if outcome.result == ReconciliationResult.UNIQUE_MATCH:
                # Adopt the existing invoice
                await idempotency.mark_invoice_created(
                    instance_id=company_context.instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=invoice_number,
                    supplier_dolibarr_id=outcome.detail.dolibarr_id or 0,
                    invoice_dolibarr_id=outcome.detail.dolibarr_id or 0,
                    dolibarr_invoice_ref=outcome.detail.ref_supplier or "",
                    dolibarr_invoice_id=outcome.detail.dolibarr_id or 0,
                )
                # Continue to attachment/completion
                return CommandResult(
                    success=True,
                    data={
                        "action": "adopted",
                        "invoice_id": outcome.detail.dolibarr_id,
                        "supplier_id": outcome.detail.dolibarr_id,
                    },
                )

            elif outcome.result == ReconciliationResult.NO_MATCH:
                # Safe to retry creation
                await idempotency.mark_failed_retryable(
                    instance_id=company_context.instance_id,
                    supplier_tax_id=supplier_tax_id,
                    supplier_invoice_number=invoice_number,
                )
                return CommandResult(
                    success=True,
                    data={
                        "action": "retry_scheduled",
                        "reason": "No matching invoice found in Dolibarr",
                    },
                )

            elif outcome.result == ReconciliationResult.AMBIGUOUS_MATCH:
                return CommandResult(
                    success=False,
                    error_code="RECONCILIATION_AMBIGUOUS",
                    error_message=outcome.detail.error_message
                    or "Multiple matching invoices found. Manual review required.",
                )

            else:  # ERROR
                return CommandResult(
                    success=False,
                    error_code="RECONCILIATION_ERROR",
                    error_message=outcome.detail.error_message
                    or "Dolibarr unavailable or unexpected error. Remain uncertain, never CREATE.",
                )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "supplier_invoice_reconciliation",
            "resource_id": result.resource_id,
            "reconciliation": True,
        }