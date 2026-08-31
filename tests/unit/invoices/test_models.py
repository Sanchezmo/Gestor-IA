"""
Unit tests for Supplier Invoice Domain Models.
"""

import pytest
from decimal import Decimal
from datetime import date

from core.hermes.invoices.models import (
    SupplierInvoiceDraft,
    SupplierInfo,
    InvoiceLine,
    TaxBreakdownItem,
    WithholdingBreakdownItem,
    DocumentClassification,
    SupplierResolutionStatus,
    ValidationStatus,
    InvoiceFieldSource,
    format_money,
    format_date,
    normalize_tax_id,
    parse_decimal,
)


class TestSupplierInvoiceModels:
    """Tests for supplier invoice domain models."""

    def test_invoice_line_computed_fields(self):
        """Test that InvoiceLine computes derived fields automatically."""
        line = InvoiceLine(
            description="Test Product",
            quantity=Decimal("2"),
            unit_price=Decimal("10.00"),
            vat_rate=Decimal("21"),
            discount_percent=Decimal("10"),
        )

        # line_total_excl_tax = 2 * 10 * (1 - 0.10) = 18.00
        assert line.line_total_excl_tax == Decimal("18.00")
        # vat_amount = 18.00 * 21% = 3.78
        assert line.vat_amount == Decimal("3.78")
        # line_total_incl_tax = 18.00 + 3.78 = 21.78
        assert line.line_total_incl_tax == Decimal("21.78")

    def test_invoice_line_with_explicit_values(self):
        """Test InvoiceLine respects explicitly provided computed values."""
        line = InvoiceLine(
            description="Test",
            quantity=Decimal("1"),
            unit_price=Decimal("100"),
            vat_rate=Decimal("21"),
            line_total_excl_tax=Decimal("90.00"),  # Explicit
            vat_amount=Decimal("18.90"),  # Explicit
            line_total_incl_tax=Decimal("108.90"),  # Explicit
        )

        assert line.line_total_excl_tax == Decimal("90.00")
        assert line.vat_amount == Decimal("18.90")
        assert line.line_total_incl_tax == Decimal("108.90")

    def test_supplier_invoice_draft_helpers(self):
        """Test SupplierInvoiceDraft helper methods."""
        supplier = SupplierInfo(name="Test Supplier", tax_id="B12345678")
        draft = SupplierInvoiceDraft(
            document_hash="abc123",
            document_filename="test.pdf",
            document_mime_type="application/pdf",
            document_size_bytes=1024,
            supplier=supplier,
            invoice_number="FAC-001",
            invoice_number_source=InvoiceFieldSource.KNOWN,
            invoice_date=date(2024, 1, 15),
            invoice_date_source=InvoiceFieldSource.KNOWN,
            subtotal=Decimal("100.00"),
            subtotal_source=InvoiceFieldSource.KNOWN,
            tax_total=Decimal("21.00"),
            tax_total_source=InvoiceFieldSource.INFERRED,
            total=Decimal("121.00"),
            total_source=InvoiceFieldSource.KNOWN,
        )

        assert draft.has_supplier() is True
        assert draft.has_complete_header() is True
        assert "tax_total" in draft.get_inferred_fields()
        assert "invoice_number" not in draft.get_unknown_fields()

    def test_get_supplier_display(self):
        """Test supplier display formatting."""
        supplier = SupplierInfo(name="Test SL", tax_id="B12345678")
        draft = SupplierInvoiceDraft(
            document_hash="abc123",
            document_filename="test.pdf",
            document_mime_type="application/pdf",
            document_size_bytes=1024,
            supplier=supplier,
        )
        assert draft.get_supplier_display() == "Test SL (B12345678)"

    def test_validation_status_display(self):
        """Test validation status display."""
        draft = SupplierInvoiceDraft(
            document_hash="abc123",
            document_filename="test.pdf",
            document_mime_type="application/pdf",
            document_size_bytes=1024,
            validation_status=ValidationStatus.VALID,
        )
        assert "✓" in draft.get_validation_display()

    def test_supplier_resolution_display(self):
        """Test supplier resolution display."""
        draft = SupplierInvoiceDraft(
            document_hash="abc123",
            document_filename="test.pdf",
            document_mime_type="application/pdf",
            document_size_bytes=1024,
            supplier_resolution_status=SupplierResolutionStatus.FOUND,
        )
        assert "✓" in draft.get_supplier_resolution_display()

    def test_format_money(self):
        """Test money formatting."""
        assert format_money(Decimal("1234.56")) == "1.234,56 EUR"
        assert format_money(Decimal("100")) == "100,00 EUR"
        assert format_money(None) == "—"

    def test_format_date(self):
        """Test date formatting."""
        assert format_date(date(2024, 1, 15)) == "15/01/2024"
        assert format_date(None) == "—"

    def test_normalize_tax_id(self):
        """Test tax ID normalization."""
        assert normalize_tax_id("B12345678") == "B12345678"
        assert normalize_tax_id(" b12345678 ") == "B12345678"
        assert normalize_tax_id("ES-B-12345678") == "ESB12345678"
        assert normalize_tax_id("") == ""

    def test_parse_decimal(self):
        """Test decimal parsing from various formats."""
        assert parse_decimal("1234.56") == Decimal("1234.56")
        assert parse_decimal("1,234.56") == Decimal("1234.56")
        assert parse_decimal("1.234,56") == Decimal("1234.56")
        assert parse_decimal(" 1 234,56 ") == Decimal("1234.56")
        assert parse_decimal(100) == Decimal("100")
        assert parse_decimal(Decimal("50.25")) == Decimal("50.25")
        assert parse_decimal(None) == Decimal("0")
        assert parse_decimal("invalid", Decimal("99")) == Decimal("99")


class TestTaxBreakdownItem:
    """Tests for TaxBreakdownItem."""

    def test_tax_breakdown_creation(self):
        tax = TaxBreakdownItem(
            rate=Decimal("21"),
            base=Decimal("1000"),
            amount=Decimal("210"),
            source=InvoiceFieldSource.KNOWN,
        )
        assert tax.rate == Decimal("21")
        assert tax.base == Decimal("1000")
        assert tax.amount == Decimal("210")


class TestPdfTextNormalization:
    """Tests for PDF text scientific notation normalization."""

    def test_normalize_scientific_percent_1e_plus_1(self):
        """Test 1E+1% -> 10%"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("IVA 1E+1%")
        assert "10%" in result

    def test_normalize_scientific_percent_2_1e_plus_1(self):
        """Test 2.1E+1% -> 21%"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("IVA 2.1E+1%")
        assert "21%" in result

    def test_normalize_scientific_percent_4e_plus_0(self):
        """Test 4E+0% -> 4%"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("IVA 4E+0%")
        assert "4%" in result

    def test_normalize_scientific_percent_1_5e_plus_1(self):
        """Test 1.5E+1% -> 15%"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("IVA 1.5E+1%")
        assert "15%" in result

    def test_normalize_scientific_percent_lowercase_e(self):
        """Test lowercase e notation: 1e+1% -> 10%"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("IVA 1e+1%")
        assert "10%" in result

    def test_normalize_scientific_percent_no_suffix_not_matched(self):
        """Test that 1E without % is NOT matched (avoid false positives)"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("Calle 1E ejemplo")
        assert "1E" in result  # Should NOT be modified

    def test_normalize_cif_not_matched(self):
        """Test CIF is NOT modified"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("CIF: B12345678")
        assert "B12345678" in result

    def test_normalize_invoice_number_not_matched(self):
        """Test invoice number is NOT modified"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("Factura: TH-2026-314")
        assert "TH-2026-314" in result

    def test_normalize_multiple_in_text(self):
        """Test multiple scientific notations in same text"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        text = "IVA 1E+1% y IVA 2.1E+1% y IVA 4E+0%"
        result = normalize_pdf_text(text)
        assert "10%" in result
        assert "21%" in result
        assert "4%" in result

    def test_normalize_preserves_normal_percent(self):
        """Test normal percentages are preserved"""
        from core.hermes.invoices.extractor import normalize_pdf_text
        result = normalize_pdf_text("IVA 10% y IVA 21%")
        assert "10%" in result
        assert "21%" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])