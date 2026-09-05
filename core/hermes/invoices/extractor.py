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
import os
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf
import structlog

from core.hermes.ai import AIProvider, create_ai_provider
from core.hermes.instance_config import InstanceConfig

from .models import (
    ClassificationResult,
    DocumentClassification,
    ExtractionResult,
    InvoiceFieldSource,
    InvoiceLine,
    SupplierInfo,
    SupplierInvoiceDraft,
    TaxBreakdownItem,
    WithholdingBreakdownItem,
    normalize_tax_id,
)

logger = structlog.get_logger()


# =========================================================================
# PDF TEXT NORMALIZATION (Scientific Notation in Percentages)
# =========================================================================
#
# PyMuPDF sometimes extracts percentages like "10%" as "1E+1%" (scientific notation).
# This function normalizes scientific notation in percentage contexts ONLY,
# avoiding false positives on alphanumeric references.
#
# Examples:
#   "1E+1%"    -> "10%"
#   "2.1E+1%"  -> "21%"
#   "4E+0%"    -> "4%"
#   "1.5E+1%"  -> "15%"
#   "1e+1%"    -> "10%"
#   "1E1%"     -> "10%"
#   "1.0E+1%"  -> "10%"
#
# Does NOT match:
#   "B12345678" (CIF)
#   "TH-2026-314" (invoice number)
#   "1E calle ejemplo" (no % suffix)
#   "referencias alfanuméricas" (no scientific notation)

import re

_SCIENTIFIC_PERCENT_PATTERN = re.compile(
    r'(\d+(?:\.\d+)?)E([+-]?\d+)%',
    re.IGNORECASE
)


def normalize_pdf_text(text: str) -> str:
    """
    Normalize scientific notation in percentage contexts within extracted PDF text.

    Args:
        text: Raw text extracted from PDF

    Returns:
        Text with scientific notation percentages normalized (e.g., "1E+1%" -> "10%")
    """
    if not text:
        return text

    def _replace_scientific_percent(match: re.Match) -> str:
        mantissa = Decimal(match.group(1))
        exponent = int(match.group(2))
        value = mantissa * (Decimal(10) ** exponent)
        # Format without scientific notation, remove trailing .0
        if value == value.to_integral_value():
            return f"{value:.0f}%"
        return f"{value}%"

    normalized = _SCIENTIFIC_PERCENT_PATTERN.sub(_replace_scientific_percent, text)
    return normalized


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
            "minItems": 1,
        },
        "withholdings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "concept": {"type": "string", "description": "Tipo de retención: IRPF, IVA soportado, etc."},
                    "rate": {"type": "number"},
                    "base": {"type": "number"},
                    "amount": {"type": "number"},
                },
                "required": ["concept", "rate", "base", "amount"],
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
    "required": ["supplier", "invoice", "lines", "taxes", "withholdings", "subtotal", "tax_total", "withholding_total", "total", "currency"],
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
        ollama_timeout: float = 1800.0,
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

        # Optional OCR provider (PaddleOCR-VL)
        self.ocr_provider: AIProvider | None = None
        if ai_config.ollama_ocr_model:
            self.ocr_provider = create_ai_provider(
                provider_type="ollama",
                endpoint=ai_config.ollama_endpoint,
                model=ai_config.ollama_ocr_model,
                vision_model=ai_config.ollama_ocr_model,
                timeout=ollama_timeout,
            )
            logger.info("ocr_provider_enabled", model=ai_config.ollama_ocr_model)
        else:
            logger.info("ocr_provider_disabled", reason="ollama_ocr_model not configured")

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

                # If no native text layer, try PaddleOCR first, then fallback to vision
                if not has_native_text and page_images:
                    logger.info("pdf_no_native_text_trying_paddleocr", pages=len(page_images))
                    ocr_text = await self._ocr_via_paddleocr(page_images)
                    
                    if ocr_text and len(ocr_text.strip()) >= 50:
                        # OCR succeeded - use text-based path
                        raw_text = ocr_text
                        has_native_text = True
                        native_text_chars = len(ocr_text)
                        logger.info("paddleocr_sufficient_text_using_text_path", chars=len(ocr_text))
                    else:
                        # OCR insufficient - fallback to vision
                        logger.info("paddleocr_insufficient_text_falling_back_to_vision", chars=len(ocr_text) if ocr_text else 0)
                        extracted_data = await self._extract_structured_data_via_vision(page_images)
                        inference_count += 1
                else:
                    # Has native text - use text-based extraction
                    if not raw_text or len(raw_text.strip()) < 20:
                        return ExtractionResult(
                            success=False,
                            error="No se pudo extraer texto del documento",
                            error_code="TEXT_EXTRACTION_FAILED",
                            requires_review=True,
                        )
            else:
                # Image - try PaddleOCR first, then fallback to vision
                page_count = 1
                # Read image bytes directly
                with open(temp_file_path, "rb") as f:
                    image_bytes = f.read()
                page_images = [image_bytes]
                
                logger.info("image_trying_paddleocr")
                ocr_text = await self._ocr_via_paddleocr(page_images)
                
                if ocr_text and len(ocr_text.strip()) >= 50:
                    # OCR succeeded - use text-based path
                    raw_text = ocr_text
                    has_native_text = True
                    native_text_chars = len(ocr_text)
                    logger.info("paddleocr_sufficient_text_using_text_path", chars=len(ocr_text))
                else:
                    # OCR insufficient - fallback to vision
                    logger.info("paddleocr_insufficient_text_falling_back_to_vision", chars=len(ocr_text) if ocr_text else 0)
                    extracted_data = await self._extract_structured_data_via_vision(page_images)
                    inference_count += 1
                    native_text_chars = 0
                    has_native_text = False
                    raw_text = ""

            # Document classification (skip if we already have extracted_data from vision)
            if 'extracted_data' not in locals():
                # Text-based path - need classification first
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

                # Structured extraction (text-based)
                extracted_data = await self._extract_structured_data(raw_text)
                inference_count += 1
            else:
                # Vision-based path - classify using the extracted data or first page image
                classification = await self._classify_document_from_vision(page_images, page_count, extracted_data)

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
                confidence=str(classification.confidence),
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
            doc = pymupdf.open(file_path)
            page_count = len(doc)

            # Limit pages
            if page_count > self.max_pages:
                page_count = self.max_pages
                doc = pymupdf.open(file_path)  # Reopen for limited processing

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
                    mat = pymupdf.Matrix(self.ocr_dpi / 72.0, self.ocr_dpi / 72.0)
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
                think=False,
                image_path=image_path,
                prompt="Extrae TODO el texto de esta imagen de factura. Devuelve solo el texto crudo, sin comentarios.",
                request_timeout=self.ollama_timeout,
            )
            return str(result.get("text", "")).strip()
        except Exception as e:
            logger.error("ocr_vision_failed", error=str(e))
            raise ExtractionTimeoutError("OCR timeout") from e

    async def _ocr_via_vision_bytes(self, image_bytes: bytes) -> str:
        """OCR via Ollama vision model from image bytes (in-memory)."""
        import tempfile
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(image_bytes)
                temp_path = tmp.name
            return await self._ocr_via_vision(temp_path)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass

    async def _ocr_via_paddleocr(self, page_images: list[bytes]) -> str:
        """
        OCR via PaddleOCR-VL 0.9B model.
        
        Processes each page/image individually with the OCR model,
        concatenates results with page separators.
        Returns clean combined text or empty string on failure.
        """
        if not self.ocr_provider:
            logger.debug("paddleocr_not_configured")
            return ""
        
        if not page_images:
            logger.warning("paddleocr_no_images")
            return ""
        
        ocr_texts = []
        for i, img_bytes in enumerate(page_images):
            try:
                import tempfile
                temp_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp.write(img_bytes)
                        temp_path = tmp.name
                    
                    result = await self.ocr_provider.vision(
                        think=False,
                        image_path=temp_path,
                        prompt="Extract ALL text from this invoice image. Return only the raw text content.",
                        request_timeout=self.ollama_timeout,
                    )
                    page_text = str(result.get("text", "")).strip()
                    if page_text:
                        ocr_texts.append(f"\n\n--- PÁGINA {i+1} ---\n\n{page_text}")
                    else:
                        ocr_texts.append(f"\n\n--- PÁGINA {i+1} ---\n\n[Sin texto detectado]")
                        
                finally:
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.unlink(temp_path)
                        except Exception:
                            pass
                            
            except Exception as e:
                logger.error("paddleocr_page_failed", page=i+1, error=str(e))
                ocr_texts.append(f"\n\n--- PÁGINA {i+1} ---\n\n[Error OCR: {str(e)}]")
        
        combined = "".join(ocr_texts).strip()
        logger.info("paddleocr_completed", pages=len(page_images), chars=len(combined))
        return combined

    async def _extract_structured_data_via_vision(self, page_images: list[bytes]) -> dict[str, Any]:
        """
        Extract structured invoice data directly from page images using vision model with JSON schema.

        This bypasses OCR + text extraction and uses the vision model's ability to
        understand document layout and extract structured data directly.

        Supports multi-page PDFs: all pages are sent to the vision model in a single request,
        and the model is instructed to extract from all pages, combine lines, and avoid duplicates.
        """
        if not page_images:
            raise ValueError("No page images provided for vision extraction")

        import base64
        import tempfile
        import os

        # Convert all page images to base64 for Ollama multi-image request
        # Ollama supports multiple images in one request via the "images" array
        base64_images = [
            base64.b64encode(img_bytes).decode() for img_bytes in page_images
        ]

        # Write the first page to a temp file so OllamaProvider.vision()
        # has a valid image_path (it requires image_path: str, not None).
        # All images are passed in the "images" kwarg, which overwrites
        # the single-image "images": [b64] in the payload via **kwargs spread.
        first_temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp.write(page_images[0])
                first_temp_path = tmp.name

            # Build prompt that instructs model to extract from ALL pages, combine lines, avoid duplicates
            # Primary data (supplier, invoice number, date, totals) come from first page
            prompt = self._build_multi_page_vision_prompt(len(page_images))

            # Send all images in a single Ollama vision request.
            # The "images" kwarg contains all base64-encoded pages;
            # **kwargs spread in OllamaProvider.vision() overwrites
            # the "images": [b64] computed from the temp file.
            result = await self.provider.vision(
                think=False,
                image_path=first_temp_path,
                prompt=prompt,
                request_timeout=self.ollama_timeout,
                format=json.dumps(INVOICE_JSON_SCHEMA),
                images=base64_images,
            )

            json_str = result.get("text", "").strip()
            data = json.loads(json_str)
            return data

        except json.JSONDecodeError as e:
            logger.error("vision_structured_extraction_invalid_json", error=str(e))
            raise
        except Exception as e:
            logger.error("vision_structured_extraction_failed", error=str(e))
            raise
        finally:
            if first_temp_path and os.path.exists(first_temp_path):
                try:
                    os.unlink(first_temp_path)
                except Exception:
                    pass

    def _build_multi_page_vision_prompt(self, page_count: int) -> str:
        """
        Build prompt for multi-page vision extraction.

        Instructs the model to:
        - Extract from ALL pages
        - Combine lines from all pages, avoiding duplicates
        - Primary data (supplier, invoice number, date, totals) from first page
        - Output only valid JSON according to INVOICE_JSON_SCHEMA
        """
        base_prompt = """
Extrae la información de la factura de proveedor de todas las páginas y devuelve JSON según el esquema.

PÁGINAS: Este documento tiene {page_count} páginas. Extrae información de TODAS las páginas.

PROCESAMIENTO:
- Proveedor, número de factura, fecha, totales: PROVENEN de la PÁGINA 1 (cabeza de factura).
- Líneas de detalle: EXTRAE de TODAS las páginas, COMPBINA todas las líneas y ELIMINA duplicados (mismas descripción+cantidad+precio_unitario en diferentes páginas cuentan una sola vez).
- Impuestos: DEVUELVE array "taxes" con TODOS los tipos de IVA (21%, 10%, 4%, 0%) incluyendo base, rate, amount.
- Retenciones: Si hay retenciones (IRPF, etc.), extrae CADA UNA en "withholdings" con: concept (IRPF/IVA soportado...), rate, base, amount (POSITIVO).
- NO omitas "withholdings" ni "taxes" si existen en el documento.
- Si NO hay retenciones, devuelve "withholdings": []. "withholding_total" es la suma de amounts.

FORMATEO:
- Fechas: DEVUELVE SIEMPRE en formato ISO YYYY-MM-DD (ej: 2026-08-27). NOuses DD/MM/YYYY ni concatenes día+mes.
- Líneas: EXTRAE TODAS las líneas de detalle visibles, incluidas líneas con IVA 0%, ajustes negativos.
- Cada línea requiere: description, quantity, unit_price. Optional: discount_percent, product_ref, vat_rate.

Devuelve SOLO el JSON válido, sin texto adicional, ajustado al esquema INVOICE_JSON_SCHEMA.
""".format(page_count=page_count)

        # Add page-specific instructions
        if page_count == 1:
            base_prompt += """
IMPORTANTE: Esta es una sola página. Extrae todas las líneas y datos de esta página única.
"""
        else:
            base_prompt += f"""
IMPORTANTE: Este documento tiene {page_count} páginas. Extrae líneas de TODAS las páginas y combina sin duplicados.

Para combinar líneas entre páginas: si una línea aparece en múltiples páginas (misma descripción, cantidad y precio unitario), inclúyela solo una vez en el resultado final. Prioriza los datos de la página 1 para campos superpuestos (precios, cantidades, descripciones).
"""

        base_prompt += """
Estructura esperada (JSON solo, sin texto adicional):
"""

        # Include schema reference
        base_prompt += json.dumps(INVOICE_JSON_SCHEMA, indent=2)

        return base_prompt



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
                think=False,
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
                request_timeout=self.ollama_timeout,
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
        """Main classification entry point for text-based extraction."""
        if has_native_text and raw_text and len(raw_text.strip()) >= 50:
            doc_type, confidence, signals = self._classify_heuristic(raw_text, page_count)
        else:
            doc_type, confidence, signals = await self._classify_llm(page_images, page_count)

        return ClassificationResult(
            document_type=doc_type,
            confidence=confidence,
            signals=signals,
            page_count=page_count,
            classification_strategy="heuristic" if has_native_text and raw_text and len(raw_text.strip()) >= 50 else "llm",
        )

    async def _classify_document_from_vision(
        self,
        page_images: list[bytes],
        page_count: int,
        extracted_data: dict[str, Any],
    ) -> DocumentClassification:
        """Classification for vision-based extraction using extracted data as signals."""
        # Use extracted data to determine document type
        supplier = extracted_data.get("supplier", {})
        invoice = extracted_data.get("invoice", {})
        lines = extracted_data.get("lines", [])

        signals = []
        if supplier.get("name"):
            signals.append("supplier_name")
        if supplier.get("tax_id"):
            signals.append("supplier_tax_id")
        if invoice.get("number"):
            signals.append("invoice_number")
        if invoice.get("date"):
            signals.append("invoice_date")
        if lines:
            signals.append("invoice_lines")

        # Check for multiple invoices in extracted data
        if isinstance(lines, list) and len(lines) > 0:
            # If we have structured data with supplier, invoice number, and lines, it's likely a single invoice
            if supplier.get("tax_id") and invoice.get("number"):
                return ClassificationResult(
                    document_type=DocumentClassification.SINGLE_INVOICE,
                    confidence=Decimal("0.9"),
                    signals=signals,
                    page_count=page_count,
                    classification_strategy="vision_structured",
                )

        # Fallback to LLM classification on first page image
        if page_images:
            doc_type, confidence, llm_signals = await self._classify_llm(page_images, page_count)
            return ClassificationResult(
                document_type=doc_type,
                confidence=confidence,
                signals=signals + llm_signals,
                page_count=page_count,
                classification_strategy="vision_structured_llm",
            )

        return ClassificationResult(
            document_type=DocumentClassification.UNKNOWN,
            confidence=Decimal("0"),
            signals=signals,
            page_count=page_count,
            classification_strategy="vision_structured",
        )

    # =========================================================================
    # STRUCTURED EXTRACTION
    # =========================================================================

    async def _extract_structured_data(self, raw_text: str) -> dict[str, Any]:
        """Extract structured data via Ollama with native JSON Schema."""
        # Normalize scientific notation in percentages BEFORE sending to LLM
        normalized_text = normalize_pdf_text(raw_text)

        prompt = f"""
Extrae la información de la factura de proveedor del siguiente texto y devuelve JSON según el esquema.

REGLAS IMPORTANTES:
- Fechas: DEVUELVE SIEMPRE en formato ISO YYYY-MM-DD (ej: 2026-08-27). NO uses DD/MM/YYYY ni concatenes día+mes.
- Líneas: EXTRAE TODAS las líneas de detalle, incluidas líneas con IVA 0%, ajustes negativos y líneas de continuación en páginas posteriores.
- Impuestos: DEVUELVE array "taxes" con TODOS los tipos de IVA (21%, 10%, 4%, 0%) incluyendo base, rate, amount.
- Retenciones: Si hay retenciones (IRPF, etc.), extrae CADA UNA en "withholdings" con: concept (IRPF/IVA soportado...), rate, base, amount (POSITIVO).
- NO omitas "withholdings" ni "taxes" si existen en el documento.
- Si NO hay retenciones, devuelve "withholdings": []. "withholding_total" es la suma de amounts.

Texto:
\"\"\"{normalized_text}\"\"\"
Devuelve SOLO el JSON válido, sin texto adicional.
"""

        try:
            result = await self.provider.generate(
                prompt=prompt,
                temperature=0.1,
                num_predict=4096,
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

    def _ensure_dict(self, value: Any, default: dict | None = None) -> dict[str, Any]:
        """Ensure value is a dict, return default if not."""
        if isinstance(value, dict):
            return value
        if default is not None:
            return default
        return {}

    def _ensure_list(self, value: Any, default: list | None = None) -> list[Any]:
        """Ensure value is a list, return default if not."""
        if isinstance(value, list):
            return value
        if default is not None:
            return default
        return []

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

        # Supplier - defensive: handle supplier as dict, list, or missing
        # Also support flat structure: supplier_name, supplier_tax_id, etc.
        supplier_raw = extracted_data.get("supplier")
        if isinstance(supplier_raw, list):
            supplier_data = self._ensure_dict(supplier_raw[0]) if supplier_raw else {}
        else:
            supplier_data = self._ensure_dict(supplier_raw)

        # Fallback to flat structure fields if nested structure is empty
        if not supplier_data.get("name"):
            supplier_data["name"] = extracted_data.get("supplier_name", "")
        if not supplier_data.get("tax_id"):
            supplier_data["tax_id"] = extracted_data.get("supplier_tax_id", "")

        supplier = SupplierInfo(
            name=supplier_data.get("name", ""),
            tax_id=normalize_tax_id(supplier_data.get("tax_id", "")),
            address=supplier_data.get("address") or extracted_data.get("supplier_address"),
            email=supplier_data.get("email") or extracted_data.get("supplier_email"),
            phone=supplier_data.get("phone") or extracted_data.get("supplier_phone"),
        )

        # Invoice header - defensive: handle invoice as dict, list, or missing
        # Also support flat structure: invoice_number, invoice_date, etc.
        invoice_raw = extracted_data.get("invoice")
        if isinstance(invoice_raw, list):
            invoice_data = self._ensure_dict(invoice_raw[0]) if invoice_raw else {}
        else:
            invoice_data = self._ensure_dict(invoice_raw)

        # Fallback to flat structure
        if not invoice_data.get("number"):
            invoice_data["number"] = extracted_data.get("invoice_number")
        if not invoice_data.get("date"):
            invoice_data["date"] = extracted_data.get("invoice_date")
        if not invoice_data.get("due_date"):
            invoice_data["due_date"] = extracted_data.get("due_date") or extracted_data.get("invoice_due_date")

        invoice_number = invoice_data.get("number")
        invoice_date = self._parse_date(invoice_data.get("date"))
        due_date = self._parse_date(invoice_data.get("due_date"))

        # Lines - defensive: handle lines as list, dict (single object), or missing
        lines = []
        lines_data = self._ensure_list(extracted_data.get("lines"))
        # If lines is a single dict instead of list, wrap it
        if isinstance(extracted_data.get("lines"), dict):
            lines_data = [extracted_data.get("lines")]

        for line_data in lines_data:
            line_data = self._ensure_dict(line_data)
            # Handle vat_rate that might be string with % or null
            vat_rate_raw = line_data.get("vat_rate")
            if vat_rate_raw is None:
                vat_rate = Decimal("21")
            else:
                vat_rate_str = str(vat_rate_raw).replace("%", "").strip()
                try:
                    vat_rate = Decimal(vat_rate_str)
                except Exception:
                    vat_rate = Decimal("21")

            line = InvoiceLine(
                description=line_data.get("description", ""),
                quantity=Decimal(str(line_data.get("quantity", 1))),
                unit_price=Decimal(str(line_data.get("unit_price", 0))),
                vat_rate=vat_rate,
                discount_percent=Decimal(str(line_data.get("discount_percent", 0))),
                product_ref=line_data.get("product_ref"),
            )
            lines.append(line)

        # Tax breakdown - defensive: handle taxes as list, dict, or missing
        tax_breakdown = []
        taxes_data = self._ensure_list(extracted_data.get("taxes"))
        if isinstance(extracted_data.get("taxes"), dict):
            taxes_data = [extracted_data.get("taxes")]

        for tax_data in taxes_data:
            tax_data = self._ensure_dict(tax_data)
            tax_breakdown.append(TaxBreakdownItem(
                rate=Decimal(str(tax_data.get("rate", 0))),
                base=Decimal(str(tax_data.get("base", 0))),
                amount=Decimal(str(tax_data.get("amount", 0))),
                source=InvoiceFieldSource.KNOWN,
            ))

        # Withholding breakdown - defensive with support for alternative field names
        # Model might use: withholdings, retentions, irpf, retencion, retenciones
        withholding_breakdown = []
        wh_data_list = self._ensure_list(extracted_data.get("withholdings"))
        if not wh_data_list:
            wh_data_list = self._ensure_list(extracted_data.get("retentions"))
        if not wh_data_list:
            wh_data_list = self._ensure_list(extracted_data.get("irpf"))
        if not wh_data_list:
            wh_data_list = self._ensure_list(extracted_data.get("retencion"))
        if not wh_data_list:
            wh_data_list = self._ensure_list(extracted_data.get("retenciones"))
        if isinstance(extracted_data.get("withholdings"), dict):
            wh_data_list = [extracted_data.get("withholdings")]
        elif isinstance(extracted_data.get("retentions"), dict):
            wh_data_list = [extracted_data.get("retentions")]
        elif isinstance(extracted_data.get("irpf"), dict):
            wh_data_list = [extracted_data.get("irpf")]
        elif isinstance(extracted_data.get("retencion"), dict):
            wh_data_list = [extracted_data.get("retencion")]
        elif isinstance(extracted_data.get("retenciones"), dict):
            wh_data_list = [extracted_data.get("retenciones")]

        for wh_data in wh_data_list:
            wh_data = self._ensure_dict(wh_data)
            withholding_breakdown.append(WithholdingBreakdownItem(
                concept=wh_data.get("concept") or wh_data.get("type") or "IRPF",
                rate=Decimal(str(wh_data.get("rate", 0))),
                base=Decimal(str(wh_data.get("base", 0))),
                amount=Decimal(str(wh_data.get("amount", 0))),
                source=InvoiceFieldSource.KNOWN,
            ))

        # Totals
        subtotal = Decimal(str(extracted_data.get("subtotal", 0)))
        tax_total = Decimal(str(extracted_data.get("tax_total", 0)))
        # Support alternative field names for withholding_total
        withholding_total_raw = (
            extracted_data.get("withholding_total") or
            extracted_data.get("withholdings_total") or
            extracted_data.get("retentions_total") or
            extracted_data.get("irpf_total") or
            extracted_data.get("retencion_total") or
            extracted_data.get("retenciones_total")
        )
        withholding_total = Decimal(str(withholding_total_raw)) if withholding_total_raw is not None else Decimal("0")
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
            classification_confidence=classification.confidence,
            classification_signals=classification.signals,
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
        """Parse date string to date object with plausibility validation."""
        if not date_str:
            return None
        try:
            # Try ISO format first
            from datetime import date
            parsed = date.fromisoformat(date_str)
            # Validate plausibility: year between 2000 and 2100
            if parsed.year < 2000 or parsed.year > 2100:
                logger.warning("implausible_date_year", raw=date_str, year=parsed.year)
                return None
            return parsed
        except Exception:
            # Try common formats
            for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"):
                try:
                    from datetime import datetime
                    parsed = datetime.strptime(date_str, fmt).date()
                    if parsed.year < 2000 or parsed.year > 2100:
                        logger.warning("implausible_date_year", raw=date_str, year=parsed.year)
                        return None
                    return parsed
                except Exception:
                    continue
        return None

    async def _check_model_ready(self) -> bool:
        """Check if Ollama model is ready."""
        try:
            # Simple health check - try to generate a minimal response
            # think=False to avoid thinking model overhead
            result = await self.provider.generate(
                prompt="OK",
                num_predict=1,
                think=False,
                request_timeout=self.ollama_timeout,
            )
            return True
        except Exception:
            return False

    async def aclose(self) -> None:
        """Close provider connections."""
        await self.provider.aclose()
