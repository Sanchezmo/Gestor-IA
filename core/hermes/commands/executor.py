"""
Command Layer V1 - Command Executor.

Orchestrates preview → confirmation → execution → audit.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog

from core.hermes.audit import AuditLogger
from core.hermes.authorization import AuthorizationService
from core.hermes.commands.base import CommandRegistry
from core.hermes.commands.models import (
    CommandIntent,
    CommandPreview,
    CommandResult,
    CommandStatus,
    CommandType,
    PendingCommand,
)
from core.hermes.commands.policy import get_company_policy
from core.hermes.commands.store import PendingCommandStore
from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.invoices import (
    CorrectionParser,
    CorrectionApplicator,
    SupplierInvoiceDraft,
    ValidationStatus,
    create_correction_parser,
    create_correction_applicator,
)
from core.integrations.dolibarr.client import DolibarrException
from core.integrations.telegram.client import TelegramClient, TelegramMessage


logger = structlog.get_logger()


class CommandExecutor:
    """Orchestrates command preview → confirmation → execution → audit."""

    def __init__(
        self,
        registry: CommandRegistry,
        store: PendingCommandStore,
        audit_logger: AuditLogger,
        company_context: CompanyContext,
        user_context: UserContext,
    ) -> None:
        self.registry = registry
        self.store = store
        self.audit = audit_logger
        self.ctx = company_context
        self.user = user_context
        self.auth = AuthorizationService()
        self.policy = get_company_policy(company_context.instance_id)

    async def preview(self, intent: CommandIntent) -> CommandPreview:
        """Generate preview for confirmation. Checks auth + policy."""
        # 1. Get handler
        handler = self.registry.get_handler(self.ctx.instance_id, intent.command_type)
        if not handler:
            raise ValueError(f"No handler for {intent.command_type}")

        # 2. Check permission BEFORE preview (skip if no Hermes permission required)
        required = handler.required_permission
        if required and not self.auth.can(self.user, required):
            await self._audit_preview_denied(intent, required)
            raise PermissionError(f"Requiere permiso: {required}")

        # 3. Validate payload
        try:
            validated_payload = handler.validate_payload(intent.payload)
        except ValueError as e:
            await self._audit_preview_denied(intent, "INVALID_PAYLOAD", str(e))
            raise

        # 4. Apply company policy
        validated_cmd = self.policy.validate_command(intent.command_type, validated_payload, self.ctx)
        enriched_payload = self.policy.enrich_command(intent.command_type, validated_cmd.payload, self.ctx)

        # 5. Generate preview
        preview = handler.generate_preview(enriched_payload, self.ctx)

        # 6. Store pending command
        pending = PendingCommand(
            command_id=preview.command_id,
            instance_id=self.ctx.instance_id,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
            chat_id=intent.chat_id if hasattr(intent, 'chat_id') and intent.chat_id else 0,
            command_type=intent.command_type,
            validated_payload=enriched_payload,
            status=CommandStatus.PENDING,
            created_at=datetime.now(),
            expires_at=datetime.now() + __import__("datetime").timedelta(hours=24),
            idempotency_key=str(preview.command_id),
            document_hash=intent.document_hash,
        )
        self.store.create(pending)

        # 7. Audit preview
        await self._audit_preview_created(preview, enriched_payload)

        return preview

    async def confirm(self, command_id: UUID, telegram_user_id: int) -> CommandResult:
        """Confirm and execute pending command."""
        # 1. Load and validate pending command atomically
        pending = self.store.confirm(command_id, telegram_user_id)
        if not pending:
            # Check if it exists but wrong user/state
            existing = self.store.get(command_id)
            if existing:
                if existing.telegram_user_id != telegram_user_id:
                    return CommandResult(
                        success=False,
                        error_code="FORBIDDEN",
                        error_message="Solo el usuario original puede confirmar",
                    )
                if existing.status == CommandStatus.EXECUTED:
                    return CommandResult(
                        success=True,
                        idempotent=True,
                        resource_id=existing.result.get("resource_id") if existing.result else None,
                        data=existing.result,
                    )
                return CommandResult(
                    success=False,
                    error_code="INVALID_STATE",
                    error_message=f"Comando en estado {existing.status.value}",
                )
            return CommandResult(
                success=False,
                error_code="NOT_FOUND",
                error_message="Confirmación no encontrada o expirada",
            )

        # 2. RECHECK authorization (skip if no Hermes permission required)
        handler = self.registry.get_handler(self.ctx.instance_id, pending.command_type)
        required = handler.required_permission
        if required and not self.auth.can(self.user, required):
            self.store.update_status(
                command_id, CommandStatus.FAILED, error_code="PERMISSION_REVOKED", error_message="Permiso revocado"
            )
            await self._audit_execution_failed(pending, "PERMISSION_REVOKED", "Permiso revocado antes de ejecutar")
            return CommandResult(success=False, error_code="PERMISSION_REVOKED", error_message="Permiso revocado")

        # 3. Audit confirmation
        await self._audit_confirmed(pending)

        # 4. Execute
        try:
            result = await handler.execute(
                self.ctx, self.user, pending.validated_payload, pending.document_hash
            )

            # 4a. Update to EXECUTED
            result_data = {
                "resource_id": result.resource_id,
                "resource_type": result.resource_type,
                "data": result.data,
                "idempotent": result.idempotent,
            }
            self.store.update_status(
                command_id,
                CommandStatus.EXECUTED,
                executed_at=datetime.now(),
                result=result_data,
            )

            # 4b. Audit success
            await self._audit_execution_success(pending, result)

            return result

        except DolibarrException as e:
            # Handle 409 as idempotent success
            if e.status_code == 409:
                existing_id = await self._find_existing_resource(pending)
                if existing_id:
                    result = CommandResult(
                        success=True,
                        resource_id=existing_id,
                        resource_type=pending.command_type.value.replace("create_", ""),
                        idempotent=True,
                    )
                    self.store.update_status(
                        command_id,
                        CommandStatus.EXECUTED,
                        executed_at=datetime.now(),
                        result={"resource_id": existing_id, "idempotent": True},
                    )
                    await self._audit_execution_success(pending, result)
                    return result

            # Other Dolibarr errors
            self.store.update_status(
                command_id, CommandStatus.FAILED, error_code=f"DOLIBARR_{e.status_code}", error_message=e.message
            )
            await self._audit_execution_failed(pending, f"DOLIBARR_{e.status_code}", e.message)
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido completar la operación",
            )

        except Exception as e:
            self.store.update_status(
                command_id, CommandStatus.FAILED, error_code="INTERNAL_ERROR", error_message=str(e)
            )
            await self._audit_execution_failed(pending, "INTERNAL_ERROR", str(e))
            return CommandResult(success=False, error_code="INTERNAL_ERROR", error_message="Error interno")

    async def cancel(self, command_id: UUID, telegram_user_id: int) -> CommandResult:
        """Cancel pending command.
        
        For supplier invoice commands, also marks the document as CANCELLED
        to allow re-upload of the same document. Never performs file cleanup
        during cancellation - files are preserved for potential re-upload.
        """
        cancelled = self.store.cancel(command_id, telegram_user_id)
        if not cancelled:
            existing = self.store.get(command_id)
            if not existing:
                return CommandResult(success=False, error_code="NOT_FOUND", error_message="Comando no encontrado")
            if existing.telegram_user_id != telegram_user_id:
                return CommandResult(
                    success=False, error_code="FORBIDDEN", error_message="Solo el usuario original puede cancelar"
                )
            return CommandResult(success=False, error_code="INVALID_STATE", error_message="Cancelado")

        # For supplier invoice commands, mark document as CANCELLED to allow re-upload
        if cancelled.command_type == CommandType.CREATE_SUPPLIER_INVOICE:
            document_hash = cancelled.validated_payload.get("document_hash")
            if document_hash:
                try:
                    from core.hermes.invoices.ingestion import DocumentIngestionService
                    ingestion_service = DocumentIngestionService(self.ctx, self.user, None)
                    await ingestion_service.mark_cancelled(document_hash)
                except Exception as e:
                    # Log but don't fail cancellation - document state update is best-effort
                    logger.warning(
                        "cancel_document_state_update_failed",
                        instance_id=self.ctx.instance_id,
                        command_id=str(command_id),
                        document_hash=document_hash[:16],
                        error=str(e),
                    )

        await self._audit_cancelled(cancelled)
        return CommandResult(success=True)

    async def correct(self, command_id: UUID, telegram_user_id: int) -> CommandResult:
        """Request correction for pending command."""
        # 1. Load and validate pending command atomically
        pending = self.store.get(command_id)
        if not pending:
            return CommandResult(
                success=False,
                error_code="NOT_FOUND",
                error_message="Comando no encontrado o expirado",
            )

        # 2. Validate ownership
        if pending.telegram_user_id != telegram_user_id:
            return CommandResult(
                success=False,
                error_code="FORBIDDEN",
                error_message="Solo el usuario original puede solicitar corrección",
            )

        # 3. Validate state - only PENDING commands can be corrected
        if pending.status != CommandStatus.PENDING:
            return CommandResult(
                success=False,
                error_code="INVALID_STATE",
                error_message=f"Comando en estado {pending.status.value}, no se puede corregir",
            )

        # 4. Update to CORRECTION_REQUESTED state
        updated = self.store.update_status(
            command_id,
            CommandStatus.CORRECTION_REQUESTED,
            correction_requested_at=datetime.now(),
        )
        if not updated:
            return CommandResult(
                success=False,
                error_code="STATE_UPDATE_FAILED",
                error_message="No se pudo actualizar el estado del comando",
            )

        # 5. Audit correction request
        await self._audit_correction_requested(pending)

        return CommandResult(success=True)

    async def handle_correction_text(
        self,
        telegram_user_id: int,
        chat_id: int,
        correction_text: str,
        telegram_client: TelegramClient,
    ) -> CommandResult:
        """
        Handle correction text message from user when in CORRECTION_REQUESTED state.
        
        Flow:
        1. Find pending command in CORRECTION_REQUESTED for this user/chat
        2. Parse correction text using LLM
        3. Apply corrections to draft
        4. Recalculate and revalidate
        5. Generate new preview
        6. Update pending command with new draft
        7. Return new preview
        
        ERP WRITES = 0 during this entire flow.
        """
        logger.info(
            "correction_text_received",
            instance_id=self.ctx.instance_id,
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            text_length=len(correction_text),
        )

        # 1. Find pending command in CORRECTION_REQUESTED state
        pending = self.store.find_correction_requested(telegram_user_id, chat_id)
        if not pending:
            return CommandResult(
                success=False,
                error_code="NO_CORRECTION_PENDING",
                error_message="No hay corrección pendiente para este chat/usuario",
            )

        command_id = pending.command_id

        # 2. Verify it's a supplier invoice command (only type that supports correction)
        if pending.command_type != CommandType.CREATE_SUPPLIER_INVOICE:
            return CommandResult(
                success=False,
                error_code="UNSUPPORTED_COMMAND_TYPE",
                error_message=f"Corrección no soportada para {pending.command_type.value}",
            )

        # 3. Extract draft from validated_payload
        draft_dict = pending.validated_payload.get("draft")
        if not draft_dict:
            return CommandResult(
                success=False,
                error_code="NO_DRAFT_IN_PENDING",
                error_message="No se encontró borrador en el comando pendiente",
            )

        # Reconstruct SupplierInvoiceDraft from dict
        try:
            draft = self._reconstruct_draft(draft_dict)
        except Exception as e:
            logger.error("correction_draft_reconstruct_failed", error=str(e))
            return CommandResult(
                success=False,
                error_code="DRAFT_RECONSTRUCT_FAILED",
                error_message="Error reconstruyendo borrador para corrección",
            )

        # 4. Create correction parser and parse the text
        ai_provider = self.ctx.create_ai_provider() if hasattr(self.ctx, 'create_ai_provider') else None
        if not ai_provider:
            # Fallback: create provider from instance config
            from core.hermes.ai import create_ai_provider
            ai_config = self.ctx.instance_config.ai
            ai_provider = create_ai_provider(
                provider_type="ollama",
                endpoint=ai_config.ollama_endpoint,
                model=ai_config.ollama_model,
                vision_model=ai_config.ollama_vision_model or ai_config.ollama_model,
                timeout=120.0,
            )

        parser = create_correction_parser(self.ctx.instance_config, ai_provider)
        parse_result = await parser.parse(draft, correction_text)

        if not parse_result.success:
            logger.warning(
                "correction_parse_failed",
                instance_id=self.ctx.instance_id,
                command_id=str(command_id),
                error_code=parse_result.error_code,
            )
            # Send error message to user
            await telegram_client.send_message(
                chat_id=chat_id,
                text=f"❌ {parse_result.error}",
                parse_mode=None,
            )
            return CommandResult(
                success=False,
                error_code=parse_result.error_code,
                error_message=parse_result.error,
            )

        # 5. Handle ambiguity
        if parse_result.ambiguous:
            clarification = parse_result.clarification_question or (
                "La corrección es ambigua. Por favor, sé más específico indicando "
                "el número de línea (empezando por 1) o el campo exacto a cambiar."
            )
            await telegram_client.send_message(
                chat_id=chat_id,
                text=f"❓ {clarification}",
                parse_mode=None,
            )
            return CommandResult(
                success=True,
                data={"ambiguous": True, "clarification": clarification},
            )

        # 6. Apply corrections
        applicator = create_correction_applicator()
        app_result = applicator.apply(draft, parse_result.changes)

        if not app_result.success:
            logger.error(
                "correction_apply_failed",
                instance_id=self.ctx.instance_id,
                command_id=str(command_id),
                error=app_result.error,
            )
            await telegram_client.send_message(
                chat_id=chat_id,
                text=f"❌ {app_result.error}",
                parse_mode=None,
            )
            return CommandResult(
                success=False,
                error_code=app_result.error_code,
                error_message=app_result.error,
            )

        # 7. Update pending command with new draft
        new_draft_dict = self._draft_to_dict(app_result.draft)
        new_validated_payload = dict(pending.validated_payload)
        new_validated_payload["draft"] = new_draft_dict

        # Update status back to PENDING (ready for confirmation again)
        updated = self.store.update_status(
            command_id,
            CommandStatus.PENDING,
            validated_payload=new_validated_payload,
        )
        if not updated:
            logger.error("correction_status_update_failed", command_id=str(command_id))
            return CommandResult(
                success=False,
                error_code="STATE_UPDATE_FAILED",
                error_message="No se pudo actualizar el comando con la corrección",
            )

        # 8. Generate new preview
        handler = self.registry.get_handler(self.ctx.instance_id, CommandType.CREATE_SUPPLIER_INVOICE)
        if not handler:
            return CommandResult(
                success=False,
                error_code="HANDLER_NOT_FOUND",
                error_message="Handler no encontrado para generar preview",
            )

        # Use the same preview generation logic as ingestion
        preview_text = self._generate_supplier_invoice_preview(app_result.draft)

        preview = CommandPreview(
            command_type=CommandType.CREATE_SUPPLIER_INVOICE,
            summary=preview_text,
            structured_data=new_validated_payload,
            command_id=command_id,
        )

        # 9. Send new preview with buttons
        try:
            from core.hermes.commands.telegram import send_command_preview
            await send_command_preview(
                telegram=telegram_client,
                chat_id=chat_id,
                preview=preview,
            )
        except Exception as e:
            logger.error("correction_preview_send_failed", error=str(e))
            # Preview send failed but command is updated - don't fail the correction
            pass

        # 10. Audit correction applied
        await self._audit_correction_applied(pending, parse_result.changes, app_result.draft)

        logger.info(
            "correction_completed",
            instance_id=self.ctx.instance_id,
            command_id=str(command_id),
            validation_status=app_result.validation_status,
            line_count=len(app_result.draft.lines),
        )

        return CommandResult(
            success=True,
            data={
                "preview": preview_text,
                "validation_status": app_result.validation_status,
                "validation_errors": app_result.validation_errors,
                "validation_warnings": app_result.validation_warnings,
            },
        )

    def _reconstruct_draft(self, data: dict) -> SupplierInvoiceDraft:
        """Reconstruct SupplierInvoiceDraft from serialized dict."""
        from core.hermes.invoices.models import (
            SupplierInvoiceDraft, SupplierInfo, InvoiceLine, TaxBreakdownItem, WithholdingBreakdownItem,
            DocumentClassification, SupplierResolutionStatus, ValidationStatus, InvoiceFieldSource
        )
        from datetime import date
        from decimal import Decimal

        def parse_date(val):
            if val is None:
                return None
            if isinstance(val, date):
                return val
            if isinstance(val, str):
                try:
                    return date.fromisoformat(val)
                except Exception:
                    return None
            return None

        # Supplier
        supplier_data = data.get("supplier") or {}
        supplier = None
        if supplier_data.get("name") or supplier_data.get("tax_id"):
            supplier = SupplierInfo(
                name=supplier_data.get("name", ""),
                tax_id=supplier_data.get("tax_id", ""),
                address=supplier_data.get("address"),
                email=supplier_data.get("email"),
                phone=supplier_data.get("phone"),
            )

        # Lines
        lines = []
        for line_data in data.get("lines", []):
            lines.append(InvoiceLine(
                description=line_data.get("description", ""),
                quantity=Decimal(str(line_data.get("quantity", 1))),
                unit_price=Decimal(str(line_data.get("unit_price", 0))),
                vat_rate=Decimal(str(line_data.get("vat_rate", 21))),
                discount_percent=Decimal(str(line_data.get("discount_percent", 0))),
                product_ref=line_data.get("product_ref"),
            ))

        # Tax breakdown
        tax_breakdown = []
        for tax_data in data.get("tax_breakdown", []):
            tax_breakdown.append(TaxBreakdownItem(
                rate=Decimal(str(tax_data.get("rate", 0))),
                base=Decimal(str(tax_data.get("base", 0))),
                amount=Decimal(str(tax_data.get("amount", 0))),
                source=InvoiceFieldSource(tax_data.get("source", "KNOWN")),
            ))

        # Withholding breakdown
        withholding_breakdown = []
        for wh_data in data.get("withholding_breakdown", []):
            withholding_breakdown.append(WithholdingBreakdownItem(
                concept=wh_data.get("concept", "IRPF"),
                rate=Decimal(str(wh_data.get("rate", 0))),
                base=Decimal(str(wh_data.get("base", 0))),
                amount=Decimal(str(wh_data.get("amount", 0))),
                source=InvoiceFieldSource(wh_data.get("source", "KNOWN")),
            ))

        # Classification
        classification = data.get("classification")
        if isinstance(classification, str):
            classification = DocumentClassification(classification)
        elif classification is None:
            classification = DocumentClassification.UNKNOWN

        supplier_resolution_status = data.get("supplier_resolution_status")
        if isinstance(supplier_resolution_status, str):
            supplier_resolution_status = SupplierResolutionStatus(supplier_resolution_status)
        elif supplier_resolution_status is None:
            supplier_resolution_status = SupplierResolutionStatus.NOT_FOUND

        validation_status = data.get("validation_status")
        if isinstance(validation_status, str):
            validation_status = ValidationStatus(validation_status)

        return SupplierInvoiceDraft(
            document_hash=data.get("document_hash", ""),
            document_filename=data.get("document_filename", ""),
            document_mime_type=data.get("document_mime_type", ""),
            document_size_bytes=data.get("document_size_bytes", 0),
            page_count=data.get("page_count", 1),
            classification=classification,
            classification_confidence=Decimal(str(data.get("classification_confidence", 0))),
            classification_signals=data.get("classification_signals", []),
            supplier=supplier,
            invoice_number=data.get("invoice_number"),
            invoice_number_source=InvoiceFieldSource(data.get("invoice_number_source", "UNKNOWN")),
            invoice_date=parse_date(data.get("invoice_date")),
            invoice_date_source=InvoiceFieldSource(data.get("invoice_date_source", "UNKNOWN")),
            due_date=parse_date(data.get("due_date")),
            due_date_source=InvoiceFieldSource(data.get("due_date_source", "UNKNOWN")),
            currency=data.get("currency", "EUR"),
            payment_terms=data.get("payment_terms"),
            payment_method=data.get("payment_method"),
            notes=data.get("notes"),
            lines=lines,
            tax_breakdown=tax_breakdown,
            withholding_breakdown=withholding_breakdown,
            subtotal=Decimal(str(data.get("subtotal", 0))),
            subtotal_source=InvoiceFieldSource(data.get("subtotal_source", "UNKNOWN")),
            tax_total=Decimal(str(data.get("tax_total", 0))),
            tax_total_source=InvoiceFieldSource(data.get("tax_total_source", "UNKNOWN")),
            withholding_total=Decimal(str(data.get("withholding_total", 0))),
            withholding_total_source=InvoiceFieldSource(data.get("withholding_total_source", "UNKNOWN")),
            total=Decimal(str(data.get("total", 0))),
            total_source=InvoiceFieldSource(data.get("total_source", "UNKNOWN")),
            supplier_resolution_status=supplier_resolution_status,
            supplier_dolibarr_id=data.get("supplier_dolibarr_id"),
            supplier_candidates=data.get("supplier_candidates", []),
            validation_status=validation_status,
            validation_errors=data.get("validation_errors", []),
            validation_warnings=data.get("validation_warnings", []),
            extraction_confidence=Decimal(str(data.get("extraction_confidence", 0))),
            extraction_model=data.get("extraction_model", ""),
            extraction_raw_text_chars=data.get("extraction_raw_text_chars", 0),
            inference_count=data.get("inference_count", 0),
            instance_id=data.get("instance_id", ""),
            received_at=data.get("received_at", ""),
            correlation_id=data.get("correlation_id", ""),
        )

    def _draft_to_dict(self, draft: SupplierInvoiceDraft) -> dict:
        """Convert SupplierInvoiceDraft to serializable dict."""
        from dataclasses import asdict
        return asdict(draft)

    def _generate_supplier_invoice_preview(self, draft: SupplierInvoiceDraft) -> str:
        """Generate human-readable preview for supplier invoice (reuses ingestion logic)."""
        lines = []

        # AI Act Compliance: Transparency notice
        from core.hermes.ai_registry import transparency_manager
        feature_id = "supplier_invoice_extraction"
        channel = "telegram"
        if transparency_manager and transparency_manager.should_show(feature_id, channel):
            notice = transparency_manager.get_notice(feature_id, channel)
            if notice:
                lines.append(f"🤖 <i>{notice.message}</i>\n")

        lines.append("📄 <b>FACTURA DE PROVEEDOR</b>\n")

        # Supplier
        lines.append(f"<b>Proveedor:</b> {draft.get_supplier_display()}")
        lines.append(f"<b>CIF/NIF:</b> {draft.supplier.tax_id if draft.supplier and draft.supplier.tax_id else '—'}")
        lines.append(f"<b>Estado proveedor:</b> {draft.get_supplier_resolution_display()}")

        if draft.supplier_resolution_status == SupplierResolutionStatus.FOUND_NOT_SUPPLIER:
            lines.append("<b>⚠ El tercero existe pero no está habilitado como proveedor.</b>")
            lines.append("Se habilitará como proveedor durante la confirmación (requiere acción explícita).")

        if draft.supplier_resolution_status == SupplierResolutionStatus.AMBIGUOUS and draft.supplier_candidates:
            lines.append("\n<b>⚠ Candidatos encontrados:</b>")
            for i, cand in enumerate(draft.supplier_candidates[:3], 1):
                cand_name = cand.get("name", "Sin nombre")
                cand_tax = cand.get("vat_number") or cand.get("tva_intra") or "Sin CIF"
                lines.append(f"  {i}. {cand_name} ({cand_tax})")

        # Invoice header
        lines.append(f"\n<b>Factura:</b> {draft.invoice_number or '—'}")
        lines.append(f"<b>Fecha:</b> {draft.invoice_date.strftime('%d/%m/%Y') if draft.invoice_date else '—'}")
        if draft.due_date:
            lines.append(f"<b>Vencimiento:</b> {draft.due_date.strftime('%d/%m/%Y')}")

        # Lines summary
        if draft.lines:
            lines.append(f"\n<b>Líneas ({len(draft.lines)}):</b>")
            for i, line in enumerate(draft.lines[:5], 1):
                desc = line.description[:50] + "..." if len(line.description) > 50 else line.description
                lines.append(
                    f"  {i}. {desc} × {line.quantity} = "
                    f"{line.line_total_excl_tax:.2f} € + {line.vat_rate:.0f}% IVA = {line.line_total_incl_tax:.2f} €"
                )
            if len(draft.lines) > 5:
                lines.append(f"  ... y {len(draft.lines) - 5} líneas más")

        # Tax breakdown
        if draft.tax_breakdown:
            lines.append("\n<b>Desglose IVA:</b>")
            for tax in draft.tax_breakdown:
                source_icon = "🔍" if tax.source == InvoiceFieldSource.INFERRED else "✓"
                lines.append(
                    f"  {source_icon} IVA {tax.rate:.0f}%: Base {tax.base:.2f} € → {tax.amount:.2f} €"
                )
        elif draft.tax_total is not None and draft.tax_total > 0:
            lines.append(f"\n<b>IVA Total:</b> {draft.tax_total:.2f} € (sin desglose)")

        # Withholding
        if draft.withholding_breakdown:
            lines.append("\n<b>Retenciones:</b>")
            for wh in draft.withholding_breakdown:
                lines.append(f"  {wh.rate:.0f}% sobre {wh.base:.2f} € = {wh.amount:.2f} €")
        elif draft.withholding_total is not None and draft.withholding_total > 0:
            lines.append(f"\n<b>Retención Total:</b> {draft.withholding_total:.2f} €")

        # Totals
        lines.append(f"\n<b>Base Imponible:</b> {draft.subtotal:.2f} €" if draft.subtotal else "\n<b>Base Imponible:</b> —")
        lines.append(f"<b>IVA:</b> {draft.tax_total:.2f} €" if draft.tax_total else "<b>IVA:</b> —")
        if draft.withholding_total and draft.withholding_total > 0:
            lines.append(f"<b>Retenciones:</b> -{draft.withholding_total:.2f} €")
        lines.append(f"<b>TOTAL:</b> {draft.total:.2f} €" if draft.total else "<b>TOTAL:</b> —")

        # Inferred/Unknown fields
        inferred = draft.get_inferred_fields()
        unknown = draft.get_unknown_fields()

        if inferred:
            lines.append(f"\n<b>⚠ Campos inferidos:</b> {', '.join(inferred)}")
        if unknown:
            lines.append(f"\n<b>❓ Campos desconocidos:</b> {', '.join(unknown)}")

        # Validation status
        lines.append(f"\n<b>Validación:</b> {draft.get_validation_display()}")
        if draft.validation_errors:
            for err in draft.validation_errors:
                lines.append(f"  ✗ {err.get('message', err.get('code', 'Error'))}")
        if draft.validation_warnings:
            for warn in draft.validation_warnings:
                lines.append(f"  ⚠ {warn.get('message', warn.get('code', 'Aviso'))}")

        # Actions
        lines.append("\n<b>Opciones:</b>")
        lines.append("• <b>Confirmar</b> - Crear factura en Dolibarr")
        lines.append("• <b>Corregir</b> - Indicar correcciones")
        lines.append("• <b>Cancelar</b> - Descartar")

        return "\n".join(lines)

    async def _audit_correction_applied(
        self,
        pending: PendingCommand,
        changes: dict[str, Any],
        new_draft: SupplierInvoiceDraft,
    ) -> None:
        """Audit log for applied correction."""
        await self.audit.log_from_context(
            self.ctx,
            action="command.correction_applied",
            resource_type=pending.command_type.value,
            resource_id=str(pending.command_id),
            success=True,
            new_state={
                "changes_keys": list(changes.keys()),
                "validation_status": new_draft.validation_status.value if new_draft.validation_status else "unknown",
                "line_count": len(new_draft.lines),
                "total": str(new_draft.total) if new_draft.total else None,
            },
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    # Audit helpers

    async def _audit_correction_requested(self, pending: PendingCommand) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.correction_requested",
            resource_type=pending.command_type.value,
            resource_id=str(pending.command_id),
            success=True,
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    # Audit helpers

    async def _audit_preview_created(self, preview: CommandPreview, payload: dict) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.preview",
            resource_type=preview.command_type.value,
            resource_id=str(preview.command_id),
            success=True,
            new_state={"preview": preview.summary, "payload": payload},
            idempotency_key=str(preview.command_id),
        )

    async def _audit_preview_denied(self, intent: CommandIntent, error_code: str, error_message: str = "") -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.preview.denied",
            resource_type=intent.command_type.value,
            success=False,
            error_code=error_code,
            error_message=error_message,
            idempotency_key=intent.request_id,
        )

    async def _audit_confirmed(self, pending: PendingCommand) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.confirm",
            resource_type=pending.command_type.value,
            resource_id=str(pending.command_id),
            success=True,
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    async def _audit_execution_success(self, pending: PendingCommand, result: CommandResult) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.execute.success",
            resource_type=pending.command_type.value,
            resource_id=str(result.resource_id) if result.resource_id else str(pending.command_id),
            success=True,
            new_state={"resource_id": result.resource_id, "idempotent": result.idempotent},
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    async def _audit_execution_failed(self, pending: PendingCommand, error_code: str, error_message: str) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.execute.failure",
            resource_type=pending.command_type.value,
            resource_id=str(pending.command_id),
            success=False,
            error_code=error_code,
            error_message=error_message,
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    async def _audit_cancelled(self, pending: PendingCommand) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.cancel",
            resource_type=pending.command_type.value,
            resource_id=str(pending.command_id),
            success=True,
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    async def _find_existing_resource(self, pending: PendingCommand) -> int | None:
        """Try to find existing resource for 409 errors."""
        dolibarr = self.ctx.create_dolibarr_client()
        async with dolibarr as client:
            if pending.command_type == CommandType.CREATE_THIRDPARTY:
                vat = pending.validated_payload.get("vat_number")
                if vat:
                    existing = await client.find_thirdparty_by_tax_id(vat)
                    return existing.get("id") if existing else None
            elif pending.command_type in (CommandType.CREATE_PRODUCT, CommandType.CREATE_SERVICE):
                ref = pending.validated_payload.get("ref")
                if ref:
                    existing = await client.get_product_by_ref(ref)
                    return existing.get("id") if existing else None
        return None
