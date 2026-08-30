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
- Idempotency via document hash + Redis
"""

from __future__ import annotations

import hashlib
import os
import shutil
import structlog
from datetime import datetime
from pathlib import Path
from typing import Any

import redis

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.config import get_global_settings
from .models import (
    SupplierInvoiceDraft,
    DocumentClassification,
    SupplierResolutionStatus,
    ValidationStatus,
    InvoiceFieldSource,
)
from .extractor import InvoiceExtractor, LocalModelUnavailableError
from .validator import validate_invoice, infer_missing_totals
from .supplier_resolver import SupplierResolver

logger = structlog.get_logger()


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
    3. Check idempotency (hash already processed)
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

        # Redis for idempotency
        settings = get_global_settings()
        self.redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            db=company_context.instance_config.get_redis_db(),
            decode_responses=True,
        )

        # Document storage paths
        self.documents_root = Path(company_context.instance_config.documents_path)
        self.pending_dir = self.documents_root / "pending"
        self.processed_dir = self.documents_root / "processed"
        self.rejected_dir = self.documents_root / "rejected"

        # Ensure directories exist
        for d in [self.pending_dir, self.processed_dir, self.rejected_dir]:
            d.mkdir(parents=True, exist_ok=True)

    async def ingest(self, file_id: str, filename: str, mime_type: str) -> IngestionResult:
        """
        Main ingestion entry point.

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

        # 2. Validate file size (max 10MB for DEVELOPMENT)
        max_size = self.company_context.instance_config.telegram.max_file_size_mb * 1024 * 1024
        if len(file_content) > max_size:
            return IngestionResult(
                success=False,
                error=f"Archivo demasiado grande ({len(file_content) / 1024 / 1024:.1f} MB). Máximo: {max_size / 1024 / 1024:.0f} MB",
                error_code="FILE_TOO_LARGE",
            )

        # 3. Validate MIME type
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

        # 4. Compute SHA-256 hash
        document_hash = hashlib.sha256(file_content).hexdigest()

        # 5. Check idempotency (document hash already processed)
        idempotency_key = f"invoice:doc:{document_hash}"
        existing = self.redis.get(idempotency_key)
        if existing:
            logger.info(
                "duplicate_document_rejected",
                instance_id=self.company_context.instance_id,
                document_hash=document_hash[:16],
                existing_status=existing,
            )
            return IngestionResult(
                success=False,
                error="Este documento ya fue procesado anteriormente",
                error_code="DUPLICATE_DOCUMENT",
            )

        # 6. Store original document (pending)
        stored_path = await self._store_document(
            file_content=file_content,
            filename=filename,
            document_hash=document_hash,
        )

        try:
            # 7. Extract invoice data
            extraction_result = await self.extractor.extract(file_content, filename, mime_type)

            if not extraction_result.success:
                # Clean up stored file on extraction failure
                self._cleanup_stored_file(stored_path)
                return IngestionResult(
                    success=False,
                    error=extraction_result.error,
                    error_code=extraction_result.error_code,
                )

            draft = extraction_result.draft

            # 8. Infer missing totals (mathematically safe)
            draft = infer_missing_totals(draft)

            # 9. Deterministic validation
            validation_result = self.validator(draft)

            # Update draft with validation results
            draft = SupplierInvoiceDraft(
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
                subtotal=draft.subtotal,
                subtotal_source=draft.subtotal_source,
                tax_total=draft.tax_total,
                tax_total_source=draft.tax_total_source,
                withholding_total=draft.withholding_total,
                withholding_total_source=draft.withholding_total_source,
                total=draft.total,
                total_source=draft.total_source,
                supplier_resolution_status=draft.supplier_resolution_status,
                supplier_dolibarr_id=draft.supplier_dolibarr_id,
                supplier_candidates=draft.supplier_candidates,
                validation_status=validation_result.status,
                validation_errors=validation_result.errors,
                validation_warnings=validation_result.warnings,
                extraction_confidence=draft.extraction_confidence,
                extraction_model=draft.extraction_model,
                extraction_raw_text_chars=draft.extraction_raw_text_chars,
                inference_count=draft.inference_count,
                instance_id=draft.instance_id,
                received_at=draft.received_at,
                correlation_id=draft.correlation_id,
            )

            # 10. Resolve supplier if we have tax_id
            if draft.has_supplier():
                resolution = await self.supplier_resolver.resolve(
                    tax_id=draft.supplier.tax_id,
                    name=draft.supplier.name,
                    address=draft.supplier.address,
                )

                draft = SupplierInvoiceDraft(
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
                    subtotal=draft.subtotal,
                    subtotal_source=draft.subtotal_source,
                    tax_total=draft.tax_total,
                    tax_total_source=draft.tax_total_source,
                    withholding_total=draft.withholding_total,
                    withholding_total_source=draft.withholding_total_source,
                    total=draft.total,
                    total_source=draft.total_source,
                    supplier_resolution_status=resolution.status,
                    supplier_dolibarr_id=resolution.supplier_dolibarr_id,
                    supplier_candidates=resolution.candidates,
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

            # 11. Generate preview text
            preview_text = self._generate_preview(draft)

            # 12. Mark document as processed in Redis (TTL 24h)
            self.redis.setex(idempotency_key, 86400, "preview_ready")

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
            self._cleanup_stored_file(stored_path)
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
            self._cleanup_stored_file(stored_path)
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
# FACTORY
# =========================================================================

def create_document_ingestion_service(
    company_context: CompanyContext,
    user_context: UserContext,
    telegram_client,
) -> DocumentIngestionService:
    """Factory for DocumentIngestionService."""
    return DocumentIngestionService(company_context, user_context, telegram_client)