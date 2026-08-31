"""
Document Ingestion Pipeline for Supplier Invoices.

Orchestrates the complete flow:
Telegram document → Download → Hash → Classify → Extract → Validate → Resolve Supplier → Preview

Ported from Transvega Animal:
- agents/invoice_processing/agent.py: process_invoice (main flow)

Adapted for Gestor-IA:
- Uses CompanyContext for instance isolation
- User-scoped DolibarrClient for supplier resolution
- LOCAL_ONLY extraction via InvoiceExtractor
- Deterministic validation via SupplierInvoiceValidator
- File storage with instance_id/document_hash isolation
- Idempotency via document hash + Redis with state-based workflow tracking
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import structlog
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import redis

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.config import get_global_settings
from core.hermes.audit import DocumentIdempotencyManager
from .models import (
    SupplierInvoiceDraft,
    DocumentClassification,
    SupplierResolutionStatus,
    ValidationStatus,
    InvoiceFieldSource,
    DocumentState,
    DocumentStateData,
)
from .extractor import InvoiceExtractor, LocalModelUnavailableError
from .validator import validate_invoice, infer_missing_totals, normalize_tax_data
from .supplier_resolver import SupplierResolver

logger = structlog.get_logger()


# =========================================================================
# CONFIGURATION
# =========================================================================

MAX_AUTO_RETRIES = 3
PROCESSING_STALE_THRESHOLD_SECONDS = 300  # 5 minutes
DOCUMENT_STATE_TTL_SECONDS = 86400 * 7  # 7 days (solo estados transitorios en Redis)


# =========================================================================
# INGESTION RESULT
# =========================================================================

class IngestionResult:
    """Result of document ingestion."""

    def __init__(
        self,
        success: bool,
        draft: SupplierInvoiceDraft | None = None,
        error: str | None = None,
        error_code: str | None = None,
        preview_text: str | None = None,
        stored_path: str | None = None,
    ):
        self.success = success
        self.draft = draft
        self.error = error
        self.error_code = error_code
        self.preview_text = preview_text
        self.stored_path = stored_path


# =========================================================================
# DOCUMENT INGESTION SERVICE
# =========================================================================

class DocumentIngestionService:
    """
    Handles the complete document ingestion pipeline for supplier invoices.

    Flow:
    1. Download document from Telegram (file_id → bytes)
    2. Compute SHA-256 hash
    3. State-based idempotency check (workflow-aware)
    4. Store original document (instance-isolated)
    5. Classify document (invoice vs not invoice)
    6. Extract structured data (LOCAL_ONLY Ollama)
    7. Normalize & validate (deterministic)
    8. Resolve supplier in Dolibarr
    9. Generate preview for user confirmation
    """

    def __init__(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        telegram_client,
    ):
        self.company_context = company_context
        self.user_context = user_context
        self.telegram_client = telegram_client

        # Initialize components
        self.extractor = InvoiceExtractor(company_context.instance_config)
        self.validator = validate_invoice  # function
        self.supplier_resolver = SupplierResolver(company_context, user_context)

        # Redis for document state tracking (transient states, TTL 7 days)
        settings = get_global_settings()
        self.redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            db=company_context.instance_config.get_redis_db(),
            decode_responses=True,
        )

        # Durable idempotency manager (MariaDB - permanent storage, audit DB)
        from core.hermes.audit import create_document_idempotency_manager
        self.idempotency_manager = create_document_idempotency_manager(instance_config=company_context.instance_config)

        # Document storage paths
        self.documents_root = Path(company_context.instance_config.documents_path)
        self.pending_dir = self.documents_root / "pending"
        self.processed_dir = self.documents_root / "processed"
        self.rejected_dir = self.documents_root / "rejected"

        # Ensure directories exist
        for d in [self.pending_dir, self.processed_dir, self.rejected_dir]:
            d.mkdir(parents=True, exist_ok=True)

    async def ingest_from_telegram(self, file_id: str, filename: str, mime_type: str) -> IngestionResult:
        """
        Entry point from Telegram webhook.

        Args:
            file_id: Telegram file_id
            filename: Original filename
            mime_type: MIME type (application/pdf, image/png, image/jpeg)

        Returns:
            IngestionResult with draft and preview or error
        """
        # 1. Download document from Telegram
        try:
            file_info = await self.telegram_client.get_file(file_id)
            file_content = await self.telegram_client.download_file(file_info.file_path)
        except Exception as e:
            logger.error("telegram_download_failed", file_id=file_id, error=str(e))
            return IngestionResult(
                success=False,
                error="No se pudo descargar el documento de Telegram",
                error_code="TELEGRAM_DOWNLOAD_FAILED",
            )

        # Delegate to internal bytes ingestion
        return await self.ingest_bytes(file_content, filename, mime_type)

    async def ingest_bytes(self, file_content: bytes, filename: str, mime_type: str) -> IngestionResult:
        """
        Internal ingestion from raw bytes.

        Args:
            file_content: Raw file bytes
            filename: Original filename
            mime_type: MIME type (application/pdf, image/png, image/jpeg)

        Returns:
            IngestionResult with draft and preview or error
        """
        # 1. Validate file size (max 10MB for DEVELOPMENT)
        max_size = self.company_context.instance_config.telegram.max_file_size_mb * 1024 * 1024
        if len(file_content) > max_size:
            return IngestionResult(
                success=False,
                error=f"Archivo demasiado grande ({len(file_content) / 1024 / 1024:.1f} MB). Máximo: {max_size / 1024 / 1024:.0f} MB",
                error_code="FILE_TOO_LARGE",
            )

        # 2. Validate MIME type
        allowed_mimes = {
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/jpg",
        }
        if mime_type not in allowed_mimes:
            return IngestionResult(
                success=False,
                error=f"Tipo de archivo no soportado: {mime_type}. Use PDF, PNG o JPEG.",
                error_code="UNSUPPORTED_MIME_TYPE",
            )

        # Compute SHA-256 hash
        document_hash = hashlib.sha256(file_content).hexdigest()

        # DURABLE IDEMPOTENCY CHECK (MariaDB - permanent)
        # Check if this invoice was already fully processed in Dolibarr
        # (Will check after supplier resolution has supplier info)

        # TRANSIENT STATE CHECK (Redis - workflow tracking, TTL 7 days)
        existing_state = await self._get_document_state(document_hash)
        
        if existing_state:
            return await self._handle_existing_document(existing_state, b"", "", "")

        # New document - create initial state
        correlation_id = str(datetime.now(timezone.utc).timestamp()).replace(".", "")
        
        new_state = DocumentStateData(
            document_hash=document_hash,
            status=DocumentState.RECEIVED,
            instance_id=self.company_context.instance_id,
            correlation_id=str(datetime.now(timezone.utc).timestamp()).replace(".", ""),
            filename="",  # Will be set after
            mime_type=mime_type,
            file_size_bytes=0,  # Will be set after
            created_at=datetime.now(timezone.utc).isoformat(),
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        
        await self._save_document_state(new_state)
        
        # Store original document (pending)
        stored_path = await self._store_document(
            file_content=b"",  # Placeholder, will be set properly
            filename="",  # Placeholder
            document_hash=document_hash,
        )
        
        # Now we have the document_hash, we can do the full ingestion with the actual content
        # This is a two-phase approach: first register, then process
        return await self._process_document(document_hash, file_content, filename, mime_type)

    async def resume_from_stored_document(self, document_hash: str) -> IngestionResult:
        """
        Resume processing from a previously stored document.

        Args:
            document_hash: SHA256 of the original document

        Returns:
            IngestionResult with draft and preview or error
        """
        # Retrieve stored document
        state = await self._get_document_state(document_hash)
        if not state:
            return IngestionResult(
                success=False,
                error="Documento no encontrado en almacenamiento",
                error_code="DOCUMENT_NOT_FOUND",
            )
        
        # Check if we can resume from this state
        if state.status in (DocumentState.COMPLETED,):
            return IngestionResult(
                success=False,
                error="Esta factura ya fue procesada completamente",
                error_code="DOCUMENT_COMPLETED",
            )
        
        # TODO: Retrieve stored file content from disk
        # For now, return error indicating manual re-send needed
        return IngestionResult(
            success=False,
            error="Reintento requerido. Reenvíe el documento.",
            error_code="RETRY_REQUIRED",
        )
        
        try:
            # Update state to PROCESSING
            await self._update_document_status(document_hash, DocumentState.PROCESSING)
            
            # 7. Extract invoice data
            extraction_result = await self.extractor.extract(file_content, filename, mime_type)

            if not extraction_result.success:
                await self._handle_extraction_failure(document_hash, stored_path, extraction_result)
                return IngestionResult(
                    success=False,
                    error=extraction_result.error,
                    error_code=extraction_result.error_code,
                )

            draft = extraction_result.draft

            # 8. Infer missing totals (mathematically safe)
            draft = infer_missing_totals(draft)

            # 8b. Normalize tax data (reconstruct tax_breakdown from lines if possible)
            draft = normalize_tax_data(draft)

            # 10. Deterministic validation (on normalized draft)
            validation_result = self.validator(draft)

            # Update draft with validation results using replace
            draft = replace(
                draft,
                validation_status=validation_result.status,
                validation_errors=validation_result.errors,
                validation_warnings=validation_result.warnings,
            )

            # 11. Resolve supplier if we have tax_id
            if draft.has_supplier():
                resolution = await self.supplier_resolver.resolve(
                    tax_id=draft.supplier.tax_id,
                    name=draft.supplier.name,
                    address=draft.supplier.address,
                )

                draft = replace(
                    draft,
                    supplier_resolution_status=resolution.status,
                    supplier_dolibarr_id=resolution.supplier_dolibarr_id,
                    supplier_candidates=resolution.candidates,
                )
                
                # Update state with supplier info
                if resolution.supplier_dolibarr_id:
                    await self._update_document_state(document_hash, {
                        "supplier_dolibarr_id": resolution.supplier_dolibarr_id
                    })

                # DURABLE IDEMPOTENCY CHECK: Check if this invoice was already completed in Dolibarr
                if draft.has_supplier() and draft.invoice_number and draft.invoice_date:
                    existing_durable = await self.idempotency_manager.check_duplicate(
                        instance_id=self.company_context.instance_id,
                        supplier_tax_id=draft.supplier.tax_id,
                        supplier_invoice_number=draft.invoice_number,
                    )
                    
                    if existing_durable:
                        logger.info("duplicate_detected_durable_after_resolution",
                            document_hash=document_hash[:16],
                            supplier_tax_id=draft.supplier.tax_id,
                            supplier_invoice_number=draft.invoice_number,
                            existing_state=existing_durable.final_state)
                        
                        # Document already completed in Dolibarr - return appropriate response
                        if existing_durable.final_state == "COMPLETED":
                            return IngestionResult(
                                success=False,
                                error="Esta factura ya fue procesada completamente y está registrada en Dolibarr",
                                error_code="DOCUMENT_COMPLETED",
                            )
                        elif existing_durable.final_state == "INVOICE_CREATED":
                            if existing_durable.attachment_uploaded:
                                return IngestionResult(
                                    success=False,
                                    error="Esta factura ya fue procesada completamente",
                                    error_code="DOCUMENT_COMPLETED",
                                )
                            return IngestionResult(
                                success=False,
                                error="La factura ya existe en Dolibarr. Adjunto pendiente.",
                                error_code="INVOICE_EXISTS_ATTACHMENT_PENDING",
                            )
                        elif existing_durable.final_state == "SUPPLIER_CREATED":
                            return IngestionResult(
                                success=False,
                                error="El proveedor ya existe. Factura pendiente de creación.",
                                error_code="SUPPLIER_EXISTS_INVOICE_PENDING",
                            )

            # Generate preview text
            preview_text = self._generate_preview(draft)
            
            # Update state to REVIEW (preview ready for user confirmation)
            await self._update_document_status(document_hash, DocumentState.REVIEW)
            await self._update_document_state(document_hash, {
                "preview_text": preview_text
            })

            logger.info(
                "document_ingestion_completed",
                instance_id=self.company_context.instance_id,
                document_hash=document_hash[:16],
                filename=filename,
                classification=draft.classification.value,
                validation_status=draft.validation_status.value,
                supplier_status=draft.supplier_resolution_status.value,
                has_preview=bool(preview_text),
            )

            return IngestionResult(
                success=True,
                draft=draft,
                preview_text=preview_text,
                stored_path=stored_path,
            )

        except LocalModelUnavailableError:
            await self._handle_extraction_failure(document_hash, stored_path, 
                IngestionResult(success=False, error="Modelo local no disponible", error_code="LOCAL_MODEL_UNAVAILABLE"))
            return IngestionResult(
                success=False,
                error="Modelo local no disponible para procesar facturas",
                error_code="LOCAL_MODEL_UNAVAILABLE",
            )
        except Exception as e:
            logger.error(
                "document_ingestion_failed",
                instance_id=self.company_context.instance_id,
                document_hash=document_hash[:16],
                error=str(e),
            )
            await self._handle_processing_failure(document_hash, stored_path, str(e))
            return IngestionResult(
                success=False,
                error=f"Error procesando documento: {type(e).__name__}",
                error_code="INGESTION_ERROR",
            )

    async def _store_document(
        self,
        file_content: bytes,
        filename: str,
        document_hash: str,
    ) -> str:
        """Store document in instance-isolated pending directory."""
        # Use first 2 chars of hash for directory sharding
        hash_prefix = document_hash[:2]
        supplier_folder = "unknown"

        # Try to extract supplier tax_id from filename or use unknown
        # We'll organize by hash for now, supplier folder added after resolution
        pending_path = self.pending_dir / hash_prefix / document_hash
        pending_path.mkdir(parents=True, exist_ok=True)

        file_path = pending_path / filename
        file_path.write_bytes(file_content)

        logger.info(
            "document_stored_pending",
            instance_id=self.company_context.instance_id,
            document_hash=document_hash[:16],
            path=str(file_path),
        )

        return str(file_path)

    def _cleanup_stored_file(self, stored_path: str | None) -> None:
        """Clean up stored file on error."""
        if stored_path and os.path.exists(stored_path):
            try:
                os.unlink(stored_path)
                # Clean up empty parent directories
                parent = Path(stored_path).parent
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
            except Exception as e:
                logger.warning("cleanup_stored_file_failed", path=stored_path, error=str(e))

    def _generate_preview(self, draft: SupplierInvoiceDraft) -> str:
        """Generate human-readable preview for Telegram."""
        lines = []

        # Header
        lines.append("📄 <b>FACTURA DE PROVEEDOR</b>\n")

        # Supplier
        lines.append(f"<b>Proveedor:</b> {draft.get_supplier_display()}")
        lines.append(f"<b>CIF/NIF:</b> {draft.supplier.tax_id if draft.supplier and draft.supplier.tax_id else '—'}")
        lines.append(f"<b>Estado proveedor:</b> {draft.get_supplier_resolution_display()}")

        if draft.supplier_resolution_status == SupplierResolutionStatus.FOUND_NOT_SUPPLIER:
            lines.append("<b>⚠ El tercero existe pero no está habilitado como proveedor.</b>")
            lines.append("Se habilitará automáticamente al confirmar la factura.")

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

    # =========================================================================
    # DOCUMENT STATE MANAGEMENT (Idempotency & Workflow Tracking)
    # =========================================================================

    def _state_key(self, document_hash: str) -> str:
        """Generate Redis key for document state."""
        return f"hermes:{self.company_context.instance_id}:invoice_documents:{document_hash}"

    async def _get_document_state(self, document_hash: str) -> DocumentStateData | None:
        """Retrieve document state from Redis."""
        key = self._state_key(document_hash)
        data = self.redis.hgetall(key)
        if not data:
            return None
        return DocumentStateData.from_dict(data)

    async def _save_document_state(self, state: DocumentStateData) -> None:
        """Save document state to Redis with TTL."""
        key = self._state_key(state.document_hash)
        self.redis.hset(key, mapping=state.to_dict())
        self.redis.expire(key, DOCUMENT_STATE_TTL_SECONDS)

    async def _update_document_status(self, document_hash: str, status: DocumentState) -> None:
        """Update document status and updated_at timestamp."""
        key = self._state_key(document_hash)
        self.redis.hset(key, mapping={
            "status": status.value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        self.redis.expire(key, DOCUMENT_STATE_TTL_SECONDS)

    async def _update_document_state(self, document_hash: str, updates: dict[str, Any]) -> None:
        """Update multiple fields in document state."""
        key = self._state_key(document_hash)
        str_updates = {k: str(v) for k, v in updates.items()}
        str_updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.redis.hset(key, mapping=str_updates)
        self.redis.expire(self._state_key(document_hash), DOCUMENT_STATE_TTL_SECONDS)

    async def _handle_existing_document(self, state: DocumentStateData, file_content: bytes, filename: str, mime_type: str) -> IngestionResult:
        """Handle document that already exists in the system based on its state."""
        status = state.status
        
        if status == DocumentState.COMPLETED:
            logger.info("document_already_completed", document_hash=state.document_hash[:16])
            return IngestionResult(
                success=False,
                error="Esta factura ya fue procesada completamente y está registrada en Dolibarr",
                error_code="DOCUMENT_COMPLETED",
            )
        
        elif status == DocumentState.INVOICE_CREATED:
            logger.info("document_invoice_exists", document_hash=state.document_hash[:16])
            if state.attachment_uploaded:
                return IngestionResult(
                    success=False,
                    error="Esta factura ya fue procesada completamente",
                    error_code="DOCUMENT_COMPLETED",
                )
            return IngestionResult(
                success=False,
                error="La factura ya existe en Dolibarr. Adjunto pendiente.",
                error_code="INVOICE_EXISTS_ATTACHMENT_PENDING",
            )
        
        elif status == DocumentState.SUPPLIER_CREATED:
            logger.info("supplier_exists_invoice_pending", document_hash=state.document_hash[:16])
            return IngestionResult(
                success=False,
                error="El proveedor ya existe. Factura pendiente de creación.",
                error_code="SUPPLIER_EXISTS_INVOICE_PENDING",
            )
        
        elif status == DocumentState.ATTACHMENT_PENDING:
            logger.info("attachment_retry", document_hash=state.document_hash[:16])
            return IngestionResult(
                success=False,
                error="Factura creada. Adjunto pendiente de subida.",
                error_code="ATTACHMENT_PENDING",
            )
        
        elif status == DocumentState.REVIEW:
            logger.info("document_in_review", document_hash=state.document_hash[:16])
            return IngestionResult(
                success=False,
                error="Esta factura ya está pendiente de confirmación",
                error_code="DOCUMENT_IN_REVIEW",
            )
        
        elif status == DocumentState.PENDING_CONFIRMATION:
            logger.info("document_pending_confirmation", document_hash=state.document_hash[:16])
            return IngestionResult(
                success=False,
                error="Factura pendiente de confirmación de creación",
                error_code="PENDING_CONFIRMATION",
            )
        
        elif status == DocumentState.CONFIRMING:
            logger.info("document_confirming", document_hash=state.document_hash[:16])
            return IngestionResult(
                success=False,
                error="Factura en proceso de confirmación",
                error_code="CONFIRMING_IN_PROGRESS",
            )
        
        elif status == DocumentState.FAILED_RETRYABLE:
            if state.retry_count >= MAX_AUTO_RETRIES:
                logger.warning("max_retries_exceeded", document_hash=state.document_hash[:16], retry_count=state.retry_count)
                await self._update_document_state(state.document_hash, {
                    "status": DocumentState.FAILED_FINAL.value,
                    "last_error": "Max auto retries exceeded",
                    "retry_count": state.retry_count
                })
                return IngestionResult(
                    success=False,
                    error="Límite de reintentos automáticos alcanzado. Requiere intervención manual.",
                    error_code="MAX_RETRIES_EXCEEDED",
                )
            
            logger.info("retrying_failed_document", document_hash=state.document_hash[:16], retry_count=state.retry_count + 1)
            await self._update_document_state(state.document_hash, {
                "status": DocumentState.RECEIVED.value,
                "retry_count": state.retry_count + 1,
                "last_error": state.last_error
            })
            return await self._retry_ingestion(state.document_hash, state.correlation_id)
        
        elif status == DocumentState.FAILED_FINAL:
            logger.warning("document_failed_final", document_hash=state.document_hash[:16])
            return IngestionResult(
                success=False,
                error="Este documento falló definitivamente. Requiere intervención manual.",
                error_code="FAILED_FINAL",
            )
        
        elif status == DocumentState.CANCELLED:
            logger.info("cancelled_document_reingestion", document_hash=state.document_hash[:16])
            await self._update_document_state(state.document_hash, {
                "status": DocumentState.RECEIVED.value,
                "retry_count": 0,
                "last_error": None
            })
            return await self.ingest(state.document_hash, filename, mime_type)
        
        elif status == DocumentState.EXPIRED:
            logger.info("expired_document_reingestion", document_hash=state.document_hash[:16])
            await self._update_document_state(state.document_hash, {
                "status": DocumentState.RECEIVED.value,
                "retry_count": 0,
                "last_error": None
            })
            return await self.ingest(state.document_hash, filename, mime_type)
        
        elif status == DocumentState.PROCESSING:
            try:
                updated = datetime.fromisoformat(state.updated_at.replace('Z', '+00:00'))
                now = datetime.now(timezone.utc)
                if (now - updated).total_seconds() > PROCESSING_STALE_THRESHOLD_SECONDS:
                    logger.warning("stale_processing_detected", document_hash=state.document_hash[:16])
                    await self._update_document_state(state.document_hash, {
                        "status": DocumentState.FAILED_RETRYABLE.value,
                        "last_error": "Processing stale, marked for retry"
                    })
                    return await self._retry_ingestion(state.document_hash, state.correlation_id)
                else:
                    logger.info("document_currently_processing", document_hash=state.document_hash[:16])
                    return IngestionResult(
                        success=False,
                        error="Este documento ya se está procesando",
                        error_code="ALREADY_PROCESSING",
                    )
            except Exception:
                logger.warning("invalid_timestamp_retry", document_hash=state.document_hash[:16])
                return await self._retry_ingestion(state.document_hash, state.correlation_id)
        
        else:
            logger.warning("unknown_document_state", document_hash=state.document_hash[:16], status=status.value)
            return await self.ingest(state.document_hash, filename, mime_type)

    async def _retry_ingestion(self, document_hash: str, correlation_id: str) -> IngestionResult:
        """Retry ingestion for a document that was previously failed or stale."""
        state = await self._get_document_state(document_hash)
        if not state:
            return IngestionResult(success=False, error="Estado no encontrado para reintento", error_code="STATE_NOT_FOUND")
        
        await self._update_document_state(document_hash, {
            "status": DocumentState.RECEIVED.value,
            "retry_count": state.retry_count + 1,
            "correlation_id": correlation_id
        })
        
        return IngestionResult(
            success=False,
            error="Reintento requerido. Reenvíe el documento.",
            error_code="RETRY_REQUIRED",
        )

    async def _handle_extraction_failure(self, document_hash: str, stored_path: str, extraction_result: IngestionResult) -> None:
        """Handle extraction failure - mark document as failed retryable."""
        self._cleanup_stored_file(stored_path)
        await self._update_document_state(document_hash, {
            "status": DocumentState.FAILED_RETRYABLE.value,
            "last_error": extraction_result.error or "Extraction failed",
            "retry_count": 0
        })

    async def _handle_processing_failure(self, document_hash: str, stored_path: str | None, error: str) -> None:
        """Handle general processing failure - mark as failed retryable."""
        if stored_path:
            self._cleanup_stored_file(stored_path)
        await self._update_document_state(document_hash, {
            "status": DocumentState.FAILED_RETRYABLE.value,
            "last_error": error,
            "retry_count": 0
        })

    async def mark_supplier_created(self, document_hash: str, supplier_dolibarr_id: int) -> None:
        """Mark supplier as created in Dolibarr."""
        await self._update_document_state(document_hash, {
            "status": DocumentState.SUPPLIER_CREATED.value,
            "supplier_dolibarr_id": supplier_dolibarr_id
        })

    async def mark_invoice_created(self, document_hash: str, invoice_dolibarr_id: int, dolibarr_invoice_ref: str, dolibarr_invoice_id: int) -> None:
        """Mark invoice as created in Dolibarr."""
        await self._update_document_state(document_hash, {
            "status": DocumentState.INVOICE_CREATED.value,
            "invoice_dolibarr_id": invoice_dolibarr_id,
            "dolibarr_invoice_ref": dolibarr_invoice_ref,
            "dolibarr_invoice_id": dolibarr_invoice_id
        })

    async def mark_attachment_uploaded(self, document_hash: str) -> None:
        """Mark attachment as uploaded."""
        await self._update_document_state(document_hash, {
            "status": DocumentState.COMPLETED.value,
            "attachment_uploaded": True
        })

    async def mark_completed(self, document_hash: str) -> None:
        """Mark document as fully completed."""
        await self._update_document_state(document_hash, {
            "status": DocumentState.COMPLETED.value,
            "attachment_uploaded": True
        })

    async def mark_cancelled(self, document_hash: str) -> None:
        """Mark document as cancelled."""
        await self._update_document_status(document_hash, DocumentState.CANCELLED)

    async def check_duplicate_in_dolibarr(self, draft: SupplierInvoiceDraft) -> bool:
        """Check if invoice already exists in Dolibarr to prevent duplicates."""
        if not draft.has_supplier() or not draft.invoice_number or not draft.invoice_date:
            return False
        
        try:
            resolution = await self.supplier_resolver.resolve(
                tax_id=draft.supplier.tax_id,
                name=draft.supplier.name,
                address=draft.supplier.address,
            )
            
            if not resolution.supplier_dolibarr_id:
                return False
            
            from core.integrations.dolibarr.client import DolibarrClient
            from core.hermes.identity_store import IdentityStore
            
            identity_store = IdentityStore(self.company_context.instance_id)
            identity = identity_store.get(self.user_context.telegram_user_id)
            
            if not identity:
                return False
            
            dolibarr = self.company_context.create_dolibarr_client_for_user(identity)
            
            async with dolibarr as client:
                invoices = await client.list_supplier_invoices(
                    thirdparty_id=resolution.supplier_dolibarr_id,
                    limit=500,
                )
                
                invoice_number_upper = draft.invoice_number.upper()
                for invoice in invoices:
                    ref = (invoice.get("ref") or "").upper()
                    ref_supplier = (invoice.get("ref_supplier") or "").upper()
                    if ref == invoice_number_upper or ref_supplier == invoice_number_upper:
                        logger.warning("duplicate_invoice_detected_in_dolibarr",
                            supplier_id=resolution.supplier_dolibarr_id,
                            invoice_number=draft.invoice_number)
                        return True
                
                return False
        except Exception as e:
            logger.warning("duplicate_check_failed", error=str(e))
            return False

    async def reconcile_with_dolibarr(self, draft: SupplierInvoiceDraft) -> dict[str, Any] | None:
        """Attempt to find existing invoice in Dolibarr for reconciliation."""
        if not draft.has_supplier() or not draft.invoice_number or not draft.invoice_date:
            return None
        
        try:
            resolution = await self.supplier_resolver.resolve(
                tax_id=draft.supplier.tax_id,
                name=draft.supplier.name,
                address=draft.supplier.address,
            )
            
            if not resolution.supplier_dolibarr_id:
                return None
            
            from core.hermes.identity_store import IdentityStore
            identity_store = IdentityStore(self.company_context.instance_id)
            identity = identity_store.get(self.user_context.telegram_user_id)
            
            if not identity:
                return None
            
            dolibarr = self.company_context.create_dolibarr_client_for_user(identity)
            
            async with dolibarr as client:
                invoices = await client.list_supplier_invoices(
                    thirdparty_id=resolution.supplier_dolibarr_id,
                    limit=500,
                )
                
                invoice_number_upper = draft.invoice_number.upper()
                for invoice in invoices:
                    ref = (invoice.get("ref") or "").upper()
                    ref_supplier = (invoice.get("ref_supplier") or "").upper()
                    if ref == invoice_number_upper or ref_supplier == invoice_number_upper:
                        logger.info("reconciliation_found_existing_invoice",
                            supplier_id=resolution.supplier_dolibarr_id,
                            invoice_number=draft.invoice_number,
                            dolibarr_id=invoice.get("id") or invoice.get("rowid"))
                        return {
                            "dolibarr_id": invoice.get("id") or invoice.get("rowid"),
                            "ref": invoice.get("ref"),
                            "ref_supplier": invoice.get("ref_supplier"),
                            "status": invoice.get("status"),
                            "total": invoice.get("total"),
                        }
            
            return None
        except Exception as e:
            logger.warning("reconciliation_failed", error=str(e))
            return None


# =========================================================================
# FACTORY
# =========================================================================

def create_document_ingestion_service(
    company_context: CompanyContext,
    user_context: UserContext,
    telegram_client,
) -> DocumentIngestionService:
    """Factory for DocumentIngestionService."""
    return DocumentIngestionService(company_context, user_context, telegram_client)