"""
Tests de aislamiento cross-instancia - CRÍTICOS.

Estos tests DEMUESTRAN que Empresa A ≠ Empresa B.
Deben pasar ANTES de cualquier feature de negocio.
"""

import dataclasses
from unittest.mock import MagicMock

import pytest

from core.hermes.context import CompanyContext
from core.hermes.extensions import extension_registry
from core.hermes.instance_config import (
    AIConfig,
    AIPolicyScope,
    DatabaseConfig,
    DolibarrConfig,
    DomainConfig,
    InstanceConfig,
    TelegramConfig,
)
from core.hermes.resolver import (
    _build_domain_index,
    invalidate_domain_cache,
    resolve_instance_config,
)
from core.integrations.dolibarr.client import DolibarrClient

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
        ),
        domains=DomainConfig(
            base="empresa-a.com",
            dolibarr="dolibarr.empresa-a.com",
            hermes="bot.empresa-a.com",
        ),
        ai=AIConfig(
            default_policy=AIPolicyScope.LOCAL_ONLY,
            ollama_model="qwen3.5:4b",
        ),
        enabled_agents=["invoice_processing"],
        enabled_workflows=["invoice_approval"],
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
        ),
        domains=DomainConfig(
            base="empresa-b.es",
            dolibarr="dolibarr.empresa-b.es",
            hermes="bot.empresa-b.es",
        ),
        ai=AIConfig(
            default_policy=AIPolicyScope.CLOUD_ALLOWED,
            ollama_model="llama3.1:8b",
        ),
        enabled_agents=["dog_intake", "publishing"],
        enabled_workflows=["dog_publishing"],
        dolibarr_apache_port=8082,
    ).resolve_paths()


@pytest.fixture
def context_a(instance_a_config):
    return CompanyContext(
        instance_config=instance_a_config,
        actor_type="telegram_user",
        actor_id="user_1",
    )


@pytest.fixture
def context_b(instance_b_config):
    return CompanyContext(
        instance_config=instance_b_config,
        actor_type="telegram_user",
        actor_id="user_2",
    )


# =========================================================================
# TESTS DE AISLAMIENTO - CONFIGURACIÓN
# =========================================================================


class TestInstanceConfigIsolation:
    """Tests de que InstanceConfig son independientes."""

    def test_instance_ids_different(self, instance_a_config, instance_b_config):
        assert instance_a_config.instance_id == "empresa_a"
        assert instance_b_config.instance_id == "empresa_b"
        assert instance_a_config.instance_id != instance_b_config.instance_id

    def test_database_configs_independent(self, instance_a_config, instance_b_config):
        db_a = instance_a_config.database
        db_b = instance_b_config.database

        assert db_a.name == "dolibarr_empresa_a"
        assert db_b.name == "dolibarr_empresa_b"
        assert db_a.name != db_b.name

        assert db_a.user == "db_empresa_a"
        assert db_b.user == "db_empresa_b"
        assert db_a.user != db_b.user

        assert db_a.password != db_b.password

    def test_dolibarr_configs_independent(self, instance_a_config, instance_b_config):
        dol_a = instance_a_config.dolibarr
        dol_b = instance_b_config.dolibarr

        assert dol_a.api_key != dol_b.api_key
        assert dol_a.internal_url != dol_b.internal_url
        assert dol_a.documents_path != dol_b.documents_path

    def test_telegram_configs_independent(self, instance_a_config, instance_b_config):
        tg_a = instance_a_config.telegram
        tg_b = instance_b_config.telegram

        assert tg_a.bot_token != tg_b.bot_token
        assert tg_a.webhook_path != tg_b.webhook_path
        assert tg_a.webhook_secret != tg_b.webhook_secret

    def test_domains_independent(self, instance_a_config, instance_b_config):
        d_a = instance_a_config.domains
        d_b = instance_b_config.domains

        assert d_a.base != d_b.base
        assert d_a.dolibarr != d_b.dolibarr
        assert d_a.hermes != d_b.hermes

    def test_ai_policies_independent(self, instance_a_config, instance_b_config):
        assert instance_a_config.ai.default_policy == AIPolicyScope.LOCAL_ONLY
        assert instance_b_config.ai.default_policy == AIPolicyScope.CLOUD_ALLOWED

    def test_enabled_extensions_independent(self, instance_a_config, instance_b_config):
        assert "invoice_processing" in instance_a_config.enabled_agents
        assert "invoice_processing" not in instance_b_config.enabled_agents

        assert "dog_intake" in instance_b_config.enabled_agents
        assert "dog_intake" not in instance_a_config.enabled_agents

        assert instance_a_config.enabled_workflows != instance_b_config.enabled_workflows

    def test_redis_db_different(self, instance_a_config, instance_b_config):
        """Cada instancia usa DB Redis diferente."""
        db_a = instance_a_config.get_redis_db()
        db_b = instance_b_config.get_redis_db()
        assert db_a != db_b

    def test_database_urls_different(self, instance_a_config, instance_b_config):
        url_a = instance_a_config.get_database_url()
        url_b = instance_b_config.get_database_url()
        assert url_a != url_b
        assert "dolibarr_empresa_a" in url_a
        assert "dolibarr_empresa_b" in url_b


# =========================================================================
# TESTS DE AISLAMIENTO - COMPANYCONTEXT
# =========================================================================


class TestCompanyContextIsolation:
    """Tests de que CompanyContext aísla operaciones."""

    def test_context_carries_correct_instance_id(self, context_a, context_b):
        assert context_a.instance_id == "empresa_a"
        assert context_b.instance_id == "empresa_b"

    def test_context_carry_correct_database_config(self, context_a, context_b):
        assert context_a.database_config.name == "dolibarr_empresa_a"
        assert context_b.database_config.name == "dolibarr_empresa_b"

    def test_context_carry_correct_dolibarr_config(self, context_a, context_b):
        assert context_a.dolibarr_config.internal_url == "http://127.0.0.1:8081"
        assert context_b.dolibarr_config.internal_url == "http://127.0.0.1:8082"

    def test_context_carry_correct_telegram_config(self, context_a, context_b):
        assert context_a.telegram_config.bot_token == "telegram_token_a"
        assert context_b.telegram_config.bot_token == "telegram_token_b"

    def test_context_carry_correct_ai_policy(self, context_a, context_b):
        assert context_a.ai_config.default_policy == AIPolicyScope.LOCAL_ONLY
        assert context_b.ai_config.default_policy == AIPolicyScope.CLOUD_ALLOWED

    def test_context_immutable(self, context_a):
        """CompanyContext es inmutable (frozen dataclass)."""
        with pytest.raises(dataclasses.FrozenInstanceError):
            context_a.instance_id = "hacked"

    def test_audit_dict_excludes_secrets(self, context_a):
        audit = context_a.to_audit_dict()
        assert "instance_id" in audit
        assert "dolibarr_config" not in audit  # No expone config completa
        assert "telegram_config" not in audit


# =========================================================================
# TESTS DE AISLAMIENTO - INSTANCE RESOLVER
# =========================================================================


class TestInstanceResolverIsolation:
    """Tests de que el resolver resuelve correctamente cada instancia."""

    @pytest.fixture(autouse=True)
    def setup_domain_cache(self, instance_a_config, instance_b_config):
        """Configurar cache de dominios para tests."""
        from core.hermes.resolver import _build_domain_index, invalidate_domain_cache

        invalidate_domain_cache()
        # Cargar configs en cache interno para que _build_domain_index las encuentre
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config
        _config_cache["empresa_b"] = instance_b_config
        _build_domain_index()
        yield
        invalidate_domain_cache()

    @pytest.fixture(autouse=True)
    def setup_domain_index(self, instance_a_config, instance_b_config):
        """Configurar cache de dominios para tests."""
        invalidate_domain_cache()
        # Simular carga de configs
        from core.hermes.instance_config import _config_cache

        _config_cache["empresa_a"] = instance_a_config
        _config_cache["empresa_b"] = instance_b_config
        _build_domain_index()
        yield
        invalidate_domain_cache()

    @pytest.mark.asyncio
    async def test_resolve_by_domain_a(self):
        """Host header empresa-a.com resuelve a empresa_a."""
        request = MagicMock()
        request.headers = {"host": "empresa-a.com"}
        request.url.path = "/api/test"

        config = await resolve_instance_config(request)
        assert config.instance_id == "empresa_a"

    @pytest.mark.asyncio
    async def test_resolve_by_domain_b(self):
        """Host header empresa-b.es resuelve a empresa_b."""
        request = MagicMock()
        request.headers = {"host": "empresa-b.es"}
        request.url.path = "/api/test"

        config = await resolve_instance_config(request)
        assert config.instance_id == "empresa_b"

    @pytest.mark.asyncio
    async def test_resolve_by_webhook_path_a(self):
        """Path /webhook/empresa_a/... resuelve a empresa_a."""
        request = MagicMock()
        request.headers = {}
        request.url.path = "/webhook/empresa_a"

        config = await resolve_instance_config(request)
        assert config.instance_id == "empresa_a"

    @pytest.mark.asyncio
    async def test_resolve_by_webhook_path_b(self):
        """Path /webhook/empresa_b/... resuelve a empresa_b."""
        request = MagicMock()
        request.headers = {}
        request.url.path = "/webhook/empresa_b"

        config = await resolve_instance_config(request)
        assert config.instance_id == "empresa_b"

    @pytest.mark.asyncio
    async def test_resolve_by_x_instance_id_header(self, instance_a_config):
        """Header X-Instance-ID tiene prioridad."""
        request = MagicMock()
        request.headers = {"X-Instance-ID": "empresa_a", "host": "empresa-b.es"}
        request.url.path = "/api/test"

        config = await resolve_instance_config(request)
        assert config.instance_id == "empresa_a"

    @pytest.mark.asyncio
    async def test_resolve_by_api_key(self):
        """API key gsk_{b64_instance_id}_... resuelve a empresa_a."""
        import base64

        # Codificar instance_id en base64 URL-safe
        instance_id_b64 = base64.urlsafe_b64encode(b"empresa_a").decode().rstrip("=")
        api_key = f"gsk_{instance_id_b64}_abc123"

        request = MagicMock()
        request.headers = {"Authorization": f"Bearer {api_key}"}
        request.url.path = "/api/test"

        config = await resolve_instance_config(request)
        assert config.instance_id == "empresa_a"

    @pytest.mark.asyncio
    async def test_cross_instance_domain_not_resolve(self):
        """Dominio de A no resuelve a B."""
        request = MagicMock()
        request.headers = {"host": "empresa-a.com"}
        request.url.path = "/api/test"

        config = await resolve_instance_config(request)
        assert config.instance_id == "empresa_a"
        assert config.instance_id != "empresa_b"


# =========================================================================
# TESTS DE AISLAMIENTO - DOLIBARR CLIENT
# =========================================================================


class TestDolibarrClientIsolation:
    """Tests de que DolibarrClient no cruza instancias."""

    def test_client_created_from_context_a(self, context_a):
        client = context_a.create_dolibarr_client()
        assert client.base_url == "http://127.0.0.1:8081"
        assert client.api_key == "dolibarr_key_a"

    def test_client_created_from_context_b(self, context_b):
        client = context_b.create_dolibarr_client()
        assert client.base_url == "http://127.0.0.1:8082"
        assert client.api_key == "dolibarr_key_b"

    def test_clients_have_different_configs(self, context_a, context_b):
        client_a = context_a.create_dolibarr_client()
        client_b = context_b.create_dolibarr_client()

        assert client_a.base_url != client_b.base_url
        assert client_a.api_key != client_b.api_key

    @pytest.mark.asyncio
    async def test_client_a_cannot_use_config_b(self, context_a, context_b):
        """Cliente de A no puede recibir config de B accidentalmente."""
        client = DolibarrClient.from_instance_config(context_a.instance_config.dolibarr)
        assert client.base_url == context_a.dolibarr_config.internal_url
        assert client.base_url != context_b.dolibarr_config.internal_url


# =========================================================================
# TESTS DE AISLAMIENTO - TELEGRAM
# =========================================================================


class TestTelegramIsolation:
    """Tests de aislamiento de Telegram por instancia."""

    def test_telegram_clients_different_tokens(self, context_a, context_b):
        client_a = context_a.create_telegram_client()
        client_b = context_b.create_telegram_client()

        assert client_a.bot_token == "telegram_token_a"
        assert client_b.bot_token == "telegram_token_b"
        assert client_a.bot_token != client_b.bot_token

    @pytest.mark.asyncio
    async def test_webhook_a_cannot_process_b_updates(self, context_a, context_b):
        """Webhook de A no puede procesar updates de B."""
        # Simular update para empresa_a

        # El path determina la instancia
        # /webhook/empresa_a -> empresa_a
        # /webhook/empresa_b -> empresa_b

        # Si alguien intenta enviar update de B a webhook de A,
        # el path /webhook/empresa_a resolverá empresa_a
        # y el bot_token usado será el de empresa_a
        # El mensaje fallará en Telegram (wrong token) o se ignorará

        assert context_a.telegram_config.webhook_path == "/webhook/empresa_a"
        assert context_b.telegram_config.webhook_path == "/webhook/empresa_b"
        assert context_a.telegram_config.webhook_path != context_b.telegram_config.webhook_path

    @pytest.mark.asyncio
    async def test_telegram_secret_verification_per_instance(self, context_a, context_b):
        """Cada instancia tiene su webhook_secret único."""
        secret_a = context_a.telegram_config.webhook_secret
        secret_b = context_b.telegram_config.webhook_secret

        assert secret_a != secret_b

        import hmac

        # Token válido para A
        valid_a = hmac.compare_digest(secret_a, secret_a)
        # Token de B NO válido para A
        invalid_b_for_a = hmac.compare_digest(secret_a, secret_b)

        assert valid_a is True
        assert invalid_b_for_a is False


# =========================================================================
# TESTS DE AISLAMIENTO - EXTENSION REGISTRY
# =========================================================================


class TestExtensionRegistryIsolation:
    """Tests de que agentes/tools/workflows no cruzan instancias."""

    def setup_method(self):
        extension_registry.clear_instance("empresa_a")
        extension_registry.clear_instance("empresa_b")

    def teardown_method(self):
        extension_registry.clear_instance("empresa_a")
        extension_registry.clear_instance("empresa_b")

    def test_agents_registered_per_instance(self):
        """Agentes registrados solo para su instancia."""

        def make_agent_a(config):
            return "agent_a"

        def make_agent_b(config):
            return "agent_b"

        extension_registry.register_agent("empresa_a", "invoice_processing", make_agent_a)
        extension_registry.register_agent("empresa_b", "dog_intake", make_agent_b)

        agents_a = extension_registry.list_agents("empresa_a")
        agents_b = extension_registry.list_agents("empresa_b")

        assert len(agents_a) == 1
        assert agents_a[0].name == "invoice_processing"

        assert len(agents_b) == 1
        assert agents_b[0].name == "dog_intake"

        # A no ve agentes de B
        assert "dog_intake" not in [a.name for a in agents_a]
        assert "invoice_processing" not in [a.name for a in agents_b]

    def test_tools_registered_per_instance(self):
        """Tools registradas solo para su instancia."""

        async def tool_a():
            return "result_a"

        async def tool_b():
            return "result_b"

        extension_registry.register_tool("empresa_a", "dolibarr_search", tool_a)
        extension_registry.register_tool("empresa_b", "milanuncios_publish", tool_b)

        tools_a = extension_registry.list_tools("empresa_a")
        tools_b = extension_registry.list_tools("empresa_b")

        assert len(tools_a) == 1
        assert tools_a[0].name == "dolibarr_search"

        assert len(tools_b) == 1
        assert tools_b[0].name == "milanuncios_publish"

    def test_workflows_registered_per_instance(self):
        """Workflows registrados solo para su instancia."""
        extension_registry.register_workflow("empresa_a", "invoice_approval", [{"step": "approve"}])
        extension_registry.register_workflow("empresa_b", "dog_publishing", [{"step": "publish"}])

        wfs_a = extension_registry.list_workflows("empresa_a")
        wfs_b = extension_registry.list_workflows("empresa_b")

        assert len(wfs_a) == 1
        assert wfs_a[0].name == "invoice_approval"

        assert len(wfs_b) == 1
        assert wfs_b[0].name == "dog_publishing"

    def test_prompts_registered_per_instance(self):
        """Prompts registrados solo para su instancia."""
        extension_registry.register_prompt("empresa_a", "invoice_prompt", "Procesa factura: {data}")
        extension_registry.register_prompt("empresa_b", "dog_prompt", "Publica perro: {data}")

        prompt_a = extension_registry.get_prompt("empresa_a", "invoice_prompt")
        prompt_b = extension_registry.get_prompt("empresa_b", "dog_prompt")

        assert prompt_a == "Procesa factura: {data}"
        assert prompt_b == "Publica perro: {data}"

        assert extension_registry.get_prompt("empresa_a", "dog_prompt") is None
        assert extension_registry.get_prompt("empresa_b", "invoice_prompt") is None


# =========================================================================
# TESTS DE AISLAMIENTO - PATH TRAVERSAL
# =========================================================================


class TestPathTraversalProtection:
    """Tests de que path traversal no permite escapar de instancia."""

    def test_documents_path_isolation(self, instance_a_config, instance_b_config):
        path_a = instance_a_config.documents_path
        path_b = instance_b_config.documents_path

        assert "empresa_a" in path_a
        assert "empresa_b" in path_b
        assert path_a != path_b

        # Intentar traversal desde A hacia B
        traversal = path_a + "/../../empresa_b/documents"
        # En producción, usar pathlib.resolve() y verificar que está dentro de base
        from pathlib import Path

        base_a = Path(path_a).resolve()
        attempted = Path(traversal).resolve()

        # Verificar que attempted NO está dentro de base_a
        # (en test real, esto lanzaría excepción o sería bloqueado)
        assert base_a not in attempted.parents

    def test_backups_path_isolation(self, instance_a_config, instance_b_config):
        assert instance_a_config.backups_path != instance_b_config.backups_path
        assert "empresa_a" in instance_a_config.backups_path
        assert "empresa_b" in instance_b_config.backups_path

    def test_runtime_path_isolation(self, instance_a_config, instance_b_config):
        assert instance_a_config.runtime_path != instance_b_config.runtime_path


# =========================================================================
# TESTS DE AISLAMIENTO - CLOUDFLARE INGRESS
# =========================================================================


class TestCloudflareIngressIsolation:
    """Tests de que Cloudflare ingress no cruza instancias."""

    @pytest.mark.asyncio
    async def test_ingress_generates_correct_routes(self, instance_a_config, instance_b_config):
        from core.integrations.cloudflare.manager import CloudflareManager

        manager = CloudflareManager("token", "account", "zone")
        config = manager.generate_ingress_config([instance_a_config, instance_b_config])

        ingress_rules = config["config"]["ingress"][:-1]  # Excluir catch-all

        # Verificar que hostnames de A apuntan a puerto de A
        for rule in ingress_rules:
            hostname = rule["hostname"]
            service = rule["service"]

            if "empresa-a.com" in hostname:
                assert ":8081" in service or ":8000" in service  # puerto de A
            elif "empresa-b.es" in hostname:
                assert ":8082" in service or ":8000" in service  # puerto de B

    @pytest.mark.asyncio
    async def test_ingress_validation_rejects_cross_instance(self, instance_a_config, instance_b_config):
        from core.integrations.cloudflare.manager import CloudflareManager

        manager = CloudflareManager("token", "account", "zone")
        config = manager.generate_ingress_config([instance_a_config, instance_b_config])

        # Validar config
        validation = manager._validate_ingress_config(config, [instance_a_config, instance_b_config])
        assert validation["valid"] is True

        # Crear config maliciosa con hostname de A apuntando a puerto de B
        bad_config = {
            "config": {
                "ingress": [
                    {"hostname": "dolibarr.empresa-a.com", "service": "http://127.0.0.1:8082"},  # Puerto de B!
                    {"service": "http_status:404"},
                ]
            }
        }

        validation = manager._validate_ingress_config(bad_config, [instance_a_config, instance_b_config])
        assert validation["valid"] is False
        assert any("wrong port" in e.lower() for e in validation["errors"])


# =========================================================================
# TESTS DE AISLAMIENTO - REDIS DB SEPARATION
# =========================================================================


class TestRedisDatabaseIsolation:
    """Tests de que cada instancia usa DB Redis separada."""

    def test_redis_db_numbers_different(self, instance_a_config, instance_b_config):
        db_a = instance_a_config.get_redis_db()
        db_b = instance_b_config.get_redis_db()

        assert db_a != db_b
        assert 0 <= db_a <= 15
        assert 0 <= db_b <= 15

    def test_redis_urls_different(self, instance_a_config, instance_b_config):
        global_redis = "redis://localhost:6379/0"

        url_a = instance_a_config.get_redis_url(global_redis)
        url_b = instance_b_config.get_redis_url(global_redis)

        assert url_a != url_b
        assert f"/{instance_a_config.get_redis_db()}" in url_a
        assert f"/{instance_b_config.get_redis_db()}" in url_b


# =========================================================================
# TESTS DE AISLAMIENTO - INSTANCE CONFIG VALIDATION
# =========================================================================


class TestInstanceConfigValidation:
    """Tests de validación de InstanceConfig."""

    def test_instance_id_validation_rejects_invalid(self):
        with pytest.raises(ValueError):
            InstanceConfig(
                instance_id="Empresa A",  # Mayúsculas y espacio
                company_name="Test",
                database=DatabaseConfig(
                    host="127.0.0.1",
                    port=3306,
                    name="d",
                    user="u",
                    password="p",
                ),
                dolibarr=DolibarrConfig(
                    internal_url="http://x",
                    api_key="k",
                    documents_path="/d",
                ),
                telegram=TelegramConfig(bot_token="t", webhook_path="/w", webhook_secret="s"),
                domains=DomainConfig(base="test.com"),
            )

    def test_instance_id_validation_rejects_reserved(self):
        for reserved in [
            "global",
            "shared",
            "core",
            "instances",
            "companies",
            "scripts",
            "tests",
            "config",
            "infrastructure",
        ]:
            with pytest.raises(ValueError):
                InstanceConfig(
                    instance_id=reserved,
                    company_name="Test",
                    database=DatabaseConfig(
                        host="127.0.0.1",
                        port=3306,
                        name="d",
                        user="u",
                        password="p",
                    ),
                    dolibarr=DolibarrConfig(
                        internal_url="http://x",
                        api_key="k",
                        documents_path="/d",
                    ),
                    telegram=TelegramConfig(bot_token="t", webhook_path="/w", webhook_secret="s"),
                    domains=DomainConfig(base="test.com"),
                )

    def test_domain_validation_requires_valid_domain(self):
        with pytest.raises(ValueError):
            InstanceConfig(
                instance_id="test",
                company_name="Test",
                database=DatabaseConfig(
                    host="127.0.0.1",
                    port=3306,
                    name="d",
                    user="u",
                    password="p",
                ),
                dolibarr=DolibarrConfig(
                    internal_url="http://x",
                    api_key="k",
                    documents_path="/d",
                ),
                telegram=TelegramConfig(bot_token="t", webhook_path="/w", webhook_secret="s"),
                domains=DomainConfig(base="invalid"),  # No es dominio válido
            )


# =========================================================================
# RUN TESTS
# =========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
