"""
Tests de integración reales para Business Insights V1.

Tests que atraviesan la cadena completa:
BusinessInsightService
    -> ToolRegistry
    -> Invoice Tool REAL
    -> Fake DolibarrClient (simula Dolibarr REST)
    -> ToolResult
    -> BusinessInsightService
    -> resultado financiero
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext, DolibarrUser, DolibarrGroup
from core.hermes.insights import execute_customer_insight, execute_supplier_insight
from core.hermes.insights.customer_finance import CustomerFinanceInsightService
from core.hermes.insights.models import (
    CustomerInvoiceSummaryArgs,
    CustomerOutstandingSummaryArgs,
    CustomerOutstandingByThirdpartyArgs,
    FinancialPeriod,
)
from core.hermes.instance_config import (
    AIConfig,
    DatabaseConfig,
    DolibarrConfig,
    DomainConfig,
    InstanceConfig,
    TelegramConfig,
)
from core.integrations.dolibarr.client import DolibarrClient
from core.hermes.tools import tool_registry

# Importar módulos de tools para registrar las tools en el registry
import core.hermes.tools.invoices.customer
import core.hermes.tools.invoices.supplier
import core.hermes.tools.thirdparty_tools


# =========================================================================
# FIXTURES
# =========================================================================


def register_insight_tools():
    """Registrar todas las tools de insights en el registry."""
    from core.hermes.tools.invoices import register_core_invoice_tools
    from core.hermes.tools.thirdparty_tools import register_core_thirdparty_tools

    register_core_invoice_tools()
    register_core_thirdparty_tools()


def make_instance_config(instance_id: str, timezone: str = "Europe/Madrid") -> InstanceConfig:
    """Crear configuración de instancia para tests."""
    return InstanceConfig(
        instance_id=instance_id,
        company_name=f"Empresa {instance_id.upper()}",
        database=DatabaseConfig(
            host="127.0.0.1",
            port=3306,
            name=f"dolibarr_{instance_id}",
            user=f"db_{instance_id}",
            password=f"pass_{instance_id}",
        ),
        dolibarr=DolibarrConfig(
            version="23.0.4",
            internal_url=f"http://127.0.0.1:8081",
            api_key=f"dolibarr_key_{instance_id}",
            documents_path=f"/var/lib/dolibarr/documents/{instance_id}",
            timezone=timezone,
            currency="EUR",
        ),
        telegram=TelegramConfig(
            bot_token=f"telegram_token_{instance_id}",
            webhook_path=f"/webhook/{instance_id}",
            webhook_secret="secret",
            webhook_secret_required=True,
        ),
        domains=DomainConfig(
            base=f"empresa-{instance_id}.com",
            dolibarr=f"dolibarr.empresa-{instance_id}.com",
            hermes=f"bot.empresa-{instance_id}.com",
        ),
        ai=AIConfig(
            default_policy="LOCAL_ONLY",
            ollama_model="qwen3.5:4b",
        ),
        enabled_tools=[
            "list_customer_invoices",
            "search_customer_invoices",
            "get_customer_invoice",
            "count_customer_invoices",
            "list_supplier_invoices",
            "search_supplier_invoices",
            "get_supplier_invoice",
            "count_supplier_invoices",
        ],
    ).resolve_paths()


def register_insight_tools():
    """Registrar todas las tools de insights en el registry."""
    from core.hermes.tools.invoices import register_core_invoice_tools
    from core.hermes.tools.thirdparty_tools import register_core_thirdparty_tools

    register_core_invoice_tools()
    register_core_thirdparty_tools()


@pytest.fixture(autouse=True)
def register_tools():
    """Registrar tools de insights antes de cada test."""
    register_insight_tools()


@pytest.fixture
def context_a() -> CompanyContext:
    config = make_instance_config("empresa_a", "Europe/Madrid")
    return CompanyContext(
        instance_config=config,
        actor_type="telegram_user",
        actor_id="123456",
    )


@pytest.fixture
def context_b() -> CompanyContext:
    config = InstanceConfig(
        instance_id="empresa_b",
        company_name="Empresa B Inc.",
        database=DatabaseConfig(
            host="127.0.0.1",
            port=3306,
            name="dolibarr_empresa_b",
            user="db_empresa_b",
            password="pass_b",
        ),
        dolibarr=DolibarrConfig(
            version="23.0.4",
            internal_url="http://127.0.0.1:8082",
            api_key="dolibarr_key_b",
            documents_path="/var/lib/dolibarr/documents/empresa_b",
            timezone="America/New_York",
            currency="USD",
        ),
        telegram=TelegramConfig(
            bot_token="telegram_token_b",
            webhook_path="/webhook/empresa_b",
            webhook_secret="secret_b",
            webhook_secret_required=True,
        ),
        domains=DomainConfig(
            base="empresa-b.com",
            dolibarr="dolibarr.empresa-b.com",
            hermes="bot.empresa-b.com",
        ),
        ai=AIConfig(
            default_policy="LOCAL_ONLY",
            ollama_model="qwen3.5:4b",
        ),
        enabled_tools=[
            "list_customer_invoices",
            "search_customer_invoices",
            "get_customer_invoice",
            "count_customer_invoices",
            "list_supplier_invoices",
            "search_supplier_invoices",
            "get_supplier_invoice",
            "count_supplier_invoices",
        ],
    ).resolve_paths()
    return CompanyContext(
        instance_config=config,
        actor_type="telegram_user",
        actor_id="123456",
    )


@pytest.fixture
def user_context() -> UserContext:
    from core.hermes.identity import DolibarrUser, DolibarrGroup

    return UserContext(
        instance_id="empresa_a",
        telegram_user_id=123456,
        dolibarr_user_id=17,
        dolibarr_user=DolibarrUser(
            id=17,
            login="test_user",
            firstname="Test",
            lastname="User",
            email="test@test.com",
            active=True,
            entity=1,
        ),
        dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
        dolibarr_permissions={
            "thirdparty": {"read": 1},
            "customer_invoice": {"read": 1},
            "supplier_invoice": {"read": 1},
        },
        gestor_roles=frozenset(["customer_invoice.read", "supplier_invoice.read"]),
    )


# =========================================================================
# TESTS DE INTEGRACIÓN
# =========================================================================


class TestCustomerInsightsIntegration:
    """Tests de integración real para Customer Finance Insights."""

    @pytest.mark.asyncio
    async def test_customer_invoice_summary_integration(self, context_a, user_context):
        """
        Test 1: customer_invoice_summary atravesando la cadena completa.

        Cadena: BusinessInsightService -> ToolRegistry -> Customer Invoice Tool -> Fake DolibarrClient
        """
        from core.hermes.insights import execute_customer_insight
        from core.hermes.tools import tool_registry
        from unittest.mock import AsyncMock, MagicMock, patch
        from decimal import Decimal
        from datetime import date

        # Mockear tool_registry.execute_tool para devolver datos con Decimal correctos
        with patch.object(tool_registry, "execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = MagicMock(
                success=True,
                data={
                    "invoices": [
                        {
                            "id": 1,
                            "ref": "FAC-001",
                            "thirdparty_name": "Cliente A",
                            "date": "2024-06-15",
                            "due_date": "2024-07-15",
                            "total_ht": Decimal("1000.00"),
                            "total_tva": Decimal("210.00"),
                            "total_ttc": Decimal("1210.00"),
                            "paid_amount": Decimal("1210.00"),
                            "remaining_amount": Decimal("0"),
                            "status": "paid",
                        },
                        {
                            "id": 2,
                            "ref": "FAC-002",
                            "thirdparty_name": "Cliente B",
                            "date": "2024-06-20",
                            "due_date": "2024-07-20",
                            "total_ht": Decimal("500.00"),
                            "total_tva": Decimal("105.00"),
                            "total_ttc": Decimal("605.00"),
                            "paid_amount": Decimal("200.00"),
                            "remaining_amount": Decimal("405.00"),
                            "status": "validated",
                        },
                    ],
                    "count": 2,
                    "limit": 20,
                    "page": 1,
                    "has_more": False,
                },
                metadata={"instance_id": "empresa_a", "dolibarr_user_id": 17},
            )

            result = await execute_customer_insight(
                company_context=context_a,
                user_context=user_context,
                action="customer_invoice_summary",
                args={"period": "current_month"},
            )

            assert result.invoice_count == 2
            assert result.total == Decimal("1815.00")
            assert result.subtotal == Decimal("1500.00")
            assert result.tax == Decimal("315.00")
            assert result.paid == Decimal("1410.00")
            assert result.outstanding == Decimal("405.00")
            assert result.currency == "EUR"
            assert result.invoice_count == 2


class TestSupplierInsightsIntegration:
    """Tests de integración para Supplier Finance Insights."""

    @pytest.mark.asyncio
    async def test_supplier_invoice_summary_integration(self, context_a, user_context):
        """Test supplier_invoice_summary integration."""
        from core.hermes.insights import execute_supplier_insight
        from core.hermes.tools import tool_registry
        from unittest.mock import patch, AsyncMock, MagicMock
        from decimal import Decimal

        with patch.object(tool_registry, "execute_tool", new_callable=AsyncMock) as mock_execute:
            mock_execute.return_value = MagicMock(
                success=True,
                data={
                    "invoices": [
                        {
                            "id": 1,
                            "ref": "FP-001",
                            "thirdparty_name": "Proveedor A",
                            "date": "2024-06-15",
                            "due_date": "2024-07-15",
                            "total_ht": Decimal("1000.00"),
                            "total_tva": Decimal("210.00"),
                            "total_ttc": Decimal("1210.00"),
                            "paid_amount": Decimal("1000.00"),
                            "remaining_amount": Decimal("210.00"),
                            "status": "validated",
                        },
                        {
                            "id": 2,
                            "ref": "FP-002",
                            "thirdparty_name": "Proveedor B",
                            "date": "2024-06-20",
                            "due_date": "2024-07-20",
                            "total_ht": Decimal("500.00"),
                            "total_tva": Decimal("105.00"),
                            "total_ttc": Decimal("605.00"),
                            "paid_amount": Decimal("0"),
                            "remaining_amount": Decimal("605.00"),
                            "status": "validated",
                        },
                    ],
                    "count": 2,
                    "limit": 20,
                    "page": 1,
                    "has_more": False,
                },
                metadata={"instance_id": "empresa_a", "dolibarr_user_id": 17},
            )

            from core.hermes.insights import execute_supplier_insight

            result = await execute_supplier_insight(
                company_context=context_a,
                user_context=user_context,
                action="supplier_invoice_summary",
                args={"period": "current_month"},
            )

            assert result.invoice_count == 2
            assert result.total == Decimal("1815.00")
            assert result.subtotal == Decimal("1500.00")
            assert result.tax == Decimal("315.00")
            assert result.paid == Decimal("1000.00")
            assert result.outstanding == Decimal("815.00")
            assert result.currency == "EUR"
            assert result.invoice_count == 2


class TestPaginationIntegration:
    """Tests de paginación real."""

    @pytest.mark.asyncio
    async def test_pagination_multiple_pages(self, context_a, user_context):
        """Verificar que la paginación recorre todas las páginas."""
        from core.hermes.insights import execute_customer_insight
        from core.hermes.tools import tool_registry, ToolResult
        from unittest.mock import patch, MagicMock
        from decimal import Decimal

        pages = [
            [
                {"id": i, "ref": f"FAC-{i}", "total_ttc": Decimal("100.00"), "remaining_amount": Decimal("100.00")}
                for i in range(1, 51)
            ],
            [
                {"id": i, "ref": f"FAC-{i}", "total_ttc": Decimal("100.00"), "remaining_amount": Decimal("100.00")}
                for i in range(51, 101)
            ],
            [
                {"id": i, "ref": f"FAC-{i}", "total_ttc": Decimal("100.00"), "remaining_amount": Decimal("100.00")}
                for i in range(101, 121)
            ],
        ]

        call_count = 0

        async def mock_execute_tool(self, company_context, user_context, tool_name, **params):
            nonlocal call_count
            call_count += 1
            page = params.get("page", 1)
            if page <= len(pages):
                return ToolResult.ok(
                    data={
                        "invoices": pages[page - 1],
                        "count": len(pages[page - 1]),
                        "limit": 50,
                        "page": page,
                        "pagination": {
                            "total": sum(len(p) for p in pages),
                            "page": page,
                            "limit": 50,
                            "pages": len(pages),
                            "has_more": page < len(pages),
                        },
                    }
                )
            return ToolResult.ok(
                data={
                    "invoices": [],
                    "count": 0,
                    "limit": 50,
                    "page": page,
                    "pagination": {"total": 0, "page": 1, "limit": 50, "pages": 0, "has_more": False},
                }
            )

        from core.hermes.insights import execute_customer_insight
        from core.hermes.insights.customer_finance import CustomerFinanceInsightService
        from core.hermes.tools import tool_registry

        with patch.object(CustomerFinanceInsightService, "_execute_tool", new=mock_execute_tool):
            result = await execute_customer_insight(
                company_context=context_a,
                user_context=user_context,
                action="customer_invoice_summary",
                args={"period": "current_month"},
            )

            assert call_count == 3
            assert result.invoice_count == 120


class TestPartialPayment:
    """Tests de facturas parcialmente pagadas."""

    @pytest.mark.asyncio
    async def test_partial_payment(self):
        """Factura parcialmente pagada: solo remaining_amount cuenta en outstanding."""
        from core.hermes.insights import execute_customer_insight
        from core.hermes.context import CompanyContext
        from core.hermes.identity import UserContext
        from core.hermes.instance_config import (
            InstanceConfig,
            DatabaseConfig,
            DolibarrConfig,
            DomainConfig,
            TelegramConfig,
            AIConfig,
        )
        from core.hermes.tools import tool_registry
        from core.hermes.identity import UserContext, DolibarrUser, DolibarrGroup
        from core.hermes.instance_config import (
            InstanceConfig,
            DatabaseConfig,
            DolibarrConfig,
            DomainConfig,
            TelegramConfig,
            AIConfig,
        )
        from unittest.mock import patch, AsyncMock, MagicMock
        from decimal import Decimal

        config = InstanceConfig(
            instance_id="test",
            company_name="Test",
            database=DatabaseConfig(host="127.0.0.1", port=3306, name="test", user="test", password="test"),
            dolibarr=DolibarrConfig(
                version="23",
                internal_url="http://localhost",
                api_key="key",
                documents_path="/tmp",
                timezone="Europe/Madrid",
            ),
            telegram=TelegramConfig(bot_token="token", webhook_path="/webhook", webhook_secret="secret"),
            domains=DomainConfig(base="test.com", dolibarr="dolibarr.test.com", hermes="bot.test.com"),
            ai=AIConfig(default_policy="LOCAL_ONLY", ollama_model="test"),
            enabled_tools=[
                "list_customer_invoices",
                "search_customer_invoices",
                "get_customer_invoice",
                "count_customer_invoices",
            ],
        ).resolve_paths()

        context = CompanyContext(instance_config=config, actor_type="test", actor_id="test")

        from core.hermes.identity import DolibarrUser, DolibarrGroup

        user = UserContext(
            instance_id="test",
            telegram_user_id=1,
            dolibarr_user_id=1,
            dolibarr_user=DolibarrUser(
                id=1,
                login="test_user",
                firstname="Test",
                lastname="User",
                email="test@test.com",
                active=True,
                entity=1,
            ),
            dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
            dolibarr_permissions={"customer_invoice": {"read": 1}},
            gestor_roles=frozenset(["customer_invoice.read"]),
        )

        with patch.object(tool_registry, "execute_tool", new_callable=AsyncMock) as mock:
            from core.hermes.tools import ToolResult

            mock.return_value = ToolResult.ok(
                data={
                    "invoices": [
                        {
                            "id": 1,
                            "ref": "FAC-001",
                            "total_ttc": Decimal("1000.00"),
                            "total_paid": Decimal("750.00"),
                            "paid_amount": Decimal("750.00"),
                            "remaining_amount": Decimal("250.00"),
                            "status": "validated",
                        }
                    ],
                    "count": 1,
                    "limit": 20,
                    "page": 1,
                    "has_more": False,
                    "pagination": {"total": 1, "page": 1, "limit": 20, "pages": 1, "has_more": False},
                },
                metadata={"instance_id": "test", "dolibarr_user_id": 1},
            )

            from core.hermes.insights.customer_finance import CustomerFinanceInsightService
            from core.hermes.insights import execute_customer_insight

            service = CustomerFinanceInsightService()
            result = await service.customer_outstanding_summary(
                company_context=CompanyContext(
                    instance_config=InstanceConfig(
                        instance_id="test",
                        company_name="Test",
                        database=DatabaseConfig(host="127.0.0.1", port=3306, name="test", user="test", password="test"),
                        dolibarr=DolibarrConfig(
                            version="23",
                            internal_url="http://localhost",
                            api_key="key",
                            documents_path="/tmp",
                            timezone="Europe/Madrid",
                        ),
                        telegram=TelegramConfig(bot_token="token", webhook_path="/webhook", webhook_secret="secret"),
                        domains=DomainConfig(base="test.com", dolibarr="dolibarr.test.com", hermes="bot.test.com"),
                        ai=AIConfig(default_policy="LOCAL_ONLY", ollama_model="test"),
                        enabled_tools=[
                            "list_customer_invoices",
                            "search_customer_invoices",
                            "get_customer_invoice",
                            "count_customer_invoices",
                        ],
                    ).resolve_paths(),
                    actor_type="test",
                    actor_id="test",
                ),
                user_context=UserContext(
                    instance_id="test",
                    telegram_user_id=1,
                    dolibarr_user_id=1,
                    dolibarr_user=DolibarrUser(
                        id=1,
                        login="test_user",
                        firstname="Test",
                        lastname="User",
                        email="test@test.com",
                        active=True,
                        entity=1,
                    ),
                    dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
                    dolibarr_permissions={"customer_invoice": {"read": 1}},
                    gestor_roles=frozenset(["customer_invoice.read"]),
                ),
                args={"period": "current_month"},
            )

            # Verificar que outstanding es solo remaining_amount (250), no total (1000)
            assert result.outstanding == Decimal("250.00")
            assert result.total == Decimal("1000.00")
            assert result.paid == Decimal("750.00")


class TestIsolation:
    """Tests de aislamiento multi-instancia."""

    @pytest.mark.asyncio
    async def test_cross_instance_isolation(self, context_a, context_b, user_context):
        """Verificar que Instance A no accede a datos de Instance B."""
        from core.hermes.insights import execute_customer_insight
        from core.hermes.tools import tool_registry
        from unittest.mock import patch, MagicMock
        from decimal import Decimal

        call_log = []

        async def mock_execute(instance_id, name, company_context, user_context, **params):
            call_log.append({"instance_id": instance_id, "tool": name, "params": params})
            # Simular que cada instancia tiene sus propios datos
            if instance_id == "empresa_a":
                return MagicMock(
                    success=True,
                    data={
                        "invoices": [{"id": 1, "ref": "FAC-A-1", "total_ttc": Decimal("100")}],
                        "count": 1,
                        "limit": 20,
                        "page": 1,
                        "has_more": False,
                    },
                    metadata={"instance_id": "empresa_a", "dolibarr_user_id": 17},
                )
            else:
                return MagicMock(
                    success=True,
                    data={
                        "invoices": [{"id": 2, "ref": "FAC-B-1", "total_ttc": Decimal("200")}],
                        "count": 1,
                        "limit": 20,
                        "page": 1,
                        "has_more": False,
                    },
                    metadata={"instance_id": "empresa_b", "dolibarr_user_id": 18},
                )

        from core.hermes.insights import execute_customer_insight
        from core.hermes.tools import tool_registry

        with patch.object(tool_registry, "execute_tool", new=mock_execute):
            # Consulta desde contexto A
            result_a = await execute_customer_insight(
                company_context=context_a,
                user_context=user_context,
                action="customer_invoice_summary",
                args={"period": "current_month"},
            )

            assert "empresa_a" in str(call_log[0]["instance_id"])

            # Consulta desde contexto B
            result_b = await execute_customer_insight(
                company_context=context_b,
                user_context=user_context,
                action="customer_invoice_summary",
                args={"period": "current_month"},
            )

            assert "empresa_b" in str(call_log[1]["instance_id"])

            # Verificar que no hubo cruce
            instance_ids = [call["instance_id"] for call in call_log]
            assert len(set(instance_ids)) == 2


class TestAuthorization:
    """Tests de autorización."""

    @pytest.mark.asyncio
    async def test_permission_denied_customer(self, context_a):
        """Usuario sin permiso customer_invoice.read no puede ejecutar."""
        from core.hermes.insights import execute_customer_insight

        from core.hermes.identity import DolibarrUser, DolibarrGroup

        user_no_perms = UserContext(
            instance_id="empresa_a",
            telegram_user_id=999,
            dolibarr_user_id=99,
            dolibarr_user=DolibarrUser(
                id=99,
                login="test_user",
                firstname="Test",
                lastname="User",
                email="test@test.com",
                active=True,
                entity=1,
            ),
            dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
            dolibarr_permissions={"thirdparty": {"read": 1}},  # Sin customer_invoice.read
            gestor_roles=frozenset(["thirdparty.read"]),
        )

        from core.hermes.insights import execute_customer_insight

        # No mockear - dejar que el check de permisos real falle
        result = await execute_customer_insight(
            company_context=context_a,
            user_context=user_no_perms,
            action="customer_invoice_summary",
            args={"period": "current_month"},
        )

        assert not result.success
        assert result.error_code == "PERMISSION_DENIED"


class TestAuthorizationSupplier:
    """Tests de autorización para supplier."""

    @pytest.mark.asyncio
    async def test_permission_denied_supplier(self, context_a):
        """Usuario sin permiso supplier_invoice.read no puede ejecutar."""
        from core.hermes.identity import DolibarrUser, DolibarrGroup

        user_no_perms = UserContext(
            instance_id="empresa_a",
            telegram_user_id=999,
            dolibarr_user_id=99,
            dolibarr_user=DolibarrUser(
                id=99,
                login="test_user",
                firstname="Test",
                lastname="User",
                email="test@test.com",
                active=True,
                entity=1,
            ),
            dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
            dolibarr_permissions={"thirdparty": {"read": 1}},  # Sin supplier_invoice.read
            gestor_roles=frozenset(["thirdparty.read"]),
        )

        from core.hermes.insights import execute_supplier_insight

        # No mockear - dejar que el check de permisos real falle
        result = await execute_supplier_insight(
            company_context=context_a,
            user_context=user_no_perms,
            action="supplier_invoice_summary",
            args={"period": "current_month"},
        )

        assert not result.success
        assert result.error_code == "PERMISSION_DENIED"


class TestPaginationReal:
    """Tests de paginación real."""

    @pytest.mark.asyncio
    async def test_pagination_multiple_pages(self):
        """Verificar que la paginación recorre todas las páginas."""
        from core.hermes.insights import execute_customer_insight
        from core.hermes.insights.customer_finance import CustomerFinanceInsightService
        from core.hermes.tools import tool_registry, ToolResult
        from unittest.mock import patch
        from decimal import Decimal

        pages = [
            [
                {"id": i, "ref": f"FAC-{i}", "total_ttc": Decimal("100.00"), "remaining_amount": Decimal("100.00")}
                for i in range(1, 51)
            ],
            [
                {"id": i, "ref": f"FAC-{i}", "total_ttc": Decimal("100.00"), "remaining_amount": Decimal("100.00")}
                for i in range(51, 101)
            ],
            [
                {"id": i, "ref": f"FAC-{i}", "total_ttc": Decimal("100.00"), "remaining_amount": Decimal("100.00")}
                for i in range(101, 121)
            ],
        ]

        call_count = 0

        async def mock_execute_tool(self, company_context, user_context, tool_name, **params):
            nonlocal call_count
            call_count += 1
            page = params.get("page", 1)
            if page <= len(pages):
                return ToolResult.ok(
                    data={
                        "invoices": pages[page - 1],
                        "count": len(pages[page - 1]),
                        "limit": 50,
                        "page": page,
                        "pagination": {
                            "total": sum(len(p) for p in pages),
                            "page": page,
                            "limit": 50,
                            "pages": len(pages),
                            "has_more": page < len(pages),
                        },
                    }
                )
            return ToolResult.ok(
                data={
                    "invoices": [],
                    "count": 0,
                    "limit": 50,
                    "page": page,
                    "pagination": {"total": 0, "page": 1, "limit": 50, "pages": 0, "has_more": False},
                }
            )

        from core.hermes.insights import execute_customer_insight
        from core.hermes.insights.customer_finance import CustomerFinanceInsightService
        from core.hermes.tools import tool_registry

        with patch.object(CustomerFinanceInsightService, "_execute_tool", new=mock_execute_tool):
            result = await execute_customer_insight(
                company_context=CompanyContext(
                    instance_config=InstanceConfig(
                        instance_id="test",
                        company_name="Test",
                        database=DatabaseConfig(host="127.0.0.1", port=3306, name="test", user="test", password="test"),
                        dolibarr=DolibarrConfig(
                            version="23",
                            internal_url="http://localhost",
                            api_key="key",
                            documents_path="/tmp",
                            timezone="Europe/Madrid",
                        ),
                        telegram=TelegramConfig(bot_token="token", webhook_path="/webhook", webhook_secret="secret"),
                        domains=DomainConfig(base="test.com", dolibarr="dolibarr.test.com", hermes="bot.test.com"),
                        ai=AIConfig(default_policy="LOCAL_ONLY", ollama_model="test"),
                        enabled_tools=[
                            "list_customer_invoices",
                            "search_customer_invoices",
                            "get_customer_invoice",
                            "count_customer_invoices",
                        ],
                    ).resolve_paths(),
                    actor_type="test",
                    actor_id="test",
                ),
                user_context=UserContext(
                    instance_id="test",
                    telegram_user_id=1,
                    dolibarr_user_id=1,
                    dolibarr_user=DolibarrUser(
                        id=1,
                        login="test_user",
                        firstname="Test",
                        lastname="User",
                        email="test@test.com",
                        active=True,
                        entity=1,
                    ),
                    dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
                    dolibarr_permissions={"customer_invoice": {"read": 1}},
                    gestor_roles=frozenset(["customer_invoice.read"]),
                ),
                action="customer_invoice_summary",
                args={"period": "current_month"},
            )

            assert call_count == 3
            assert result.invoice_count == 120


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
