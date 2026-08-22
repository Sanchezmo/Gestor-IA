"""
HTTP E2E Tests - Real FastAPI TestClient tests that traverse the full HTTP stack.

These tests verify:
- Secret non-exposure in admin responses
- Webhook secret validation
- Happy path /terceros command
- Permission denied flows
- Cross-instance isolation
- Idempotency
- Dolibarr error handling (no internal details leaked)
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

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
from core.hermes.main import app
from core.hermes.tools import tool_registry
from core.hermes.tools.thirdparty_tools import register_core_thirdparty_tools

# =========================================================================
# FIXTURES
# =========================================================================


@pytest.fixture(autouse=True)
def setup_tools():
    """Registrar core tools antes de cada test."""
    tool_registry.clear_all()
    register_core_thirdparty_tools()
    yield
    tool_registry.clear_all()


@pytest.fixture(autouse=True)
def mock_redis():
    """Mock Redis for tests that need it."""

    # Create fake Redis (synchronous)
    fake_redis = fakeredis.FakeRedis(decode_responses=True)

    def mock_redis_factory(*args, **kwargs):
        return fake_redis

    with (
        patch("redis.Redis", side_effect=mock_redis_factory),
        patch("redis.asyncio.Redis", side_effect=mock_redis_factory),
    ):
        yield fake_redis


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
            api_key="DOLIBARR_API_KEY_A_SECRET",
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
            api_key="DOLIBARR_API_KEY_B_SECRET",
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
        rights={"thirdparty": {"read": 1, "create": 1}},
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


def make_mock_telegram_client():
    """Create a mock TelegramClient."""
    mock = AsyncMock()
    mock.send_message = AsyncMock(return_value={"message_id": 1, "chat_id": 123, "date": 0, "text": "ok"})
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


def make_valid_webhook_payload(update_id: int, text: str, chat_id: int = 123, user_id: int = 123456):
    """Create a valid Telegram webhook payload."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "from": {"id": user_id, "is_bot": False, "first_name": "Test", "username": "testuser"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1234567890,
            "text": text,
        },
    }


def make_webhook_secret_header(secret: str) -> dict:
    """Create webhook secret header."""
    return {"X-Telegram-Bot-Api-Secret-Token": secret}


# =========================================================================
# TESTS: Secret Non-Exposure
# =========================================================================


class TestSecretNonExposure:
    """Tests que NO se filtran secretos en respuestas administrativas."""

    def test_admin_list_instances_no_api_keys(self, instance_a_config, instance_b_config):
        """Admin /admin/instances NO expone api_key, bot_token, webhook_secret."""
        from unittest.mock import patch

        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config
        _config_cache["empresa_b"] = instance_b_config

        # Mock list_instances to return our test instances
        with patch("core.hermes.main.list_instances", return_value=["empresa_a", "empresa_b"]):
            client = TestClient(app)
            response = client.get("/admin/instances", headers={"Authorization": "Bearer gsk_admin_test"})

        assert response.status_code == 200
        data = response.json()

        # Verify no secrets in response
        response_text = response.text
        assert "DOLIBARR_API_KEY_A_SECRET" not in response_text
        assert "DOLIBARR_API_KEY_B_SECRET" not in response_text
        assert "telegram_token_a" not in response_text
        assert "telegram_token_b" not in response_text
        assert "secret_a" not in response_text
        assert "secret_b" not in response_text

        # Verify configured flags exist
        instances = data["instances"]
        assert len(instances) == 2
        for inst in instances:
            assert "dolibarr_api_key_configured" in inst
            assert "telegram_webhook_secret_configured" in inst
            assert inst["dolibarr_api_key_configured"] is True
            assert inst["telegram_webhook_secret_configured"] is True

    def test_admin_get_instance_no_secrets(self, instance_a_config):
        """Admin /admin/instances/{id} NO expone secretos reales."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        response = client.get("/admin/instances/empresa_a", headers={"Authorization": "Bearer gsk_admin_test"})

        assert response.status_code == 200
        data = response.json()

        response_text = response.text
        # Verify no real secrets
        assert "DOLIBARR_API_KEY_A_SECRET" not in response_text
        assert "telegram_token_a" not in response_text
        assert "secret_a" not in response_text

        # Verify configured flags
        assert data["dolibarr"]["api_key_configured"] is True
        assert data["telegram"]["webhook_secret_configured"] is True

    def test_admin_endpoints_require_admin_auth(self, instance_a_config):
        """Admin endpoints rechazan requests sin auth admin."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)

        # Sin auth
        response = client.get("/admin/instances")
        assert response.status_code == 403

        # Con auth regular (no admin)
        response = client.get("/admin/instances", headers={"Authorization": "Bearer gsk_regular_token"})
        assert response.status_code == 403

        # Con auth admin
        response = client.get("/admin/instances", headers={"Authorization": "Bearer gsk_admin_test"})
        assert response.status_code == 200


# =========================================================================
# TESTS: Webhook Secret Validation
# =========================================================================


class TestWebhookSecretValidation:
    """Tests de validación de webhook secret."""

    def test_invalid_secret_returns_403(self, instance_a_config):
        """Secret inválido → 403, IdentityResolver NO ejecutado."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        payload = make_valid_webhook_payload(1, "/terceros")
        headers = make_webhook_secret_header("wrong_secret")

        response = client.post("/webhook/empresa_a", json=payload, headers=headers)

        assert response.status_code == 403
        assert "Invalid webhook secret token" in response.json()["error"]["message"]

    def test_missing_secret_returns_403(self, instance_a_config):
        """Secret faltante → 403."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        payload = make_valid_webhook_payload(1, "/terceros")

        response = client.post("/webhook/empresa_a", json=payload)

        assert response.status_code == 403
        assert "Missing webhook secret token" in response.json()["error"]["message"]

    def test_valid_secret_allows_processing(self, instance_a_config, telegram_identity_a, dolibarr_user_a):
        """Secret válido permite procesar el webhook."""
        from core.hermes.identity_store import IdentityStore
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_dolibarr = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[DolibarrGroup(id=5, name="Comercial", entity=1)],
                list_thirdparties_return_value=[],
            )

            mock_telegram = make_mock_telegram_client()

            with (
                patch("core.hermes.main._get_telegram_client", return_value=mock_telegram),
                patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_dolibarr),
                patch("core.hermes.resolver.IdentityStore", return_value=store),
            ):
                client = TestClient(app)
                payload = make_valid_webhook_payload(1, "/terceros")
                headers = make_webhook_secret_header("secret_a")

                response = client.post("/webhook/empresa_a", json=payload, headers=headers)

                assert response.status_code == 200
                assert response.json()["success"] is True


# =========================================================================
# TESTS: Happy Path
# =========================================================================


class TestHappyPath:
    """Tests del happy path completo."""

    def test_terceros_command_returns_formatted_response(self, instance_a_config, telegram_identity_a, dolibarr_user_a):
        """Comando /terceros → respuesta formateada con terceros."""
        from core.hermes.identity_store import IdentityStore
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            # Create a mock user_context directly
            from core.hermes.identity import UserContext

            mock_user_context = UserContext(
                instance_id="empresa_a",
                telegram_user_id=123456,
                dolibarr_user_id=17,
                dolibarr_user=dolibarr_user_a,
                dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
                dolibarr_permissions={"thirdparty": {"read": 1}},
                gestor_roles=frozenset(),
            )

            mock_dolibarr = make_mock_dolibarr_client(
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
                ],
            )

            mock_telegram = make_mock_telegram_client()

            with (
                patch("core.hermes.main._get_telegram_client", return_value=mock_telegram),
                patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_dolibarr),
                patch("core.hermes.main.get_user_context", return_value=mock_user_context),
            ):
                client = TestClient(app)
                payload = make_valid_webhook_payload(100, "/terceros")
                headers = make_webhook_secret_header("secret_a")

                response = client.post("/webhook/empresa_a", json=payload, headers=headers)

                assert response.status_code == 200
                assert response.json()["success"] is True

                # Verify Telegram send_message was called with formatted response
                mock_telegram.send_message.assert_called()
                call_args = mock_telegram.send_message.call_args
                assert "Cliente Uno" in call_args.kwargs["text"]
                assert "Proveedor Dos" in call_args.kwargs["text"]


# =========================================================================
# TESTS: Permission Denied
# =========================================================================


class TestPermissionDenied:
    """Tests de autorización denegada."""

    def test_user_without_permission_denied(self, instance_a_config, telegram_identity_a, dolibarr_user_no_perms):
        """Usuario válido SIN thirdparty.read → denegado, Dolibarr NO llamado."""
        from core.hermes.identity import UserContext
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        # Create a mock user_context WITHOUT thirdparty.read permission
        mock_user_context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=dolibarr_user_no_perms,
            dolibarr_groups=[],
            dolibarr_permissions={},  # No permissions
            gestor_roles=frozenset(),
        )

        mock_dolibarr = make_mock_dolibarr_client(
            user_return_value=dolibarr_user_no_perms,
            groups_return_value=[],
            list_thirdparties_return_value=[],
        )

        mock_telegram = make_mock_telegram_client()

        with (
            patch("core.hermes.main._get_telegram_client", return_value=mock_telegram),
            patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_dolibarr),
            patch("core.hermes.main.get_user_context", return_value=mock_user_context),
        ):
            client = TestClient(app)
            payload = make_valid_webhook_payload(200, "/terceros")
            headers = make_webhook_secret_header("secret_a")

            response = client.post("/webhook/empresa_a", json=payload, headers=headers)

            assert response.status_code == 200  # Webhook returns 200, but Telegram message is denial
            mock_telegram.send_message.assert_called()
            call_args = mock_telegram.send_message.call_args
            assert "permiso" in call_args.kwargs["text"].lower() or "acceso" in call_args.kwargs["text"].lower()

            # Dolibarr list_thirdparties NOT called
            mock_dolibarr.list_thirdparties.assert_not_called()


# =========================================================================
# TESTS: Cross-Instance Isolation
# =========================================================================


class TestCrossInstanceIsolation:
    """Tests CRÍTICOS de aislamiento entre instancias."""

    def test_webhook_a_only_uses_dolibarr_a(
        self, instance_a_config, instance_b_config, telegram_identity_a, dolibarr_user_a
    ):
        """Webhook Empresa A → solo Dolibarr A, NUNCA Dolibarr B."""
        from core.hermes.identity import UserContext
        from core.hermes.instance_config import _config_cache
        from core.integrations.dolibarr.client import DolibarrClient

        _config_cache["empresa_a"] = instance_a_config
        _config_cache["empresa_b"] = instance_b_config

        with tempfile.TemporaryDirectory() as tmpdir:
            from core.hermes.identity_store import IdentityStore

            store_a = IdentityStore("empresa_a", Path(tmpdir))
            store_a.create(telegram_identity_a)

            # Create mock user_context
            mock_user_context = UserContext(
                instance_id="empresa_a",
                telegram_user_id=123456,
                dolibarr_user_id=17,
                dolibarr_user=dolibarr_user_a,
                dolibarr_groups=[],
                dolibarr_permissions={"thirdparty": {"read": 1}},
                gestor_roles=frozenset(),
            )

            # Track which DolibarrClient is created
            created_clients = []

            original_init = DolibarrClient.__init__

            def tracking_init(self, base_url, api_key, timeout=30):
                created_clients.append({"base_url": base_url, "api_key": api_key})
                original_init(self, base_url, api_key, timeout)

            mock_telegram = make_mock_telegram_client()

            with (
                patch("core.hermes.main._get_telegram_client", return_value=mock_telegram),
                patch.object(DolibarrClient, "__init__", tracking_init),
                patch("core.hermes.main.get_user_context", return_value=mock_user_context),
            ):
                client = TestClient(app)
                payload = make_valid_webhook_payload(300, "/terceros")
                headers = make_webhook_secret_header("secret_a")

                response = client.post("/webhook/empresa_a", json=payload, headers=headers)

                assert response.status_code == 200
                # Verify DolibarrClient was created with empresa_a config
                assert len(created_clients) == 1
                assert created_clients[0]["base_url"] == "http://127.0.0.1:8081"
                assert created_clients[0]["api_key"] == "DOLIBARR_API_KEY_A_SECRET"


# =========================================================================
# TESTS: Idempotency
# =========================================================================


class TestIdempotency:
    """Tests de idempotencia webhook via Redis."""

    def test_duplicate_update_id_executed_once(self, instance_a_config, telegram_identity_a, dolibarr_user_a):
        """Mismo update_id dos veces → Tool ejecutada 1 vez."""
        from core.hermes.identity import UserContext
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        # Create mock user_context
        mock_user_context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=dolibarr_user_a,
            dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
            dolibarr_permissions={"thirdparty": {"read": 1}},
            gestor_roles=frozenset(),
        )

        call_count = {"list_thirdparties": 0}

        def counting_mock_dolibarr():
            mock = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[DolibarrGroup(id=5, name="Comercial", entity=1)],
                list_thirdparties_return_value=[{"id": 1, "name": "Test", "client": 1, "fournisseur": 0, "status": 1}],
            )
            original_list = mock.list_thirdparties

            async def counting_list(*args, **kwargs):
                call_count["list_thirdparties"] += 1
                return await original_list(*args, **kwargs)

            mock.list_thirdparties = counting_list
            return mock

        mock_telegram = make_mock_telegram_client()

        with (
            patch("core.hermes.main._get_telegram_client", return_value=mock_telegram),
            patch("core.hermes.context.CompanyContext.create_dolibarr_client", side_effect=counting_mock_dolibarr),
            patch("core.hermes.main.get_user_context", return_value=mock_user_context),
        ):
            client = TestClient(app)
            payload = make_valid_webhook_payload(400, "/terceros")
            headers = make_webhook_secret_header("secret_a")

            # First request
            response1 = client.post("/webhook/empresa_a", json=payload, headers=headers)
            assert response1.status_code == 200

            # Second request with SAME update_id
            response2 = client.post("/webhook/empresa_a", json=payload, headers=headers)
            assert response2.status_code == 200
            assert response2.json().get("duplicate") is True

            # Tool should only be called ONCE
            assert call_count["list_thirdparties"] == 1


# =========================================================================
# TESTS: Dolibarr Errors - No Internal Leaks
# =========================================================================


class TestDolibarrErrorsNoLeaks:
    """Tests de errores de Dolibarr - respuesta segura."""

    def test_dolibarr_timeout_returns_safe_message(self, instance_a_config, telegram_identity_a, dolibarr_user_a):
        """Dolibarr timeout → respuesta segura, NO stacktrace."""
        from core.hermes.identity import UserContext
        from core.hermes.instance_config import _config_cache
        from core.integrations.dolibarr.client import DolibarrException

        _config_cache["empresa_a"] = instance_a_config

        # Create a mock user_context directly
        mock_user_context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=dolibarr_user_a,
            dolibarr_groups=[],
            dolibarr_permissions={"thirdparty": {"read": 1}},
            gestor_roles=frozenset(),
        )

        mock_dolibarr = make_mock_dolibarr_client(
            user_return_value=dolibarr_user_a,
            groups_return_value=[],
            list_thirdparties_return_value=[],
            list_thirdparties_side_effect=DolibarrException(
                message="Timeout connecting to Dolibarr", endpoint="thirdparties", status_code=504
            ),
        )

        mock_telegram = make_mock_telegram_client()

        with (
            patch("core.hermes.main._get_telegram_client", return_value=mock_telegram),
            patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_dolibarr),
            patch("core.hermes.main.get_user_context", return_value=mock_user_context),
        ):
            client = TestClient(app)
            payload = make_valid_webhook_payload(500, "/terceros")
            headers = make_webhook_secret_header("secret_a")

            response = client.post("/webhook/empresa_a", json=payload, headers=headers)

            assert response.status_code == 200
            mock_telegram.send_message.assert_called()
            call_args = mock_telegram.send_message.call_args
            response_text = call_args.kwargs["text"]

            # Verify safe message (no internal details)
            assert "No he podido consultar Dolibarr" in response_text
            assert "Timeout" not in response_text
            assert "504" not in response_text
            assert "endpoint" not in response_text.lower()
            assert "traceback" not in response_text.lower()
            assert "exception" not in response_text.lower()

    def test_dolibarr_500_returns_safe_message(self, instance_a_config, telegram_identity_a, dolibarr_user_a):
        """Dolibarr 500 → respuesta segura."""
        from core.hermes.identity import UserContext
        from core.hermes.instance_config import _config_cache
        from core.integrations.dolibarr.client import DolibarrException

        _config_cache["empresa_a"] = instance_a_config

        # Create a mock user_context directly
        mock_user_context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=dolibarr_user_a,
            dolibarr_groups=[],
            dolibarr_permissions={"thirdparty": {"read": 1}},
            gestor_roles=frozenset(),
        )

        mock_dolibarr = make_mock_dolibarr_client(
            user_return_value=dolibarr_user_a,
            groups_return_value=[],
            list_thirdparties_return_value=[],
            list_thirdparties_side_effect=DolibarrException(
                message="Internal Server Error", endpoint="thirdparties", status_code=500
            ),
        )

        mock_telegram = make_mock_telegram_client()

        with (
            patch("core.hermes.main._get_telegram_client", return_value=mock_telegram),
            patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_dolibarr),
            patch("core.hermes.main.get_user_context", return_value=mock_user_context),
        ):
            client = TestClient(app)
            payload = make_valid_webhook_payload(501, "/terceros")
            headers = make_webhook_secret_header("secret_a")

            response = client.post("/webhook/empresa_a", json=payload, headers=headers)

            assert response.status_code == 200
            mock_telegram.send_message.assert_called()
            call_args = mock_telegram.send_message.call_args
            response_text = call_args.kwargs["text"]

            assert "No he podido consultar Dolibarr" in response_text
            assert "500" not in response_text
            assert "Internal" not in response_text

    def test_dolibarr_401_returns_safe_message_no_api_key_leak(
        self, instance_a_config, telegram_identity_a, dolibarr_user_a
    ):
        """Dolibarr 401 (auth failure) → respuesta segura, NO API key filtrada."""
        from core.hermes.identity import UserContext
        from core.hermes.instance_config import _config_cache
        from core.integrations.dolibarr.client import DolibarrException

        _config_cache["empresa_a"] = instance_a_config

        # Create a mock user_context directly
        mock_user_context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=dolibarr_user_a,
            dolibarr_groups=[],
            dolibarr_permissions={"thirdparty": {"read": 1}},
            gestor_roles=frozenset(),
        )

        mock_dolibarr = make_mock_dolibarr_client(
            user_return_value=dolibarr_user_a,
            groups_return_value=[],
            list_thirdparties_return_value=[],
            list_thirdparties_side_effect=DolibarrException(
                message="Invalid API key: DOLIBARR_API_KEY_A_SECRET", endpoint="thirdparties", status_code=401
            ),
        )

        mock_telegram = make_mock_telegram_client()

        with (
            patch("core.hermes.main._get_telegram_client", return_value=mock_telegram),
            patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_dolibarr),
            patch("core.hermes.main.get_user_context", return_value=mock_user_context),
        ):
            client = TestClient(app)
            payload = make_valid_webhook_payload(502, "/terceros")
            headers = make_webhook_secret_header("secret_a")

            response = client.post("/webhook/empresa_a", json=payload, headers=headers)

            assert response.status_code == 200
            mock_telegram.send_message.assert_called()
            call_args = mock_telegram.send_message.call_args
            response_text = call_args.kwargs["text"]

            assert "No he podido consultar Dolibarr" in response_text
            # Verify NO API key leaked
            assert "DOLIBARR_API_KEY_A_SECRET" not in response_text
            assert "API key" not in response_text
            assert "api_key" not in response_text.lower()


# =========================================================================
# TESTS: API Endpoints Authentication
# =========================================================================


class TestAPIEndpointsAuthentication:
    """Tests de autenticación en endpoints /api/*."""

    def test_api_thirdparties_requires_auth(self, instance_a_config):
        """GET /api/{id}/dolibarr/thirdparties requiere autenticación."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        response = client.get("/api/empresa_a/dolibarr/thirdparties", headers={"X-Instance-ID": "empresa_a"})

        assert response.status_code == 401

    def test_api_ai_requires_auth(self, instance_a_config):
        """POST /api/{id}/ai/generate requiere autenticación."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        body = {"prompt": "test", "task": "general"}
        headers = {"X-Instance-ID": "empresa_a"}
        response = client.post("/api/empresa_a/ai/generate", json=body, headers=headers)

        assert response.status_code == 401

    def test_api_audit_requires_auth(self, instance_a_config):
        """GET /api/{id}/audit requiere autenticación."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        response = client.get("/api/empresa_a/audit", headers={"X-Instance-ID": "empresa_a"})

        assert response.status_code == 401

    def test_api_extensions_requires_auth(self, instance_a_config):
        """GET /api/{id}/extensions requiere autenticación."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        response = client.get("/api/empresa_a/extensions", headers={"X-Instance-ID": "empresa_a"})

        assert response.status_code == 401


# =========================================================================
# TESTS: CORS and Docs Configuration
# =========================================================================


class TestCORSAndDocs:
    """Tests de configuración CORS y docs por entorno."""

    def test_dev_environment_has_docs(self):
        """En desarrollo, /docs está disponible."""
        # This test runs in current environment (development)
        client = TestClient(app)
        response = client.get("/docs")
        # In development, docs should be available (200 or redirect)
        assert response.status_code in (200, 307)

    def test_dev_environment_has_openapi(self):
        """En desarrollo, /openapi.json está disponible."""
        client = TestClient(app)
        response = client.get("/openapi.json")
        assert response.status_code in (200, 307)


# =========================================================================
# TESTS: ToolRegistry Cross-Instance Validation
# =========================================================================


class TestToolRegistryCrossInstance:
    """Tests de validación cross-instance en ToolRegistry."""

    @pytest.mark.asyncio
    async def test_cross_instance_company_context_mismatch_rejected(
        self, instance_a_config, instance_b_config, telegram_identity_a, dolibarr_user_a
    ):
        """instance_id=A, CompanyContext=B → CROSS_INSTANCE_ERROR."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools import tool_registry

        tool_registry.clear_all()
        register_core_thirdparty_tools()

        with tempfile.TemporaryDirectory() as tmpdir:
            store = IdentityStore("empresa_a", Path(tmpdir))
            store.create(telegram_identity_a)

            mock_dolibarr = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            def mock_factory(ctx):
                return mock_dolibarr

            resolver = IdentityResolver(store, mock_factory)

            ctx_a = CompanyContext(instance_config=instance_a_config, actor_type="telegram_user", actor_id="123456")
            user_context = await resolver.resolve(ctx_a, 123456)

            # Create context B but use instance_id A
            ctx_b = CompanyContext(instance_config=instance_b_config, actor_type="telegram_user", actor_id="123456")

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_dolibarr):
                result = await tool_registry.execute_tool(
                    instance_id="empresa_a",  # instance_id = A
                    name="list_thirdparties",
                    company_context=ctx_b,  # CompanyContext = B
                    user_context=user_context,  # user_context = A
                    limit=10,
                    offset=0,
                )

            assert result.success is False
            assert result.error_code == "CROSS_INSTANCE_ERROR"
            assert "company_context" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_cross_instance_user_context_mismatch_rejected(
        self,
        instance_a_config,
        instance_b_config,
        telegram_identity_a,
        telegram_identity_b,
        dolibarr_user_a,
        dolibarr_user_b,
    ):
        """instance_id=A, UserContext=B → CROSS_INSTANCE_ERROR."""
        import tempfile
        from pathlib import Path

        from core.hermes.identity_resolver import IdentityResolver
        from core.hermes.identity_store import IdentityStore
        from core.hermes.tools import tool_registry

        tool_registry.clear_all()
        register_core_thirdparty_tools()

        with tempfile.TemporaryDirectory() as tmpdir:
            store_a = IdentityStore("empresa_a", Path(tmpdir))
            store_b = IdentityStore("empresa_b", Path(tmpdir))
            store_a.create(telegram_identity_a)
            store_b.create(telegram_identity_b)

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

            resolver_a = IdentityResolver(store_a, mock_factory)
            resolver_b = IdentityResolver(store_b, mock_factory)

            ctx_a = CompanyContext(instance_config=instance_a_config, actor_type="telegram_user", actor_id="123456")
            ctx_b = CompanyContext(instance_config=instance_b_config, actor_type="telegram_user", actor_id="123456")

            _ = await resolver_a.resolve(ctx_a, 123456)
            user_context_b = await resolver_b.resolve(ctx_b, 123456)

            mock_dolibarr = make_mock_dolibarr_client(
                user_return_value=dolibarr_user_a,
                groups_return_value=[],
                list_thirdparties_return_value=[],
            )

            with patch("core.hermes.context.CompanyContext.create_dolibarr_client", return_value=mock_dolibarr):
                # Use instance_id A, CompanyContext A, but user_context B
                result = await tool_registry.execute_tool(
                    instance_id="empresa_a",
                    name="list_thirdparties",
                    company_context=ctx_a,
                    user_context=user_context_b,  # Wrong instance!
                    limit=10,
                    offset=0,
                )

            assert result.success is False
            assert result.error_code == "CROSS_INSTANCE_ERROR"
            assert "user_context" in result.error_message.lower()


# =========================================================================
# TESTS: Sort Field Allowlist
# =========================================================================


class TestSortFieldAllowlist:
    """Tests de allowlist para sort_field."""

    def test_valid_sort_field_accepted(self, instance_a_config, telegram_identity_a, dolibarr_user_a):
        """sort_field válido → OK."""
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesParams

        params = ListThirdpartiesParams(sort_field="name", sort_order="ASC")
        assert params.sort_field == "name"

        params = ListThirdpartiesParams(sort_field="date_creation", sort_order="DESC")
        assert params.sort_field == "date_creation"

    def test_invalid_sort_field_rejected(self, instance_a_config):
        """sort_field inválido → validation error."""
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesParams

        with pytest.raises(ValueError) as exc_info:
            ListThirdpartiesParams(sort_field="invalid_field", sort_order="ASC")

        assert "no permitido" in str(exc_info.value)
        assert "invalid_field" in str(exc_info.value)

    def test_invalid_sort_order_rejected(self, instance_a_config):
        """sort_order inválido → validation error."""
        from core.hermes.tools.thirdparty_tools import ListThirdpartiesParams

        with pytest.raises(ValueError) as exc_info:
            ListThirdpartiesParams(sort_field="name", sort_order="INVALID")

        assert "no permitido" in str(exc_info.value)
        assert "INVALID" in str(exc_info.value)

    def test_tool_schema_includes_enum(self):
        """ToolDefinition.parameters_schema incluye enum para sort_field y sort_order."""
        from core.hermes.tools.thirdparty_tools import (
            ALLOWED_SORT_ORDERS,
            ALLOWED_THIRDPARTY_SORT_FIELDS,
            ListThirdpartiesTool,
        )

        tool = ListThirdpartiesTool()
        schema = tool.definition.parameters_schema

        sort_field_prop = schema["properties"]["sort_field"]
        assert "enum" in sort_field_prop
        assert set(sort_field_prop["enum"]) == ALLOWED_THIRDPARTY_SORT_FIELDS

        sort_order_prop = schema["properties"]["sort_order"]
        assert "enum" in sort_order_prop
        assert set(sort_order_prop["enum"]) == ALLOWED_SORT_ORDERS


# =========================================================================
# TESTS: Endpoint Classification
# =========================================================================


class TestEndpointClassification:
    """Tests de clasificación explícita de endpoints."""

    def test_health_endpoints_public(self):
        """Health endpoints son públicos."""
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200

        response = client.get("/health/ready")
        assert response.status_code == 200

    def test_webhook_endpoint_public_but_validated(self, instance_a_config):
        """Webhook es público pero requiere secret token."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        # Sin secret → 403 (no 401)
        payload = make_valid_webhook_payload(1, "/start")
        response = client.post("/webhook/empresa_a", json=payload)
        assert response.status_code == 403

    def test_admin_endpoints_require_admin(self, instance_a_config):
        """Admin endpoints requieren auth admin."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        # Sin auth
        response = client.get("/admin/instances")
        assert response.status_code == 403

        # Con auth admin
        response = client.get("/admin/instances", headers={"Authorization": "Bearer gsk_admin_test"})
        assert response.status_code == 200

    def test_api_endpoints_require_user_auth(self, instance_a_config):
        """API endpoints requieren UserContext autenticado."""
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config

        client = TestClient(app)
        # Sin auth
        endpoints = [
            ("GET", "/api/empresa_a/dolibarr/thirdparties"),
            ("POST", "/api/empresa_a/ai/generate"),
            ("GET", "/api/empresa_a/audit"),
            ("GET", "/api/empresa_a/extensions"),
        ]
        for method, endpoint in endpoints:
            if method == "GET":
                response = client.get(endpoint, headers={"X-Instance-ID": "empresa_a"})
            else:
                body = {"prompt": "test", "task": "general"}
                response = client.post(endpoint, json=body, headers={"X-Instance-ID": "empresa_a"})
            assert response.status_code == 401, f"Endpoint {method} {endpoint} should require auth"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
