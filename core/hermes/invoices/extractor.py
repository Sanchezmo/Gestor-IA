"""
Invoice Extractor - LOCAL_ONLY Ollama extraction.

Ported from Transvega Animal:
- agents/invoice_processing/agent.py: _extract_structured_data, _validate_with_pydantic

Adapted for Gestor-IA:
- Uses AIProvider abstraction (OllamaProvider with LOCAL_ONLY)
- Native structured output with JSON Schema (format=INVOICE_JSON_SCHEMA)
- think=false, num_predict=2048, timeout=600s
- FAIL CLOSED if local model not available
- NO cloud fallback
"""

from __future__ import annotations

import json
import tempfile
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import structlog

from core.hermes.ai import AIProvider, create_ai_provider
from core.hermes.instance_config import InstanceConfig
from .models import (
    SupplierInvoiceDraft,
    SupplierInfo,
    InvoiceLine,
    TaxBreakdownItem,
    WithholdingBreakdownItem,
    DocumentClassification,
    InvoiceFieldSource,
    ExtractionResult,
    normalize_tax_id,
)

logger = structlog.get_logger()


# =========================================================================
# JSON SCHEMA FOR STRUCTURED EXTRACTION (Ollama native)
# =========================================================================

INVOICE_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "supplier": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "tax_id": {"type": "string"},
                "address": {"type": ["string", "null"]},
                "email": {"type": ["string", "null"]},
                "phone": {"type": ["string", "null"]},
            },
            "required": ["name", "tax_id"],
        },
        "invoice": {
            "type": "object",
            "properties": {
                "number": {"type": ["string", "null"]},
                "date": {"type": ["string", "null"], "format": "date"},
                "due_date": {"type": ["string", "null"], "format": "date"},
            },
            "required": ["number", "date"],
        },
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number"},
                    "vat_rate": {"type": ["number", "null"]},
                    "discount_percent": {"type": "number", "default": 0},
                    "product_ref": {"type": ["string", "null"]},
                },
                "required": ["description", "quantity", "unit_price"],
            },
            "minItems": 1,
        },
        "taxes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "rate": {"type": "number"},
                    "base": {"type": "number"},
                    "amount": {"type": "number"},
                },
                "required": ["rate", "amount"],
            },
        },
        "withholdings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "rate": {"type": "number"},
                    "base": {"type": "number"},
                    "amount": {"type": "number"},
                },
                "required": ["rate", "amount"],
            },
        },
        "subtotal": {"type": "number"},
        "tax_total": {"type": "number"},
        "withholding_total": {"type": "number", "default": 0},
        "total": {"type": "number"},
        "currency": {"type": "string", "default": "EUR"},
        "payment_terms": {"type": ["string", "null"]},
        "payment_method": {"type": ["string", "null"]},
        "notes": {"type": ["string", "null"]},
    },
    "required": ["supplier", "invoice", "lines", "subtotal", "tax_total", "total", "currency"],
}


DOCUMENT_CLASSIFICATION_SCHEMA = {
    "type": "object",
    "properties": {
        "document_type": {
            "type": "string",
            "enum": ["single_invoice", "multi_document", "not_invoice", "unknown"],
        },
        "invoice_count": {"type": "integer", "minimum": 0},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "signals": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["document_type", "invoice_count", "confidence", "signals"],
}


# =========================================================================
# EXTRACTION ERRORS
# =========================================================================

class LocalModelUnavailableError(Exception):
    """Raised when local Ollama model is not available."""
    pass


class ExtractionTimeoutError(Exception):
    """Raised when extraction exceeds timeout."""
    pass


# =========================================================================
# INVOICE EXTRACTOR
# =========================================================================

class InvoiceExtractor:
    """
    Extracts structured supplier invoice data from documents using LOCAL_ONLY Ollama.

    Flow:
    1. Extract text from PDF (native text layer or OCR via vision)
    2. Classify document (heuristic for text, LLM for scanned)
    3. Extract structured data via Ollama with JSON Schema
    4. Parse and validate into SupplierInvoiceDraft

    All processing is LOCAL_ONLY - NO cloud fallback.
    """

    def __init__(
        self,
        instance_config: InstanceConfig,
        ollama_timeout: float = 600.0,
        ocr_dpi: int = 150,
        max_pages: int = 10,
    ):
        self.instance_config = instance_config
        self.ollama_timeout = ollama_timeout
        self.ocr_dpi = ocr_dpi
        self.max_pages = max_pages

        # Create LOCAL_ONLY AI provider for invoice processing
        ai_config = instance_config.ai
        if ai_config.default_policy != "LOCAL_ONLY":
            logger.warning(
                "invoice_extractor_policy_not_local_only",
                instance_id=instance_config.instance_id,
                policy=ai_config.default_policy,
            )

        self.provider: AIProvider = create_ai_provider(
            provider_type="ollama",
            endpoint=ai_config.ollama_endpoint,
            model=ai_config.ollama_model,
            vision_model=ai_config.ollama_vision_model or ai_config.ollama_model,
            timeout=ollama_timeout,
        )

    async def extract(self, file_content: bytes, filename: str, mime_type: str) -> ExtractionResult:
        """
        Main entry point: extract invoice data from document.

        Args:
            file_content: Raw document bytes
            filename: Original filename
            mime_type: MIME type (application/pdf, image/png, image/jpeg)

        Returns:
            ExtractionResult with draft or error
        """
        import time
        start_time = time.perf_counter()

        # Check Ollama model readiness
        if not await self._check_model_ready():
            return ExtractionResult(
                success=False,
                error="Modelo Ollama local no disponible",
                error_code="LOCAL_MODEL_UNAVAILABLE",
                requires_review=False,
            )

        temp_file_path = None
        raw_text = ""
        has_native_text = False
        native_text_chars = 0
        inference_count = 0
        page_images: list[bytes] = []
        page_count = 1

        try:
            # Save to temp file for processing
            suffix = Path(filename).suffix or ".pdf"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(file_content)
                temp_file_path = tmp.name

            # Extract text based on mime type
            if mime_type == "application/pdf" or filename.lower().endswith(".pdf"):
                raw_text, has_native_text, page_images, page_count = await self._process_pdf(temp_file_path)
                native_text_chars = len(raw_text) if raw_text else 0
            else:
                # Image - direct OCR
                page_count = 1
                raw_text = await self._ocr_via_vision(temp_file_path)
                inference_count += 1
                native_text_chars = len(raw_text) if raw_text else 0

            if not raw_text or len(raw_text.strip()) < 20:
                return ExtractionResult(
                    success=False,
                    error="No se pudo extraer texto del documento",
                    error_code="TEXT_EXTRACTION_FAILED",
                    requires_review=True,
                )

            # Document classification
            classification = await self._classify_document(raw_text, page_images, page_count, has_native_text)

            # Handle non-invoice documents
            if classification.document_type == DocumentClassification.NOT_INVOICE:
                return ExtractionResult(
                    success=False,
                    error="El documento no parece una factura de proveedor",
                    error_code="NOT_INVOICE",
                    requires_review=False,
                )

            if classification.document_type == DocumentClassification.MULTI_DOCUMENT:
                return ExtractionResult(
                    success=False,
                    error="El documento parece contener varios documentos. Envíe cada factura por separado.",
                    error_code="MULTI_DOCUMENT",
                    requires_review=True,
                )

            if classification.document_type == DocumentClassification.UNKNOWN:
                return ExtractionResult(
                    success=False,
                    error="No se puede determinar si es una factura válida",
                    error_code="DOCUMENT_UNKNOWN",
                    requires_review=True,
                )

            # Structured extraction
            extracted_data = await self._extract_structured_data(raw_text)
            inference_count += 1

            # Build draft
            draft = self._build_draft(
                extracted_data=extracted_data,
                file_content=file_content,
                filename=filename,
                mime_type=mime_type,
                raw_text=raw_text,
                has_native_text=has_native_text,
                native_text_chars=native_text_chars,
                inference_count=inference_count,
                page_count=page_count,
                classification=classification,
            )

            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            logger.info(
                "invoice_extraction_completed",
                instance_id=self.instance_config.instance_id,
                filename=filename,
                elapsed_ms=elapsed_ms,
                classification=classification.document_type.value,
                confidence=str(classification.classification_confidence),
                has_supplier=draft.has_supplier(),
                line_count=len(draft.lines),
            )

            return ExtractionResult(success=True, draft=draft)

        except LocalModelUnavailableError:
            return ExtractionResult(
                success=False,
                error="Modelo local no disponible",
                error_code="LOCAL_MODEL_UNAVAILABLE",
                requires_review=False,
            )
        except ExtractionTimeoutError:
            return ExtractionResult(
                success=False,
                error="Tiempo de procesamiento agotado",
                error_code="EXTRACTION_TIMEOUT",
                requires_review=False,
            )
        except Exception as e:
            logger.error(
                "invoice_extraction_failed",
                instance_id=self.instance_config.instance_id,
                filename=filename,
                error=str(e),
            )
            return ExtractionResult(
                success=False,
                error=f"Error en extracción: {type(e).__name__}",
                error_code="EXTRACTION_ERROR",
                requires_review=True,
            )
        finally:
            # Cleanup temp file
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                except Exception:
                    pass

    # =========================================================================
    # PDF PROCESSING
    # =========================================================================

    async def _process_pdf(self, file_path: str) -> tuple[str, bool, list[bytes], int]:
        """Process PDF: try text layer first, then OCR if needed."""
        raw_text = ""
        has_native_text = False
        page_images = []
        page_count = 0

        try:
            doc = fitz.open(file_path)
            page_count = len(doc)

            # Limit pages
            if page_count > self.max_pages:
                page_count = self.max_pages
                doc = fitz.open(file_path)  # Reopen for limited processing

            # Try native text extraction first
            text_parts = []
            for page_num in range(page_count):
                page = doc[page_num]
                text = page.get_text("text")
                if text.strip():
                    text_parts.append(text)

            raw_text = "\n\n".join(text_parts)

            if raw_text and len(raw_text.strip()) >= 50:
                has_native_text = True
                logger.debug("pdf_native_text_extracted", chars=len(raw_text), pages=page_count)
            else:
                # No text layer or very little - render for OCR
                logger.info("pdf_no_text_layer_rendering_for_ocr", pages=page_count)
                for page_num in range(page_count):
                    page = doc[page_num]
                    mat = fitz.Matrix(self.ocr_dpi / 72.0, self.ocr_dpi / 72.0)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    page_images.append(pix.tobytes("png"))

            doc.close()

        except Exception as e:
            logger.error("pdf_processing_failed", error=str(e))
            raise

        return raw_text, has_native_text, page_images, page_count

    async def _ocr_via_vision(self, image_path: str) -> str:
        """OCR via Ollama vision model."""
        try:
            result = await self.provider.vision(
                image_path=image_path,
                prompt="Extrae TODO el texto de esta imagen de factura. Devuelve solo el texto crudo, sin comentarios.",
                request_timeout=self.ollama_timeout,
            )
            return str(result.get("text", "")).strip()
        except Exception as e:
            logger.error("ocr_vision_failed", error=str(e))
            raise ExtractionTimeoutError("OCR timeout") from e

    # =========================================================================
    # DOCUMENT CLASSIFICATION
    # =========================================================================

    def _classify_heuristic(self, raw_text: str, page_count: int) -> tuple[DocumentClassification, Decimal, list[str]]:
        """Heuristic classification for native text PDFs."""
        text_lower = raw_text.lower()
        signals = []

        strong_signals = [
            ("factura", "factura keyword"),
            ("invoice", "invoice keyword"),
            ("número de factura", "invoice number ES"),
            ("invoice number", "invoice number EN"),
            ("nº factura", "invoice number short ES"),
            ("cif", "CIF tax ID"),
            ("nif", "NIF tax ID"),
            ("vat", "VAT tax"),
            ("iva", "IVA tax"),
            ("base imponible", "taxable base"),
            ("subtotal", "subtotal"),
            ("total", "total amount"),
            ("vencimiento", "due date"),
            ("forma de pago", "payment terms"),
            ("proveedor", "supplier"),
            ("cliente", "customer"),
            ("fecha de factura", "invoice date"),
        ]

        supporting_signals = [
            ("dirección", "address"),
            ("teléfono", "phone"),
            ("email", "email"),
            ("iban", "IBAN"),
            ("cuenta bancaria", "bank account"),
        ]

        strong_count = 0
        for keyword, signal_name in strong_signals:
            if keyword in text_lower:
                signals.append(signal_name)
                strong_count += 1

        supporting_count = 0
        for keyword, signal_name in supporting_signals:
            if keyword in text_lower:
                signals.append(signal_name)
                supporting_count += 1

        # Count invoice headers
        import re
        invoice_headers = re.findall(
            r'(?:^|\n)\s*(?:factura|invoice)\s*[#:nº]', raw_text, re.IGNORECASE
        )
        header_count = len(invoice_headers)

        if strong_count >= 3:
            if header_count >= 2:
                return (
                    DocumentClassification.MULTI_DOCUMENT,
                    Decimal("0.85"),
                    signals,
                )

            if strong_count >= 4:
                return (
                    DocumentClassification.SINGLE_INVOICE,
                    min(Decimal("0.70") + Decimal(str(strong_count)) * Decimal("0.05"), Decimal("0.95")),
                    signals,
                )

            return (
                DocumentClassification.SINGLE_INVOICE,
                Decimal("0.60"),
                signals,
            )

        elif strong_count >= 1 and supporting_count >= 2:
            return (
                DocumentClassification.SINGLE_INVOICE,
                Decimal("0.50"),
                signals,
            )

        elif strong_count == 0 and supporting_count == 0:
            return (
                DocumentClassification.NOT_INVOICE,
                Decimal("0.70"),
                signals,
            )

        else:
            return (
                DocumentClassification.UNKNOWN,
                Decimal("0.40"),
                signals,
            )

    async def _classify_llm(self, page_images: list[bytes], page_count: int) -> tuple[DocumentClassification, Decimal, list[str]]:
        """LLM classification for scanned documents."""
        if not page_images:
            return DocumentClassification.UNKNOWN, Decimal("0"), []

        # Use first page for classification
        import tempfile
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(page_images[0])
                temp_path = tmp.name

            result = await self.provider.vision(
                image_path=temp_path,
                prompt="""
Clasifica este documento. Devuelve SOLO JSON:
{
  "document_type": "single_invoice" | "multi_document" | "not_invoice" | "unknown",
  "invoice_count": 0 | 1 | 2+,
  "confidence": 0.0-1.0,
  "signals": ["signal1", "signal2"]
}
Busca: encabezados factura, números factura, CIF/NIF/VAT, base imponible, IVA, total, proveedor/cliente, forma pago.
Múltiples facturas = varios números/encabezados distintos.
""",
                request_timeout=60,
            )

            json_str = result.get("text", "").strip()
            data = json.loads(json_str)

            return (
                DocumentClassification(data["document_type"]),
                Decimal(str(data["confidence"])),
                data.get("signals", []),
            )

        except Exception as e:
            logger.warning("llm_classification_failed", error=str(e))
            return DocumentClassification.UNKNOWN, Decimal("0"), []
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    async def _classify_document(
        self,
        raw_text: str,
        page_images: list[bytes],
        page_count: int,
        has_native_text: bool,
    ) -> DocumentClassification:
        """Main classification entry point."""
        if has_native_text and raw_text and len(raw_text.strip()) >= 50:
            doc_type, confidence, signals = self._classify_heuristic(raw_text, page_count)
        else:
            doc_type, confidence, signals = await self._classify_llm(page_images, page_count)

        return DocumentClassification(
            document_type=doc_type,
            classification_confidence=confidence,
            classification_signals=signals,
        )

    # =========================================================================
    # STRUCTURED EXTRACTION
    # =========================================================================

    async def _extract_structured_data(self, raw_text: str) -> dict[str, Any]:
        """Extract structured data via Ollama with native JSON Schema."""
        prompt = f"""
Extrae la información de la factura de proveedor del siguiente texto y devuelve JSON según el esquema.
Texto:
\"\"\"{raw_text}\"\"\"
Devuelve SOLO el JSON válido, sin texto adicional.
"""

        try:
            result = await self.provider.generate(
                prompt=prompt,
                temperature=0.1,
                num_predict=2048,
                think=False,
                format=json.dumps(INVOICE_JSON_SCHEMA),
                request_timeout=self.ollama_timeout,
            )

            json_str = result.get("text", "").strip()
            data = json.loads(json_str)
            return data

        except json.JSONDecodeError as e:
            logger.error("structured_extraction_invalid_json", error=str(e), response=json_str[:500])
            raise
        except Exception as e:
            logger.error("structured_extraction_failed", error=str(e))
            raise

    # =========================================================================
    # DRAFT BUILDING
    # =========================================================================

    def _build_draft(
        self,
        extracted_data: dict[str, Any],
        file_content: bytes,
        filename: str,
        mime_type: str,
        raw_text: str,
        has_native_text: bool,
        native_text_chars: int,
        inference_count: int,
        page_count: int,
        classification: DocumentClassification,
    ) -> SupplierInvoiceDraft:
        """Build SupplierInvoiceDraft from extracted data."""

        # Supplier
        supplier_data = extracted_data.get("supplier", {})
        supplier = SupplierInfo(
            name=supplier_data.get("name", ""),
            tax_id=normalize_tax_id(supplier_data.get("tax_id", "")),
            address=supplier_data.get("address"),
            email=supplier_data.get("email"),
            phone=supplier_data.get("phone"),
        )

        # Invoice header
        invoice_data = extracted_data.get("invoice", {})
        invoice_number = invoice_data.get("number")
        invoice_date = self._parse_date(invoice_data.get("date"))
        due_date = self._parse_date(invoice_data.get("due_date"))

        # Lines
        lines = []
        for line_data in extracted_data.get("lines", []):
            line = InvoiceLine(
                description=line_data.get("description", ""),
                quantity=Decimal(str(line_data.get("quantity", 1))),
                unit_price=Decimal(str(line_data.get("unit_price", 0))),
                vat_rate=Decimal(str(line_data.get("vat_rate", 21))) if line_data.get("vat_rate") else Decimal("21"),
                discount_percent=Decimal(str(line_data.get("discount_percent", 0))),
                product_ref=line_data.get("product_ref"),
            )
            lines.append(line)

        # Tax breakdown
        tax_breakdown = []
        for tax_data in extracted_data.get("taxes", []):
            tax_breakdown.append(TaxBreakdownItem(
                rate=Decimal(str(tax_data.get("rate", 0))),
                base=Decimal(str(tax_data.get("base", 0))),
                amount=Decimal(str(tax_data.get("amount", 0))),
                source=InvoiceFieldSource.KNOWN,
            ))

        # Withholding breakdown
        withholding_breakdown = []
        for wh_data in extracted_data.get("withholdings", []):
            withholding_breakdown.append(WithholdingBreakdownItem(
                rate=Decimal(str(wh_data.get("rate", 0))),
                base=Decimal(str(wh_data.get("base", 0))),
                amount=Decimal(str(wh_data.get("amount", 0))),
                source=InvoiceFieldSource.KNOWN,
            ))

        # Totals
        subtotal = Decimal(str(extracted_data.get("subtotal", 0)))
        tax_total = Decimal(str(extracted_data.get("tax_total", 0)))
        withholding_total = Decimal(str(extracted_data.get("withholding_total", 0)))
        total = Decimal(str(extracted_data.get("total", 0)))
        currency = extracted_data.get("currency", "EUR")

        # Document hash
        import hashlib
        document_hash = hashlib.sha256(file_content).hexdigest()

        from datetime import datetime
        return SupplierInvoiceDraft(
            document_hash=document_hash,
            document_filename=filename,
            document_mime_type=mime_type,
            document_size_bytes=len(file_content),
            page_count=page_count,
            classification=classification.document_type,
            classification_confidence=classification.classification_confidence,
            classification_signals=classification.classification_signals,
            supplier=supplier,
            invoice_number=invoice_number,
            invoice_number_source=InvoiceFieldSource.KNOWN if invoice_number else InvoiceFieldSource.UNKNOWN,
            invoice_date=invoice_date,
            invoice_date_source=InvoiceFieldSource.KNOWN if invoice_date else InvoiceFieldSource.UNKNOWN,
            due_date=due_date,
            due_date_source=InvoiceFieldSource.KNOWN if due_date else InvoiceFieldSource.UNKNOWN,
            currency=currency,
            payment_terms=extracted_data.get("payment_terms"),
            payment_method=extracted_data.get("payment_method"),
            notes=extracted_data.get("notes"),
            lines=lines,
            tax_breakdown=tax_breakdown,
            withholding_breakdown=withholding_breakdown,
            subtotal=subtotal,
            subtotal_source=InvoiceFieldSource.KNOWN,
            tax_total=tax_total,
            tax_total_source=InvoiceFieldSource.KNOWN,
            withholding_total=withholding_total,
            withholding_total_source=InvoiceFieldSource.KNOWN if withholding_total > 0 else InvoiceFieldSource.UNKNOWN,
            total=total,
            total_source=InvoiceFieldSource.KNOWN,
            extraction_confidence=Decimal("0.8"),  # Default, could be refined
            extraction_model=self.instance_config.ai.ollama_model,
            extraction_raw_text_chars=native_text_chars,
            inference_count=inference_count,
            instance_id=self.instance_config.instance_id,
            received_at=datetime.utcnow().isoformat(),
        )

    def _parse_date(self, date_str: str | None) -> Any:
        """Parse date string to date object."""
        if not date_str:
            return None
        try:
            # Try ISO format first
            from datetime import date
            return date.fromisoformat(date_str)
        except Exception:
            # Try common formats
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
                try:
                    from datetime import datetime
                    return datetime.strptime(date_str, fmt).date()
                except Exception:
                    continue
        return None

    async def _check_model_ready(self) -> bool:
        """Check if Ollama model is ready."""
        try:
            # Simple health check - try to generate a minimal response
            result = await self.provider.generate(
                prompt="OK",
                num_predict=1,
                request_timeout=10,
            )
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        """Close provider connections."""
        await self.provider.aclose()