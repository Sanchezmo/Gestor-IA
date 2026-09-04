"""
Supplier Invoice Domain Package.

Exports:
- models: SupplierInvoiceDraft, InvoiceLine, TaxBreakdownItem, etc.
- extractor: InvoiceExtractor (LOCAL_ONLY Ollama)
- validator: validate_invoice, infer_missing_totals
- supplier_resolver: SupplierResolver
- ingestion: DocumentIngestionService
- correction: CorrectionParser, CorrectionApplicator (Corregir flow)
"""

from .models import (
    SupplierInvoiceDraft,
    SupplierInfo,
    InvoiceLine,
    TaxBreakdownItem,
    WithholdingBreakdownItem,
    DocumentClassification,
    SupplierResolutionStatus,
    ValidationStatus,
    InvoiceFieldSource,
    ExtractionResult,
    ValidationResult,
    SupplierResolutionResult,
    format_money,
    format_date,
    normalize_tax_id,
)

from .extractor import InvoiceExtractor, LocalModelUnavailableError, ExtractionTimeoutError
from .validator import validate_invoice, infer_missing_totals, normalize_tax_data
from .supplier_resolver import SupplierResolver
from .ingestion import DocumentIngestionService, IngestionResult, create_document_ingestion_service
from .correction_parser import CorrectionParser, CorrectionResult, create_correction_parser
from .correction_applicator import CorrectionApplicator, ApplicationResult, create_correction_applicator

__all__ = [
    # Models
    "SupplierInvoiceDraft",
    "SupplierInfo",
    "InvoiceLine",
    "TaxBreakdownItem",
    "WithholdingBreakdownItem",
    "DocumentClassification",
    "SupplierResolutionStatus",
    "ValidationStatus",
    "InvoiceFieldSource",
    "ExtractionResult",
    "ValidationResult",
    "SupplierResolutionResult",
    "format_money",
    "format_date",
    "normalize_tax_id",
    # Extractor
    "InvoiceExtractor",
    "LocalModelUnavailableError",
    "ExtractionTimeoutError",
    # Validator
    "validate_invoice",
    "infer_missing_totals",
    "normalize_tax_data",
    # Supplier Resolver
    "SupplierResolver",
    # Ingestion
    "DocumentIngestionService",
    "IngestionResult",
    "create_document_ingestion_service",
    # Correction (Corregir flow)
    "CorrectionParser",
    "CorrectionResult",
    "create_correction_parser",
    "CorrectionApplicator",
    "ApplicationResult",
    "create_correction_applicator",
]