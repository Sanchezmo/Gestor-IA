"""
Tests for V3 Invoice Handlers - Calculations and Validation.
"""

from __future__ import annotations

import pytest
from decimal import Decimal

from core.hermes.commands.handlers.invoice import CreateInvoiceHandler, CreateInvoiceFromProposalHandler
from core.hermes.commands.models import CreateInvoiceArgs, CreateInvoiceFromProposalArgs, InvoiceLineArgs


class TestCreateInvoiceCalculations:
    """Tests for deterministic invoice calculations."""

    def setup_method(self):
        self.handler = CreateInvoiceHandler()

    def test_line_calculation_basic(self):
        """Test basic line calculation without discount or retention."""
        line = {
            "cantidad": 10,
            "precio_unitario": 15.0,
            "iva_porcentaje": 21,
            "descuento_porcentaje": 0,
            "retencion_porcentaje": 0,
        }
        calc = self.handler._calculate_line(line)
        
        assert calc["base"] == Decimal("150.00")
        assert calc["iva"] == Decimal("31.50")
        assert calc["retention"] == Decimal("0.00")
        assert calc["total"] == Decimal("181.50")

    def test_line_calculation_with_discount(self):
        """Test line calculation with discount."""
        line = {
            "cantidad": 10,
            "precio_unitario": 100.0,
            "iva_porcentaje": 21,
            "descuento_porcentaje": 10,
            "retencion_porcentaje": 0,
        }
        calc = self.handler._calculate_line(line)
        
        assert calc["base"] == Decimal("900.00")  # 10 * 100 * 0.9
        assert calc["iva"] == Decimal("189.00")   # 900 * 0.21
        assert calc["total"] == Decimal("1089.00")

    def test_line_calculation_with_retention(self):
        """Test line calculation with line-level retention."""
        line = {
            "cantidad": 10,
            "precio_unitario": 50.0,
            "iva_porcentaje": 21,
            "descuento_porcentaje": 0,
            "retencion_porcentaje": 7,
        }
        calc = self.handler._calculate_line(line)
        
        assert calc["base"] == Decimal("500.00")
        assert calc["iva"] == Decimal("105.00")
        assert calc["retention"] == Decimal("35.00")  # 500 * 0.07
        assert calc["total"] == Decimal("570.00")

    def test_totals_without_header_retention(self):
        """Test totals calculation without header retention."""
        lines = [
            {"cantidad": 10, "precio_unitario": 15.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 0},
            {"cantidad": 20, "precio_unitario": 25.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 7},
        ]
        totals = self.handler._calculate_totals(lines, header_retention_rate=0.0)
        
        assert totals["total_base"] == Decimal("650.00")
        assert totals["total_iva"] == Decimal("136.50")
        assert totals["total_retention"] == Decimal("35.00")  # solo línea 2
        assert totals["total_ttc"] == Decimal("751.50")

    def test_totals_with_header_retention(self):
        """Test totals calculation with header retention (7%)."""
        lines = [
            {"cantidad": 10, "precio_unitario": 15.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 0},
            {"cantidad": 20, "precio_unitario": 25.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 0},
        ]
        totals = self.handler._calculate_totals(lines, header_retention_rate=7.0)
        
        assert totals["total_base"] == Decimal("650.00")
        assert totals["total_iva"] == Decimal("136.50")
        assert totals["total_retention"] == Decimal("45.50")  # 650 * 0.07
        assert totals["total_ttc"] == Decimal("741.00")

    def test_invoice_validation_basic(self):
        """Test invoice payload validation."""
        payload = {
            "cliente": "ACME",
            "lineas": [
                {"descripcion": "Pintura", "cantidad": 10, "precio_unitario": 15.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 0},
            ],
        }
        validated = CreateInvoiceHandler().validate_payload(payload)
        
        assert validated["cliente_query"] == "ACME"
        assert len(validated["lineas"]) == 1

    def test_invoice_validation_empty_client_raises(self):
        """Test validation raises for empty client."""
        payload = {
            "cliente": "",
            "lineas": [{"descripcion": "Pintura", "cantidad": 10, "precio_unitario": 15.0, "iva_porcentaje": 21}],
        }
        with pytest.raises(ValueError, match="Cliente es obligatorio"):
            CreateInvoiceHandler().validate_payload(payload)

    def test_invoice_validation_empty_lines_raises(self):
        """Test validation raises for empty lines."""
        payload = {"cliente": "ACME", "lineas": []}
        with pytest.raises(ValueError, match="Al menos una línea es obligatoria"):
            CreateInvoiceHandler().validate_payload(payload)

    def test_invoice_preview_generation(self):
        """Test preview generation with all details."""
        handler = CreateInvoiceHandler()
        validated = {
            "cliente_query": "ACME",
            "fecha": "2025-01-15",
            "fecha_vencimiento": "2025-02-14",
            "serie": "FAC-2025",
            "forma_pago": "transferencia",
            "retencion_porcentaje": 7,
            "lineas": [
                {"descripcion": "Pintura", "cantidad": 10, "precio_unitario": 15.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 0},
                {"descripcion": "Mano de obra", "cantidad": 20, "precio_unitario": 25.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 7},
            ],
            "retencion_porcentaje": 7,
        }
        
        class MockContext:
            currency = "EUR"
        
        preview = handler.generate_preview(validated, MockContext())
        
        assert "ACME" in preview.summary
        assert "Pintura" in preview.summary
        assert "Mano de obra" in preview.summary
        assert "650.00" in preview.summary  # base imponible
        assert "136.50" in preview.summary  # IVA
        assert "Retención" in preview.summary
        assert "706.00" in preview.summary  # TOTAL (base 650 + IVA 136.50 - retención 80.50 = 706.00)

    def test_invoice_from_proposal_validation(self):
        """Test invoice from proposal validation."""
        payload = {"proposal_id": 42}
        validated = CreateInvoiceFromProposalHandler().validate_payload(payload)
        assert validated["proposal_id"] == 42

    def test_invoice_from_proposal_missing_id_raises(self):
        """Test validation raises for missing proposal_id."""
        payload = {}
        with pytest.raises(ValueError, match="proposal_id es obligatorio"):
            CreateInvoiceFromProposalHandler().validate_payload(payload)

    def test_decimal_precision_edge_cases(self):
        """Test edge cases for decimal precision."""
        # Test rounding half up
        line = {
            "cantidad": 3,
            "precio_unitario": 10.0/3,  # 3.3333...
            "iva_porcentaje": 21,
            "descuento_porcentaje": 0,
            "retencion_porcentaje": 0,
        }
        calc = CreateInvoiceHandler()._calculate_line(line)
        
        # base = 3 * 3.3333... = 10.0 (exact)
        # iva = 10.0 * 0.21 = 2.1
        assert calc["base"] == Decimal("10.00")
        assert calc["iva"] == Decimal("2.10")


class TestSupplierInvoiceCalculations:
    """Tests for Supplier Invoice Handler calculations."""

    def setup_method(self):
        from core.hermes.commands.handlers.supplier_invoice import CreateSupplierInvoiceHandler
        self.handler = CreateSupplierInvoiceHandler()

    def test_supplier_line_calculation_no_retention(self):
        """Supplier invoices don't have retention."""
        line = {
            "cantidad": 50,
            "precio_unitario": 10.0,
            "iva_porcentaje": 21,
            "descuento_porcentaje": 5,
        }
        calc = self.handler._calculate_line(line)
        
        assert calc["base"] == Decimal("475.00")  # 50 * 10 * 0.95
        assert calc["iva"] == Decimal("99.75")    # 475 * 0.21
        assert "retention" not in calc
        assert calc["total"] == Decimal("574.75")

    def test_supplier_totals(self):
        lines = [
            {"cantidad": 10, "precio_unitario": 100.0, "iva_porcentaje": 21, "descuento_porcentaje": 0},
            {"cantidad": 5, "precio_unitario": 50.0, "iva_porcentaje": 10, "descuento_porcentaje": 0},
        ]
        totals = self.handler._calculate_totals(lines)
        
        assert totals["total_base"] == Decimal("1250.00")
        assert totals["total_iva"] == Decimal("235.00")
        assert totals["total_ttc"] == Decimal("1485.00")


class TestPaymentAllocation:
    """Tests for payment FIFO allocation."""

    def test_fifo_allocation_exact(self):
        """Test FIFO allocation exact match."""
        from core.hermes.commands.handlers.payment import allocate_payment_fifo, PaymentAllocation
        
        amount = Decimal("500.00")
        pending = [
            {"id": 1, "ref": "FAC-001", "date": "2025-01-15", "remaining_amount": 300.00},
            {"id": 2, "ref": "FAC-002", "date": "2025-01-20", "remaining_amount": 200.00},
            {"id": 3, "ref": "FAC-003", "date": "2025-02-01", "remaining_amount": 100.00},
        ]
        
        allocations = allocate_payment_fifo(amount, pending)
        
        assert len(allocations) == 2
        assert allocations[0].invoice_id == 1
        assert allocations[0].amount == Decimal("300.00")
        assert allocations[0].invoice_remaining == Decimal("0.00")
        assert allocations[1].invoice_id == 2
        assert allocations[1].amount == Decimal("200.00")

    def test_fifo_allocation_partial(self):
        """Test FIFO allocation with partial payment."""
        from core.hermes.commands.handlers.payment import allocate_payment_fifo
        
        amount = Decimal("250.00")
        pending = [
            {"id": 1, "remaining_amount": 300.00},
            {"id": 2, "remaining_amount": 200.00},
        ]
        
        allocations = allocate_payment_fifo(amount, pending)
        
        assert len(allocations) == 1
        assert allocations[0].invoice_id == 1
        assert allocations[0].amount == Decimal("250.00")
        assert allocations[0].invoice_remaining == Decimal("50.00")

    def test_fifo_allocation_empty_pending(self):
        """Test FIFO with no pending invoices."""
        from core.hermes.commands.handlers.payment import allocate_payment_fifo
        
        amount = Decimal("100.00")
        pending = []
        
        allocations = allocate_payment_fifo(amount, pending)
        assert allocations == []


class TestStockCalculations:
    """Tests for stock movement calculations."""

    def test_stock_valuation(self):
        from core.hermes.commands.models import calculate_stock_valuation, StockLineArgs
        
        lines = [
            StockLineArgs(producto_ref="REF-001", cantidad=100, precio_unitario=15.0),
            StockLineArgs(producto_ref="REF-002", cantidad=50, precio_unitario=25.0),
        ]
        
        result = calculate_stock_valuation("entrada", lines, "weighted_average")
        
        assert result["total_qty"] == Decimal("150")
        assert result["total_value"] == Decimal("2750.00")
        assert result["average_price"] == Decimal("18.33")


class TestInvoiceFromProposalHandler:
    """Tests for Invoice from Proposal handler."""

    def test_proposal_payload_validation(self):
        payload = {
            "proposal_id": 123,
            "fecha": "2025-01-15",
            "fecha_vencimiento": "2025-02-14",
            "serie": "FAC-2025",
            "forma_pago": "transferencia",
        }
        
        validated = CreateInvoiceFromProposalHandler().validate_payload(payload)
        
        assert validated["proposal_id"] == 123
        assert validated["serie"] == "FAC-2025"
        assert validated["forma_pago"] == "transferencia"

    def test_proposal_preview_generation(self):
        handler = CreateInvoiceFromProposalHandler()
        validated = {"proposal_id": 42}
        
        class MockContext:
            currency = "EUR"
        
        preview = handler.generate_preview(validated, MockContext())
        
        assert "presupuesto 42" in preview.summary.lower()
        assert "líneas del presupuesto" in preview.summary.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])