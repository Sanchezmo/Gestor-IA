"""
E2E Tests for Command Layer V3 - Full Telegram → NL → Preview → Confirm → Execute → Audit flow.

Architecture: Dolibarr is the SOLE authority for ERP permissions.
Hermes only manages Hermes-specific capabilities (ai.use, admin, etc.).
"""

from __future__ import annotations

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date
from decimal import Decimal
from uuid import uuid4

from core.hermes.commands.handlers.invoice import CreateInvoiceHandler
from core.hermes.commands.handlers.supplier_invoice import CreateSupplierInvoiceHandler
from core.hermes.commands.handlers.payment import CreatePaymentHandler, CreateCollectionHandler, allocate_payment_fifo, PaymentAllocation
from core.hermes.commands.handlers.stock_movement import CreateStockMovementHandler
from core.hermes.commands.handlers.project import CreateProjectHandler, AddProjectTaskHandler
from core.hermes.commands.models import (
    CommandType, CommandStatus, CommandPreview, PendingCommand, CommandResult
)
from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext, DolibarrUser, DolibarrGroup
from core.hermes.commands.executor import CommandExecutor
from core.hermes.commands.store import PendingCommandStore
from core.hermes.audit import AuditLogger
from core.hermes.authorization import AuthorizationService
from core.hermes.identity import GestorPermissions


class TestV3InvoiceE2E:
    """E2E tests for Invoice command flow."""

    @pytest.fixture
    def mock_company_context(self):
        """Create mock CompanyContext."""
        context = MagicMock(spec=CompanyContext)
        context.instance_id = "test_empresa"
        context.company_name = "Test Empresa"
        context.currency = "EUR"
        context.dolibarr_config = MagicMock()
        context.dolibarr_config.internal_url = "http://localhost:8080"
        context.dolibarr_config.api_key = "test_key"
        context.create_dolibarr_client = MagicMock()
        return context

    @pytest.fixture
    def mock_user_context(self):
        """Create mock UserContext with Hermes capabilities (NOT ERP permissions).
        
        Architecture: Dolibarr is the SOLE authority for ERP permissions.
        Hermes only manages Hermes-specific capabilities.
        """
        user = MagicMock(spec=UserContext)
        user.instance_id = "test_empresa"
        user.telegram_user_id = 12345
        user.dolibarr_user_id = 1
        # Hermes capabilities only (admin, ai.use, etc.) - NOT ERP permissions
        user.gestor_roles = frozenset([GestorPermissions.ADMIN, GestorPermissions.AI_USE])
        user.has_permission = MagicMock(side_effect=lambda p: p in user.gestor_roles)
        return user

    @pytest.fixture
    def mock_dolibarr_client(self):
        """Create mock DolibarrClient."""
        client = AsyncMock()
        client.find_thirdparty_by_tax_id = AsyncMock(return_value=None)
        client.search_thirdparties = AsyncMock(return_value=[])
        client.create_thirdparty = AsyncMock(return_value={"id": 99})
        client.create_invoice = AsyncMock(return_value={"id": 42, "ref": "FAC-2025-0001"})
        client.add_invoice_line = AsyncMock(return_value={"id": 1})
        client.validate_invoice = AsyncMock(return_value={})
        client.get_product_by_ref = AsyncMock(return_value=None)
        return client

    @pytest.fixture
    def mock_audit_logger(self):
        """Create mock AuditLogger."""
        audit = AsyncMock(spec=AuditLogger)
        audit.log_from_context = AsyncMock(return_value="audit-id-123")
        audit.query_logs = MagicMock(return_value=[])
        return audit

    @pytest.fixture
    def mock_command_store(self):
        """Create mock PendingCommandStore."""
        store = MagicMock(spec=PendingCommandStore)
        store.create = MagicMock()
        store.get = MagicMock(return_value=None)
        store.update_status = MagicMock(return_value=True)
        store.confirm = MagicMock(return_value=None)
        return store

    @pytest.fixture
    def mock_command_registry(self):
        """Create mock CommandRegistry."""
        registry = MagicMock()
        registry.get_handler = MagicMock()
        return registry

    @pytest.mark.asyncio
    async def test_invoice_preview_to_execute_flow(
        self,
        mock_company_context,
        mock_user_context,
        mock_dolibarr_client,
        mock_audit_logger,
        mock_command_store,
        mock_command_registry,
    ):
        """Test complete flow: preview → confirm → execute."""
        # Setup
        mock_company_context.create_dolibarr_client.return_value.__aenter__.return_value = mock_dolibarr_client
        mock_command_registry.get_handler.return_value = CreateInvoiceHandler()
        
        # Create executor
        executor = CommandExecutor(
            registry=mock_command_registry,
            store=mock_command_store,
            audit_logger=mock_audit_logger,
            company_context=mock_company_context,
            user_context=mock_user_context,
        )

        # 1. Preview
        from core.hermes.commands.models import CommandIntent
        intent = CommandIntent(
            command_type=CommandType.CREATE_INVOICE,
            payload={
                "cliente": "ACME",
                "lineas": [
                    {"descripcion": "Pintura", "cantidad": 10, "precio_unitario": 15.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 0},
                ],
            },
            instance_id="test_empresa",
            telegram_user_id=12345,
            dolibarr_user_id=1,
            request_id="req-123",
        )

        preview = await executor.preview(intent)
        
        assert isinstance(preview, CommandPreview)
        assert preview.command_type == CommandType.CREATE_INVOICE
        assert "ACME" in preview.summary
        assert preview.structured_data["cliente_query"] == "ACME"

        # 2. Verify pending command was stored
        mock_command_store.create.assert_called_once()
        created_pending = mock_command_store.create.call_args[0][0]
        assert isinstance(created_pending, PendingCommand)
        assert created_pending.status == CommandStatus.PENDING
        assert created_pending.command_type == CommandType.CREATE_INVOICE
        assert created_pending.telegram_user_id == 12345

        # 3. Confirm (execute)
        command_id = created_pending.command_id
        
        # Mock the store.confirm to return the pending command
        pending_confirmed = MagicMock(spec=PendingCommand)
        pending_confirmed.command_id = command_id
        pending_confirmed.instance_id = "test_empresa"
        pending_confirmed.telegram_user_id = 12345
        pending_confirmed.dolibarr_user_id = 1
        pending_confirmed.command_type = CommandType.CREATE_INVOICE
        pending_confirmed.validated_payload = {"cliente_query": "ACME", "lineas": [{"descripcion": "Pintura", "cantidad": 10, "precio_unitario": 15.0, "iva_porcentaje": 21, "descuento_porcentaje": 0, "retencion_porcentaje": 0}]}
        pending_confirmed.status = CommandStatus.CONFIRMED
        
        mock_command_store.confirm.return_value = pending_confirmed
        mock_command_store.update_status.return_value = True

        # Execute
        result = await executor.confirm(command_id, 12345)
        
        assert result.success is True
        assert result.resource_type == "invoice"
        assert result.resource_id == 42
        assert result.data["ref"] == "FAC-2025-0001"

        # Verify audit was called
        assert mock_audit_logger.log_from_context.call_count >= 2  # preview + confirm + execute

    @pytest.mark.asyncio
    async def test_invoice_duplicate_confirmation_idempotent(
        self,
        mock_company_context,
        mock_user_context,
        mock_dolibarr_client,
        mock_audit_logger,
        mock_command_store,
        mock_command_registry,
    ):
        """Test that confirming twice doesn't create duplicate invoices."""
        mock_company_context.create_dolibarr_client.return_value.__aenter__.return_value = mock_dolibarr_client
        mock_command_registry.get_handler.return_value = CreateInvoiceHandler()
        
        executor = CommandExecutor(
            registry=mock_command_registry,
            store=mock_command_store,
            audit_logger=mock_audit_logger,
            company_context=mock_company_context,
            user_context=mock_user_context,
        )

        # Create a pending command that's already EXECUTED
        executed_pending = MagicMock(spec=PendingCommand)
        executed_pending.command_id = uuid4()
        executed_pending.status = CommandStatus.EXECUTED
        executed_pending.result = {"resource_id": 42, "data": {"ref": "FAC-2025-0001"}}
        executed_pending.telegram_user_id = 12345
        executed_pending.instance_id = "test_empresa"
        
        mock_command_store.get.return_value = executed_pending

        result = await executor.confirm(executed_pending.command_id, 12345)
        
        assert result.success is True
        assert result.idempotent is True
        assert result.resource_id == 42
        # Dolibarr should NOT be called again
        mock_dolibarr_client.create_invoice.assert_not_called()


class TestV3SupplierInvoiceE2E:
    """E2E tests for Supplier Invoice command flow."""

    @pytest.mark.asyncio
    async def test_supplier_invoice_flow(self):
        """Test supplier invoice creation flow."""
        from core.hermes.commands.handlers.supplier_invoice import CreateSupplierInvoiceHandler
        
        handler = CreateSupplierInvoiceHandler()
        
        payload = {
            "proveedor": "Pinturas Norte SL",
            "lineas": [
                {"descripcion": "Pintura blanca", "cantidad": 50, "precio_unitario": 12.0, "iva_porcentaje": 21, "descuento_porcentaje": 0},
            ],
        }
        
        validated = handler.validate_payload(payload)
        assert validated["proveedor_query"] == "Pinturas Norte SL"
        assert len(validated["lineas"]) == 1

        class MockContext:
            currency = "EUR"
        
        preview = handler.generate_preview(validated, MagicMock())
        assert "Pinturas Norte SL" in preview.summary
        assert "Pintura blanca" in preview.summary
        assert "Base imponible" in preview.summary


class TestV3PaymentE2E:
    """E2E tests for Payment command flow."""

    @pytest.mark.asyncio
    async def test_payment_fifo_allocation_preview(self):
        """Test payment preview shows FIFO allocation info."""
        from core.hermes.commands.handlers.payment import CreatePaymentHandler
        
        handler = CreatePaymentHandler()
        
        validated = {
            "cliente_query": "ACME",
            "importe": 500.0,
            "fecha": "2025-01-15",
            "forma_pago": "transferencia",
            "auto_allocate": True,
        }
        
        class MockContext:
            currency = "EUR"
        
        preview = handler.generate_preview(validated, MagicMock())
        
        assert "ACME" in preview.summary
        assert "500.00€" in preview.summary
        assert "FIFO" in preview.summary


class TestV3StockMovementE2E:
    """E2E tests for Stock Movement command flow."""

    @pytest.mark.asyncio
    async def test_stock_entrada_preview(self):
        """Test stock entrada preview."""
        from core.hermes.commands.handlers.stock_movement import CreateStockMovementHandler
        
        handler = CreateStockMovementHandler()
        
        validated = {
            "tipo": "entrada",
            "almacen_origen": "Central",
            "lineas": [
                {"producto_ref": "PINT-001", "cantidad": 100, "precio_unitario": 15.0},
            ],
        }
        
        class MockContext:
            currency = "EUR"
        
        preview = handler.generate_preview(validated, MagicMock())
        
        assert "ENTRADA" in preview.summary
        assert "Central" in preview.summary
        assert "PINT-001" in preview.summary
        assert "100" in preview.summary

    @pytest.mark.asyncio
    async def test_stock_traslado_preview(self):
        """Test stock traslado preview."""
        from core.hermes.commands.handlers.stock_movement import CreateStockMovementHandler
        
        handler = CreateStockMovementHandler()
        
        validated = {
            "tipo": "traslado",
            "almacen_origen": "Central",
            "almacen_destino": "Obra 1",
            "lineas": [
                {"producto_ref": "PINT-001", "cantidad": 20},
            ],
        }
        
        class MockContext:
            currency = "EUR"
        
        preview = handler.generate_preview(validated, MagicMock())
        
        assert "TRASLADO" in preview.summary
        assert "Central" in preview.summary
        assert "Obra 1" in preview.summary


class TestV3ProjectE2E:
    """E2E tests for Project commands."""

    @pytest.mark.asyncio
    async def test_create_project_preview(self):
        """Test project creation preview."""
        from core.hermes.commands.handlers.project import CreateProjectHandler
        
        handler = CreateProjectHandler()
        
        validated = {
            "nombre": "Reforma Nave Industrial",
            "descripcion": "Reforma completa de nave",
            "cliente": "ACME",
            "fecha_inicio": "2025-02-01",
            "fecha_fin": "2025-06-30",
            "presupuesto": 50000.0,
            "estado": "planificacion",
        }
        
        class MockContext:
            currency = "EUR"
        
        preview = handler.generate_preview(validated, MagicMock())
        
        assert "Reforma Nave Industrial" in preview.summary
        assert "ACME" in preview.summary
        assert "50000.00" in preview.summary


class TestV3BC3E2E:
    """E2E tests for BC3 commands."""

    @pytest.mark.asyncio
    async def test_import_bc3_preview(self):
        """Test BC3 import preview."""
        from core.hermes.commands.handlers.bc3 import ImportBC3Handler
        
        handler = ImportBC3Handler()
        
        validated = {
            "nombre_proyecto": "Reforma Nave",
            "vincular_productos": True,
        }
        
        class MockContext:
            currency = "EUR"
        
        preview = handler.generate_preview(validated, MagicMock())
        
        assert "Reforma Nave" in preview.summary
        assert "Vincular productos" in preview.summary


class TestV3Isolation:
    """Cross-instance isolation tests for V3 commands."""

    @pytest.mark.asyncio
    async def test_invoice_cross_instance_rejected(self):
        """Test that invoice from Empresa A cannot be confirmed by Empresa B user."""
        from core.hermes.commands.executor import CommandExecutor
        from core.hermes.commands.models import PendingCommand, CommandStatus
        from uuid import uuid4
        
        mock_company_context = MagicMock()
        mock_company_context.instance_id = "empresa_a"
        
        mock_user_context = MagicMock()
        mock_user_context.instance_id = "empresa_b"  # Diferente instancia
        mock_user_context.telegram_user_id = 99999
        
        mock_store = MagicMock()
        mock_store.confirm.return_value = None  # Comando no existe
        mock_store.get.return_value = None  # Comando no existe
        
        executor = CommandExecutor(
            registry=MagicMock(),
            store=mock_store,
            audit_logger=AsyncMock(),
            company_context=mock_company_context,
            user_context=mock_user_context,
        )
        
        result = await executor.confirm(uuid4(), 99999)
        
        assert result.success is False
        assert result.error_code == "NOT_FOUND"


class TestV3Idempotency:
    """Idempotency tests for V3 commands."""

    @pytest.mark.asyncio
    async def test_duplicate_stock_confirmation(self):
        """Test duplicate stock movement confirmation is idempotent."""
        from core.hermes.commands.executor import CommandExecutor
        from core.hermes.commands.models import PendingCommand, CommandStatus
        from uuid import uuid4
        
        executed_pending = MagicMock(spec=PendingCommand)
        executed_pending.command_id = uuid4()
        executed_pending.status = CommandStatus.EXECUTED
        executed_pending.result = {"resource_id": 100}
        executed_pending.telegram_user_id = 12345
        executed_pending.instance_id = "test_empresa"
        executed_pending.validated_payload = {}
        
        mock_store = MagicMock()
        mock_store.confirm.return_value = executed_pending
        mock_store.get.return_value = executed_pending
        
        mock_registry = MagicMock()
        mock_handler = MagicMock()
        mock_handler.required_permission = ""
        mock_handler.execute = AsyncMock(return_value=MagicMock(success=True, resource_id=100, resource_type="test", data={}, idempotent=True))
        mock_registry.get_handler.return_value = mock_handler
        
        executor = CommandExecutor(
            registry=mock_registry,
            store=mock_store,
            audit_logger=AsyncMock(),
            company_context=MagicMock(instance_id="test"),
            user_context=MagicMock(telegram_user_id=12345),
        )
        
        result = await executor.confirm(executed_pending.command_id, 12345)
        
        assert result.success is True
        assert result.idempotent is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])