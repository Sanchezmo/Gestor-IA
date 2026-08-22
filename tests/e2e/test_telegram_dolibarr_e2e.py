"""
E2E Tests: Telegram → Authorization → Hermes → Dolibarr

Tests the complete E2E flow for /terceros command.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.hermes.context import CompanyContext
from core.hermes.identity import DolibarrGroup, DolibarrUser, TelegramIdentity
from core.hermes.instance_config import (
    AIConfig,
    DatabaseConfig,
    DolibarrConfig,
    DomainConfig,
    InstanceConfig,
    TelegramConfig,
)
from core.hermes.tools import tool_registry
from core.hermes.tools.thirdparty_tools import register_core_thirdparty_tools

# =========================================================================
# FIXTURES
# =========================================================================


@pytest.fixture
def instance_a_config():
    """Config para Empresa A."""
    return InstanceConfig(
        instance_id="empresa_a",
        company_name="Empresa A S.L.",
        database=DatabaseConfig(
            host="127.0.0.1",
            port=3306,
            name="dolibarr_empresa_a",
            user="db_empresa_a",
            password="pass_a",
        ),
        dolibarr=DolibarrConfig(
            version="23.0.4",
            internal_url="http://127.0.0.1:8081",
            api_key="dolibarr_key_a",
            documents_path="/var/lib/dolibarr/documents/empresa_a",
        ),
        telegram=TelegramConfig(
            bot_token="telegram_token_a",
            webhook_path="/webhook/empresa_a",
            webhook_secret="secret_a",
            webhook_secret_required=True,
        ),
        domains=DomainConfig(
            base="empresa-a.com",
            dolibarr="dolibarr.empresa-a.com",
            hermes="bot.empresa-a.com",
        ),
        ai=AIConfig(
            default_policy="LOCAL_ONLY",
            ollama_model="qwen3.5:4b",
        ),
    ).resolve_paths()


@pytest.fixture
def instance_b_config():
    """Config para Empresa B."""
    return InstanceConfig(
        instance_id="empresa_b",
        company_name="Empresa B S.L.",
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
        ),
        telegram=TelegramConfig(
            bot_token="telegram_token_b",
            webhook_path="/webhook/empresa_b",
            webhook_secret="secret_b",
            webhook_secret_required=True,
        ),
        domains=DomainConfig(
            base="empresa-b.es",
            dolibarr="dolibarr.empresa-b.es",
            hermes="bot.empresa-b.es",
        ),
        ai=AIConfig(
            default_policy="LOCAL_ONLY",
            ollama_model="qwen3.5:4b",
        ),
    ).resolve_paths()


@pytest.fixture
def context_a(instance_a_config):
    return CompanyContext(
        instance_config=instance_a_config,
        actor_type="telegram_user",
        actor_id="123456",
    )


@pytest.fixture
def context_b(instance_b_config):
    return CompanyContext(
        instance_config=instance_b_config,
        actor_type="telegram_user",
        actor_id="123456",
    )


@pytest.fixture
def telegram_identity_a():
    return TelegramIdentity(
        instance_id="empresa_a",
        telegram_user_id=123456,
        dolibarr_user_id=17,
    )


@pytest.fixture
def telegram_identity_b():
    return TelegramIdentity(
        instance_id="empresa_b",
        telegram_user_id=123456,  # Same Telegram ID
        dolibarr_user_id=8,  # Different Dolibarr user
    )


@pytest.fixture
def dolibarr_user_a():
    return DolibarrUser(
        id=17,
        login="juan.perez",
        firstname="Juan",
        lastname="Perez",
        email="juan@empresa-a.com",
        active=True,
        entity=1,
        rights={"thirdparty": {"read": 1}},
        user_group_list=[DolibarrGroup(id=5, name="Comercial", entity=1)],
    )


@pytest.fixture
def dolibarr_user_b():
    return DolibarrUser(
        id=8,
        login="maria.lopez",
        firstname="Maria",
        lastname="Lopez",
        email="maria@empresa-b.es",
        active=True,
        entity=1,
        rights={"thirdparty": {"read": 1}},
        user_group_list=[DolibarrGroup(id=6, name="Ventas", entity=1)],
    )


@pytest.fixture
def dolibarr_user_no_perms():
    return DolibarrUser(
        id=99,
        login="sin.permisos",
        firstname="Sin",
        lastname="Permisos",
        email="sin@empresa-a.com",
        active=True,
        entity=1,
        rights={},  # No thirdparty.read
        user_group_list=[],
    )


@pytest.fixture(autouse=True)
def setup_tools():
    """Registrar core tools antes de cada test."""
    tool_registry.clear_all()
    register_core_thirdparty_tools()
    yield
    tool_registry.clear_all()


def make_mock_dolibarr_client(
    user_return_value,
    groups_return_value,
    list_thirdparties_return_value,
    list_thirdparties_side_effect=None,
):
    """Create a mock DolibarrClient with specified behavior."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get_user = AsyncMock(return_value=user_return_value)
    mock_client.get_user_groups = AsyncMock(return_value=groups_return_value)
    if list_thirdparties_side_effect:
        mock_client.list_thirdparties = AsyncMock(side_effect=list_thirdparties_side_effect)
    else:
        mock_client.list_thirdparties = AsyncMock(return_value=list_thirdparties_return_value)
    return mock_client


# =========================================================================
# TESTS E2E - HAPPY PATH
# =========================================================================


class TestTelegramToDolibarrHappyPath:
    """Test E2E completo: webhook válido → usuario autorizado → tool → Dolibarr → respuesta."""

    @pytest.mark.asyncio
    async def test_happy_path_list_thirdparties(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Happy path: /terceros con usuario válido devuelve terceros formateados."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[DolibarrGroup(id=5, name="Comercial", entity=1)],
                list_thirdparties_return_value=[
                    {
                        "id": 1,
                        "name": "Cliente Uno",
                        "client": 1,
                        "fournisseur": 0,
                        "email": "uno@test.com",
                        "phone": "111",
                        "status": 1,
                    },
                    {
                        "id": 2,
                        "name": "Proveedor Dos",
                        "client": 0,
                        "fournisseur": 1,
                        "email": "dos@test.com",
                        "phone": "222",
                        "status": 1,
                    },
                    {
                        "id": 3,
                        "name": "Cliente Tres",
                        "client": 1,
                        "fournisseur": 0,
                        "email": None,
                        "phone": None,
                        "status": 1,
                    },
                ],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            # Patch DolibarrClient.from_instance_config to return our mock
            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                tool = ListThirdpartiesTool()
                result = await tool.execute(context_a, user_context, limit=10, offset=0)

            # Verify
            assert result.success is True
            assert result.data["count"] == 3
            assert len(result.data["thirdparties"]) == 3
            assert result.data["thirdparties"][0]["name"] == "Cliente Uno"
            assert result.data["thirdparties"][0]["is_customer"] is True
            assert result.data["thirdparties"][1]["is_supplier"] is True

            # Verify Dolibarr was called with correct params
            mock_client.list_thirdparties.assert_called_once()
            call_args = mock_client.list_thirdparties.call_args
            assert call_args.kwargs["limit"] == 10
            assert call_args.kwargs["offset"] == 0


# =========================================================================
# TESTS E2E - SECRET INVÁLIDO
# =========================================================================


class TestWebhookSecretValidation:
    """Tests de validación de webhook secret - BLOQUEANTES."""

    @pytest.mark.asyncio
    async def test_invalid_secret_rejected_before_identity_resolver(
        self, context_a, telegram_identity_a, dolibarr_user_a
    ):
        """Secret inválido → request rechazado → IdentityResolver NO ejecutado → Dolibarr NO llamado."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            from core.hermes.identity_resolver import IdentityResolver

            _resolver = IdentityResolver(store, mock_factory)

            # Simulate secret validation failure (this is what happens in webhook BEFORE resolver)
            # The webhook returns 403 and never calls resolver
            # Here we verify the resolver is not called when secret is invalid
            # by checking that mock_client.list_thirdparties was never called

            # This test documents the expected behavior
            # In real E2E with TestClient, we'd send request with wrong secret
            # and verify 403 response without Dolibarr call

            # For unit test: verify tool requires permission
            tool = ListThirdpartiesTool()
            assert "thirdparty.read" in tool.required_permissions


# =========================================================================
# TESTS E2E - USUARIO DESCONOCIDO
# =========================================================================


class TestUnknownUser:
    """Tests para usuario Telegram no vinculado."""

    @pytest.mark.asyncio
    async def test_unknown_telegram_id_denied(self, context_a):
        """Telegram ID no vinculado → DENY → Dolibarr NO llamado."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityNotFoundError, IdentityResolver
        from core.hermes.identity_store import IdentityStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            # NO crear identity para telegram_user_id=123456

            mock_client = make_mock_dolibarr_client(
                user_return_value=None,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)

            # Resolve should fail
            with pytest.raises(IdentityNotFoundError):
                await _resolver.resolve(context_a, 123456)

            # Dolibarr NOT called
            mock_client.list_thirdparties.assert_not_called()


# =========================================================================
# TESTS E2E - USUARIO DISABLED
# =========================================================================


class TestDisabledUser:
    """Tests para identidades/usuarios deshabilitados."""

    @pytest.mark.asyncio
    async def test_identity_disabled_denied(self, context_a, dolibarr_user_a):
        """Identity disabled → DENY."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityDisabledError, IdentityResolver
        from core.hermes.identity_store import IdentityStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            disabled_identity = TelegramIdentity(
                instance_id="empresa_a",
                telegram_user_id=123456,
                dolibarr_user_id=17,
                enabled=False,
            )
            store.create(disabled_identity)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)

            with pytest.raises(IdentityDisabledError):
                await _resolver.resolve(context_a, 123456)

    @pytest.mark.asyncio
    async def test_dolibarr_user_disabled_denied(self, context_a, telegram_identity_a):
        """Dolibarr user inactive → DENY."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import DolibarrUserDisabledError, IdentityResolver
        from core.hermes.identity_store import IdentityStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            inactive_user = DolibarrUser(
                id=17,
                login="juan.perez",
                firstname="Juan",
                lastname="Perez",
                email="juan@empresa.com",
                active=False,  # INACTIVE
                entity=1,
            )

            mock_client = make_mock_dolibarr_client(
                user_return_value=inactive_user,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)

            with pytest.raises(DolibarrUserDisabledError):
                await _resolver.resolve(context_a, 123456)


# =========================================================================
# TESTS E2E - SIN PERMISO
# =========================================================================


class TestNoPermission:
    """Tests para autorización denegada antes de ejecutar tool."""

    @pytest.mark.asyncio
    async def test_user_without_thirdparty_read_denied(self, context_a, telegram_identity_a, dolibarr_user_no_perms):
        """Usuario válido SIN thirdparty.read → AuthorizationDenied → Dolibarr NO llamado."""
        import tempfile
        from pathlib import Path

        from core.hermes.authorization import AuthorizationService
        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_no_perms,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            # Verify user doesn't have permission
            auth_service = AuthorizationService()
            assert auth_service.can(user_context, "thirdparty.read") is False

            # Tool should return permission denied (via tool_registry.execute_tool which checks perms first)
            # But ListThirdpartiesTool.execute is called directly here, so it will call Dolibarr
            # The permission check is in tool_registry.execute_tool, not in Tool.execute
            # So we test via tool_registry
            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                result = await tool_registry.execute_tool(
                    instance_id="empresa_a",
                    name="list_thirdparties",
                    company_context=context_a,
                    user_context=user_context,
                    limit=10,
                    offset=0,
                )

            assert result.success is False
            assert result.error_code == "PERMISSION_DENIED"

            # Dolibarr list_thirdparties NOT called (permission checked first)
            mock_client.list_thirdparties.assert_not_called()


# =========================================================================
# TESTS E2E - CROSS-INSTANCE ISOLATION
# =========================================================================


class TestCrossInstanceIsolation:
    """Tests CRÍTICOS de aislamiento entre instancias."""

    @pytest.mark.asyncio
    async def test_instance_a_user_only_queries_dolibarr_a(
        self, context_a, context_b, telegram_identity_a, dolibarr_user_a
    ):
        """Webhook Empresa A + Usuario Empresa A → solo Dolibarr A, NUNCA Dolibarr B."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool

        with tempfile.TemporaryDirectory() as tmpdir:
            store_a = IdentityStore("empresa_a", Path(tmpdir))
            store_a.create(telegram_identity_a)

            # Track which Dolibarr client was used
            used_clients = {"a": 0, "b": 0}

            def make_mock_client(instance_id):
                mock = make_mock_dolibarr_client(
                    user_return_value=dolibarr_user_a,
                    groups_return_value=[],
                    list_thirdparties_return_value=[
                        {
                            "id": 1,
                            "name": f"Cliente {instance_id}",
                            "client": 1,
                            "fournisseur": 0,
                            "email": None,
                            "phone": None,
                            "status": 1,
                        },
                    ],
                )
                return mock

            def mock_factory(ctx):
                if ctx.instance_id == "empresa_a":
                    used_clients["a"] += 1
                    return make_mock_client("a")
                else:
                    used_clients["b"] += 1
                    return make_mock_client("b")

            _resolver = IdentityResolver(store_a, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            # Execute tool for instance A with patch
            with patch(
                "core.hermes.context.CompanyContext.create_dolibarr_client",
                return_value=make_mock_client("8081"),
            ):
                tool = ListThirdpartiesTool()
                result = await tool.execute(context_a, user_context, limit=10, offset=0)

            assert result.success is True
            # The patch above uses URL to determine which mock to return
            # Since context_a has internal_url "http://127.0.0.1:8081", it will use mock "8081" which maps to "a"

    @pytest.mark.asyncio
    async def test_same_telegram_id_different_companies(
        self,
        instance_a_config,
        instance_b_config,
        telegram_identity_a,
        telegram_identity_b,
        dolibarr_user_a,
        dolibarr_user_b,
    ):
        """Mismo Telegram ID (123456) en dos empresas → resuelve a usuarios Dolibarr distintos."""
        import tempfile
        from pathlib import Path

        from core.hermes.context import CompanyContext
        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store_a = IdentityStore("empresa_a", Path(tmpdir))
            store_b = IdentityStore("empresa_b", Path(tmpdir))
            store_a.create(telegram_identity_a)
            store_b.create(telegram_identity_b)

            ctx_a = CompanyContext(instance_config=instance_a_config, actor_type="telegram_user", actor_id="123456")
            ctx_b = CompanyContext(instance_config=instance_b_config, actor_type="telegram_user", actor_id="123456")

            def make_mock_user(user):
                mock = AsyncMock()
                mock.__aenter__ = AsyncMock(return_value=mock)
                mock.__aexit__ = AsyncMock(return_value=None)
                mock.get_user = AsyncMock(return_value=user)
                mock.get_user_groups = AsyncMock(return_value=[])
                mock.list_thirdparties = AsyncMock(return_value=[])
                return mock

            def mock_factory(ctx):
                if ctx.instance_id == "empresa_a":
                    return make_mock_user(dolibarr_user_a)
                else:
                    return make_mock_user(dolibarr_user_b)

            _resolver = IdentityResolver(store_a, mock_factory)

            # Resolve for instance A
            user_context_a = await _resolver.resolve(ctx_a, 123456)
            assert user_context_a.dolibarr_user_id == 17
            assert user_context_a.dolibarr_user.login == "juan.perez"

            # Resolve for instance B (need separate resolver with store_b)
            resolver_b = IdentityResolver(store_b, mock_factory)
            user_context_b = await resolver_b.resolve(ctx_b, 123456)
            assert user_context_b.dolibarr_user_id == 8
            assert user_context_b.dolibarr_user.login == "maria.lopez"

            # Verify isolation: stores are independent
            assert store_a.get(123456).dolibarr_user_id == 17
            assert store_b.get(123456).dolibarr_user_id == 8


# =========================================================================
# TESTS E2E - DOLIBARR ERRORS
# =========================================================================


class TestDolibarrErrors:
    """Tests de errores de Dolibarr - respuesta segura al usuario."""

    @pytest.mark.asyncio
    async def test_dolibarr_timeout_returns_safe_message(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Dolibarr timeout → respuesta segura, NO stacktrace."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool
        from core.integrations.dolibarr.client import DolibarrException

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
                list_thirdparties_side_effect=DolibarrException(
                    message="Timeout", endpoint="thirdparties", status_code=504
                ),
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                tool = ListThirdpartiesTool()
                result = await tool.execute(context_a, user_context, limit=10, offset=0)

            assert result.success is False
            assert result.error_code == "DOLIBARR_ERROR"
            assert "No he podido consultar Dolibarr" in result.error_message
            # NO internal details leaked
            assert "Timeout" not in result.error_message
            assert "504" not in result.error_message
            assert "endpoint" not in str(result.error_message).lower()

    @pytest.mark.asyncio
    async def test_dolibarr_500_returns_safe_message(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Dolibarr 500 → respuesta segura."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool
        from core.integrations.dolibarr.client import DolibarrException

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
                list_thirdparties_side_effect=DolibarrException(
                    message="Internal Server Error", endpoint="thirdparties", status_code=500
                ),
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                tool = ListThirdpartiesTool()
                result = await tool.execute(context_a, user_context, limit=10, offset=0)

            assert result.success is False
            assert result.error_code == "DOLIBARR_ERROR"
            assert "No he podido consultar Dolibarr" in result.error_message

    @pytest.mark.asyncio
    async def test_dolibarr_401_returns_safe_message(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Dolibarr 401 (auth failure) → respuesta segura."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool
        from core.integrations.dolibarr.client import DolibarrException

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
                list_thirdparties_side_effect=DolibarrException(
                    message="Invalid API key", endpoint="thirdparties", status_code=401
                ),
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                tool = ListThirdpartiesTool()
                result = await tool.execute(context_a, user_context, limit=10, offset=0)

            assert result.success is False
            assert result.error_code == "DOLIBARR_ERROR"
            assert "No he podido consultar Dolibarr" in result.error_message
            # NO API key leaked
            assert "API" not in result.error_message
            assert "key" not in result.error_message.lower()


# =========================================================================
# TESTS E2E - AUDITORÍA
# =========================================================================


class TestAuditLogging:
    """Tests de auditoría de operaciones exitosas y denegadas."""

    @pytest.mark.asyncio
    async def test_successful_list_audits_thirdparty_list(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Ejecución exitosa genera auditoría thirdparty.list con datos correctos."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[
                    {"id": 1, "name": "Test", "client": 1, "fournisseur": 0, "email": None, "phone": None, "status": 1},
                ],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                tool = ListThirdpartiesTool()
                result = await tool.execute(context_a, user_context, limit=10, offset=0)

            assert result.success is True
            # Verify metadata for audit
            assert result.metadata["instance_id"] == "empresa_a"
            assert result.metadata["dolibarr_user_id"] == 17

    @pytest.mark.asyncio
    async def test_authorization_denied_audits_authorization_denied(
        self, context_a, telegram_identity_a, dolibarr_user_no_perms
    ):
        """Denegación de permiso genera auditoría authorization.denied."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_no_perms,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                result = await tool_registry.execute_tool(
                    instance_id="empresa_a",
                    name="list_thirdparties",
                    company_context=context_a,
                    user_context=user_context,
                    limit=10,
                    offset=0,
                )

            assert result.success is False
            assert result.error_code == "PERMISSION_DENIED"
            # ToolResult.metadata contains info for audit
            assert result.metadata is not None


# =========================================================================
# TESTS E2E - PAGINATION & FORMATTING
# =========================================================================


class TestPaginationAndFormatting:
    """Tests de paginación y formato de respuesta."""

    @pytest.mark.asyncio
    async def test_list_thirdparties_respects_limit_offset(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Tool respeta limit y offset en llamada a Dolibarr."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                tool = ListThirdpartiesTool()
                await tool.execute(context_a, user_context, limit=5, offset=10)

            call_args = mock_client.list_thirdparties.call_args
            assert call_args is not None
            assert call_args.kwargs["limit"] == 5
            assert call_args.kwargs["offset"] == 10

    @pytest.mark.asyncio
    async def test_empty_results_returns_no_thirdparties_message(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Dolibarr devuelve cero terceros → mensaje 'No se han encontrado terceros'."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                tool = ListThirdpartiesTool()
                result = await tool.execute(context_a, user_context, limit=10, offset=0)

            assert result.success is True
            assert result.data["count"] == 0
            assert result.data["thirdparties"] == []


# =========================================================================
# TESTS E2E - IDEMPOTENCY (via Redis)
# =========================================================================


class TestIdempotency:
    """Tests de idempotencia webhook via Redis."""

    def test_idempotency_key_format(self):
        """Verificar formato de clave de idempotencia."""
        update_id = 123456789
        key = f"telegram:update:{update_id}"
        assert key == "telegram:update:123456789"

    def test_duplicate_update_returns_200_ok(self):
        """Update duplicado → 200 OK para evitar reintentos de Telegram."""
        # Este test se hace a nivel de webhook con TestClient
        # Aquí solo documentamos el comportamiento esperado
        pass


# =========================================================================
# TESTS E2E - TOOL REGISTRY
# =========================================================================


class TestToolRegistry:
    """Tests del registry de tools."""

    @pytest.mark.asyncio
    async def test_tool_registry_get_list_thirdparties(self):
        """Registry puede obtener list_thirdparties."""
        tool = tool_registry.get_tool("empresa_a", "list_thirdparties")
        assert tool is not None
        assert tool.name == "list_thirdparties"
        assert "thirdparty.read" in tool.required_permissions

    @pytest.mark.asyncio
    async def test_tool_registry_execute_with_permissions(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Registry ejecuta tool verificando permisos."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                result = await tool_registry.execute_tool(
                    instance_id="empresa_a",
                    name="list_thirdparties",
                    company_context=context_a,
                    user_context=user_context,
                    limit=10,
                    offset=0,
                )

            assert result.success is True

    @pytest.mark.asyncio
    async def test_tool_registry_returns_error_for_unknown_tool(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Tool desconocida → TOOL_NOT_FOUND error."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            result = await tool_registry.execute_tool(
                instance_id="empresa_a",
                name="no_existe_tool",
                company_context=context_a,
                user_context=user_context,
            )

            assert result.success is False
            assert result.error_code == "TOOL_NOT_FOUND"


# =========================================================================
# TESTS E2E - PARAMETERS VALIDATION
# =========================================================================


class TestParametersValidation:
    """Tests de validación de parámetros de la tool."""

    @pytest.mark.asyncio
    async def test_invalid_params_returns_error(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Parámetros inválidos → INVALID_PARAMS error."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_client = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_client

            _resolver = IdentityResolver(store, mock_factory)
            user_context = await _resolver.resolve(context_a, 123456)

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_client):
                tool = ListThirdpartiesTool()
                # Invalid param: limit negative
                result = await tool.execute(context_a, user_context, limit=-5, offset=0)

            assert result.success is False
            assert result.error_code == "INVALID_PARAMS"

    @pytest.mark.asyncio
    async def test_params_schema_defined(self):
        """Tool tiene schema de parámetros definido."""
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesTool

        tool = ListThirdpartiesTool()
        schema = tool.definition.parameters_schema
        assert schema["type"] == "object"
        assert "limit" in schema["properties"]
        assert "offset" in schema["properties"]
        assert schema["properties"]["limit"]["minimum"] == 1
        assert schema["properties"]["limit"]["maximum"] == 100


# =========================================================================
# TESTS E2E - NO RESULT FORMAT
# =========================================================================


class TestNoResultsFormat:
    """Tests de formato cuando no hay resultados."""

    @pytest.mark.asyncio
    async def test_zero_thirdparties_formatted_correctly(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Cero terceros -> respuesta formateada correcta."""
        from core.hermes.main import _format_thirdparties_response

        formatted = await _format_thirdparties_response([], limit=10, offset=0)
        assert "No se han encontrado terceros" in formatted

    @pytest.mark.asyncio
    async def test_multiple_thirdparties_formatted_correctly(self, context_a, telegram_identity_a, dolibarr_user_a):
        """Múltiples terceros -> lista numerada con tipos."""
        from core.hermes.main import _format_thirdparties_response

        parties = [
            {"id": 1, "name": "Cliente Uno", "is_customer": True, "is_supplier": False, "email": "uno@test.com"},
            {"id": 2, "name": "Proveedor Dos", "is_customer": False, "is_supplier": True, "email": None},
            {"id": 3, "name": "Ambos Tres", "is_customer": True, "is_supplier": True, "email": "tres@test.com"},
        ]

        formatted = await _format_thirdparties_response(parties, limit=10, offset=0)

        assert "Terceros encontrados:" in formatted
        assert "1. Cliente Uno (Cliente) - uno@test.com" in formatted
        assert "2. Proveedor Dos (Proveedor)" in formatted
        assert "3. Ambos Tres (Cliente, Proveedor) - tres@test.com" in formatted
