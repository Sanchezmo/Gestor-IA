"""
Tests for IdentityResolver.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.hermes.context import CompanyContext
from core.hermes.identity import DolibarrGroup, DolibarrUser, TelegramIdentity, UserContext
from core.hermes.identity_resolver import (
    DolibarrConnectionError,
    DolibarrUserDisabledError,
    DolibarrUserNotFoundError,
    IdentityDisabledError,
    IdentityNotFoundError,
    IdentityResolver,
)
from core.hermes.identity_store import IdentityStore
from core.hermes.instance_config import (
    AIConfig,
    DatabaseConfig,
    DolibarrConfig,
    DomainConfig,
    InstanceConfig,
    TelegramConfig,
)
from core.integrations.dolibarr.client import DolibarrClient, DolibarrException


class TestIdentityResolver:
    """Tests for IdentityResolver."""

    @pytest.fixture
    def instance_config(self):
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
            ),
            domains=DomainConfig(base="empresa-a.com"),
            ai=AIConfig(ollama_model="test-model"),
        ).resolve_paths()

    @pytest.fixture
    def company_context(self, instance_config):
        return CompanyContext(
            instance_config=instance_config,
            actor_type="telegram_user",
            actor_id="123456",
        )

    @pytest.fixture
    def temp_instances_root(self, tmp_path):
        return tmp_path

    @pytest.fixture
    def identity_store(self, temp_instances_root):
        return IdentityStore("empresa_a", temp_instances_root)

    @pytest.fixture
    def dolibarr_client_factory(self):
        def factory(ctx: CompanyContext, identity) -> DolibarrClient:
            return DolibarrClient.from_instance_config(ctx.dolibarr_config, identity.dolibarr_api_key)

        return factory

    @pytest.fixture
    def resolver(self, identity_store, dolibarr_client_factory):
        return IdentityResolver(identity_store, dolibarr_client_factory)

    @pytest.fixture
    def telegram_identity(self):
        return TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )

    @pytest.fixture
    def dolibarr_user(self):
        return DolibarrUser(
            id=17,
            login="juan.perez",
            firstname="Juan",
            lastname="Perez",
            email="juan@empresa.com",
            active=True,
            entity=1,
            rights={
                "thirdparty": {"read": 1},
            },
            user_group_list=[DolibarrGroup(id=5, name="Comercial", entity=1)],
        )

    @pytest.fixture
    def dolibarr_groups(self):
        return [
            DolibarrGroup(id=5, name="Comercial", entity=1, rights={"thirdparty": {"write": 1}}),
        ]

    @pytest.mark.asyncio
    async def test_resolve_success(
        self, resolver, company_context, identity_store, telegram_identity, dolibarr_user, dolibarr_groups
    ):
        # Setup: create identity in store
        identity_store.create(telegram_identity)

        # Mock DolibarrClient
        mock_client = AsyncMock(spec=DolibarrClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_user = AsyncMock(return_value=dolibarr_user)
        mock_client.get_user_groups = AsyncMock(return_value=dolibarr_groups)

        # Patch factory to return mock client (takes company_context and identity)
        def mock_factory(ctx, identity):
            return mock_client

        resolver = IdentityResolver(identity_store, mock_factory)

        # Resolve
        user_context = await resolver.resolve(company_context, 123456)

        assert isinstance(user_context, UserContext)
        assert user_context.instance_id == "empresa_a"
        assert user_context.telegram_user_id == 123456
        assert user_context.dolibarr_user_id == 17
        assert user_context.dolibarr_user.login == "juan.perez"
        assert user_context.has_permission("thirdparty.read")
        assert user_context.has_permission("thirdparty.write")  # from group

    @pytest.mark.asyncio
    async def test_resolve_identity_not_found(self, resolver, company_context):
        with pytest.raises(IdentityNotFoundError) as exc_info:
            await resolver.resolve(company_context, 999999)

        assert exc_info.value.instance_id == "empresa_a"
        assert exc_info.value.telegram_user_id == 999999

    @pytest.mark.asyncio
    async def test_resolve_identity_disabled(self, resolver, company_context, identity_store, telegram_identity):
        disabled_identity = telegram_identity.with_enabled(False)
        identity_store.create(disabled_identity)

        with pytest.raises(IdentityDisabledError) as exc_info:
            await resolver.resolve(company_context, 123456)

        assert exc_info.value.instance_id == "empresa_a"
        assert exc_info.value.telegram_user_id == 123456

    @pytest.mark.asyncio
    async def test_resolve_dolibarr_user_not_found(self, resolver, company_context, identity_store, telegram_identity):
        identity_store.create(telegram_identity)

        mock_client = AsyncMock(spec=DolibarrClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_user = AsyncMock(
            side_effect=DolibarrException(message="User not found", endpoint="users/17", status_code=404)
        )

        def mock_factory(ctx, identity):
            return mock_client

        resolver = IdentityResolver(identity_store, mock_factory)

        with pytest.raises(DolibarrUserNotFoundError) as exc_info:
            await resolver.resolve(company_context, 123456)

        assert exc_info.value.dolibarr_user_id == 17

    @pytest.mark.asyncio
    async def test_resolve_dolibarr_user_disabled(self, resolver, company_context, identity_store, telegram_identity):
        identity_store.create(telegram_identity)

        inactive_user = DolibarrUser(
            id=17,
            login="juan.perez",
            firstname="Juan",
            lastname="Perez",
            email="juan@empresa.com",
            active=False,  # INACTIVE
            entity=1,
        )

        mock_client = AsyncMock(spec=DolibarrClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_user = AsyncMock(return_value=inactive_user)

        def mock_factory(ctx, identity):
            return mock_client

        resolver = IdentityResolver(identity_store, mock_factory)

        with pytest.raises(DolibarrUserDisabledError) as exc_info:
            await resolver.resolve(company_context, 123456)

        assert exc_info.value.dolibarr_user_id == 17

    @pytest.mark.asyncio
    async def test_resolve_dolibarr_connection_error(
        self, resolver, company_context, identity_store, telegram_identity
    ):
        identity_store.create(telegram_identity)

        mock_client = AsyncMock(spec=DolibarrClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_user = AsyncMock(
            side_effect=DolibarrException(message="Timeout", endpoint="users/17", status_code=504)
        )

        def mock_factory(ctx, identity):
            return mock_client

        resolver = IdentityResolver(identity_store, mock_factory)

        with pytest.raises(DolibarrConnectionError) as exc_info:
            await resolver.resolve(company_context, 123456)

        assert exc_info.value.instance_id == "empresa_a"

    @pytest.mark.asyncio
    async def test_resolve_updates_last_seen(
        self, resolver, company_context, identity_store, telegram_identity, dolibarr_user, dolibarr_groups
    ):
        identity_store.create(telegram_identity)

        mock_client = AsyncMock(spec=DolibarrClient)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get_user = AsyncMock(return_value=dolibarr_user)
        mock_client.get_user_groups = AsyncMock(return_value=dolibarr_groups)

        def mock_factory(ctx, identity):
            return mock_client

        resolver = IdentityResolver(identity_store, mock_factory)

        await resolver.resolve(company_context, 123456)

        # Verify last_seen was updated
        updated = identity_store.get(123456)
        assert updated.last_seen_at is not None


class TestIdentityResolverCrossInstance:
    """Tests for cross-instance isolation in IdentityResolver."""

    @pytest.fixture
    def temp_instances_root(self, tmp_path):
        return tmp_path

    @pytest.mark.asyncio
    async def test_same_telegram_id_different_instances(self, temp_instances_root):
        """Same telegram_user_id in different instances resolves to different Dolibarr users."""
        # Create stores for two instances
        store_a = IdentityStore("empresa_a", temp_instances_root)
        store_b = IdentityStore("empresa_b", temp_instances_root)

        # Create identities: same telegram_user_id, different dolibarr_user_id
        identity_a = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        identity_b = TelegramIdentity(
            instance_id="empresa_b",
            telegram_user_id=123456,  # Same Telegram ID
            dolibarr_user_id=8,  # Different Dolibarr user
        )

        store_a.create(identity_a)
        store_b.create(identity_b)

        # Create company configs
        config_a = InstanceConfig(
            instance_id="empresa_a",
            company_name="Empresa A",
            database=DatabaseConfig(host="127.0.0.1", port=3306, name="dolibarr_a", user="db_a", password="pass"),
            dolibarr=DolibarrConfig(
                version="23.0.4",
                internal_url="http://localhost:8081",
                api_key="key_a",
                documents_path="/docs",
            ),
            telegram=TelegramConfig(bot_token="token_a", webhook_path="/webhook/a", webhook_secret="secret_a"),
            domains=DomainConfig(base="a.com"),
            ai=AIConfig(ollama_model="test-model"),
        ).resolve_paths()

        config_b = InstanceConfig(
            instance_id="empresa_b",
            company_name="Empresa B",
            database=DatabaseConfig(host="127.0.0.1", port=3306, name="dolibarr_b", user="db_b", password="pass"),
            dolibarr=DolibarrConfig(
                version="23.0.4",
                internal_url="http://localhost:8082",
                api_key="key_b",
                documents_path="/docs",
            ),
            telegram=TelegramConfig(bot_token="token_b", webhook_path="/webhook/b", webhook_secret="secret_b"),
            domains=DomainConfig(base="b.com"),
            ai=AIConfig(ollama_model="test-model"),
        ).resolve_paths()

        ctx_a = CompanyContext(instance_config=config_a, actor_type="telegram_user", actor_id="123456")
        ctx_b = CompanyContext(instance_config=config_b, actor_type="telegram_user", actor_id="123456")

        # Mock clients
        user_a = DolibarrUser(
            id=17,
            login="user_a",
            firstname="User",
            lastname="A",
            email="a@a.com",
            active=True,
            entity=1,
        )
        user_b = DolibarrUser(
            id=8,
            login="user_b",
            firstname="User",
            lastname="B",
            email="b@b.com",
            active=True,
            entity=1,
        )

        def mock_factory(ctx, identity):
            mock = AsyncMock(spec=DolibarrClient)
            # Setup async context manager protocol
            mock.__aenter__ = AsyncMock(return_value=mock)
            mock.__aexit__ = AsyncMock(return_value=None)
            if ctx.instance_id == "empresa_a":
                mock.get_user = AsyncMock(return_value=user_a)
                mock.get_user_groups = AsyncMock(return_value=[])
            else:
                mock.get_user = AsyncMock(return_value=user_b)
                mock.get_user_groups = AsyncMock(return_value=[])
            return mock

        resolver = IdentityResolver(store_a, mock_factory)

        # Resolve for instance A
        context_a = await resolver.resolve(ctx_a, 123456)
        assert context_a.dolibarr_user_id == 17

        # Resolve for instance B (need separate resolver with store_b)
        resolver_b = IdentityResolver(store_b, mock_factory)
        context_b = await resolver_b.resolve(ctx_b, 123456)
        assert context_b.dolibarr_user_id == 8

        # Verify isolation: A's identity not in B's store
        assert store_a.get(123456).dolibarr_user_id == 17
        assert store_b.get(123456).dolibarr_user_id == 8


class TestIdentityResolverExceptions:
    """Tests for exception hierarchy and attributes."""

    def test_identity_not_found_error_attributes(self):
        error = IdentityNotFoundError("empresa_a", 123456)
        assert error.instance_id == "empresa_a"
        assert error.telegram_user_id == 123456
        assert "123456" in str(error)
        assert "empresa_a" in str(error)

    def test_identity_disabled_error_attributes(self):
        error = IdentityDisabledError("empresa_a", 123456)
        assert error.instance_id == "empresa_a"
        assert error.telegram_user_id == 123456

    def test_dolibarr_user_not_found_error_attributes(self):
        error = DolibarrUserNotFoundError("empresa_a", 123456, 17)
        assert error.instance_id == "empresa_a"
        assert error.telegram_user_id == 123456
        assert error.dolibarr_user_id == 17

    def test_dolibarr_user_disabled_error_attributes(self):
        error = DolibarrUserDisabledError("empresa_a", 123456, 17)
        assert error.dolibarr_user_id == 17

    def test_dolibarr_connection_error_attributes(self):
        original = Exception("timeout")
        error = DolibarrConnectionError("empresa_a", 123456, original)
        assert error.original_error is original
        assert "timeout" in str(error)
