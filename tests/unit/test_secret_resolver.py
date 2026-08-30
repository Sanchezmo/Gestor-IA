"""
Tests for SecretResolver - FAIL CLOSED behavior, env resolution, no leakage.
"""

import os
import tempfile
from pathlib import Path

import pytest

from core.hermes.secret_resolver import SecretResolver, SecretResolutionError


class TestSecretResolver:
    """Tests for SecretResolver class."""

    @pytest.fixture
    def temp_instances_root(self):
        """Create a temporary instances root directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def instance_dir(self, temp_instances_root):
        """Create a test instance directory with instance.env."""
        instance_dir = temp_instances_root / "test_instance"
        instance_dir.mkdir(parents=True)

        # Create instance.env with test secrets
        env_content = """# Test instance secrets
DOLIBARR_DB_PASSWORD_TEST_INSTANCE=test_db_password_123
DOLIBARR_API_KEY_TEST_INSTANCE=test_api_key_456
TELEGRAM_BOT_TOKEN_TEST_INSTANCE=123456789:ABCdefGhIjKlMnOpQrStUvWxYz
TELEGRAM_WEBHOOK_SECRET_TEST_INSTANCE=abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890
"""
        (instance_dir / "instance.env").write_text(env_content)

        yield instance_dir

    def test_resolve_secrets_valid_env_refs(self, instance_dir):
        """Test 1: Valid env reference -> correct loading."""
        resolver = SecretResolver("test_instance", instance_dir.parent)

        secrets_refs = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_TEST_INSTANCE",
            "dolibarr_api_key": "env:DOLIBARR_API_KEY_TEST_INSTANCE",
            "telegram_bot_token": "env:TELEGRAM_BOT_TOKEN_TEST_INSTANCE",
            "telegram_webhook_secret": "env:TELEGRAM_WEBHOOK_SECRET_TEST_INSTANCE",
        }

        resolved = resolver.resolve_secrets(secrets_refs)

        assert resolved["dolibarr_db_password"] == "test_db_password_123"
        assert resolved["dolibarr_api_key"] == "test_api_key_456"
        assert resolved["telegram_bot_token"] == "123456789:ABCdefGhIjKlMnOpQrStUvWxYz"
        assert resolved["telegram_webhook_secret"] == "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

    def test_missing_env_var_fails_closed(self, instance_dir):
        """Test 2: Missing variable -> FAIL CLOSED."""
        resolver = SecretResolver("test_instance", instance_dir.parent)

        secrets_refs = {
            "dolibarr_db_password": "env:NONEXISTENT_VAR",
        }

        with pytest.raises(SecretResolutionError) as exc_info:
            resolver.resolve_secrets(secrets_refs)

        assert "NONEXISTENT_VAR" in str(exc_info.value)
        assert "not found" in str(exc_info.value)

    def test_missing_instance_env_file_fails_closed(self, temp_instances_root):
        """Test 2b: Missing instance.env file -> FAIL CLOSED."""
        instance_dir = temp_instances_root / "missing_env"
        instance_dir.mkdir(parents=True)
        # No instance.env file created

        resolver = SecretResolver("missing_env", temp_instances_root)

        secrets_refs = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_MISSING_ENV",
        }

        with pytest.raises(SecretResolutionError) as exc_info:
            resolver.resolve_secrets(secrets_refs)

        assert "Instance env file not found" in str(exc_info.value)

    def test_secret_not_in_repr_or_log(self, instance_dir, capfd):
        """Test 3: Secret does not appear in repr/log."""
        resolver = SecretResolver("test_instance", instance_dir.parent)

        secrets_refs = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_TEST_INSTANCE",
        }

        resolved = resolver.resolve_secrets(secrets_refs)

        # Check that secret value is not in string representation
        resolver_repr = repr(resolver)
        assert "test_db_password_123" not in resolver_repr

        # Check stdout/stderr for leakage
        out, err = capfd.readouterr()
        assert "test_db_password_123" not in out
        assert "test_db_password_123" not in err

    def test_yaml_works_without_real_secrets(self, instance_dir):
        """Test 4: YAML versioned works without containing secret."""
        # This test verifies that the config.yml can have null/placeholder values
        # and secrets are injected at runtime via SecretResolver

        resolver = SecretResolver("test_instance", instance_dir.parent)

        # Simulate config.yml data with null secrets
        config_data = {
            "instance_id": "test_instance",
            "company_name": "Test Company",
            "database": {
                "host": "127.0.0.1",
                "port": 3306,
                "name": "dolibarr_test",
                "user": "db_test",
                "password": None,  # Placeholder - will be replaced
            },
            "dolibarr": {
                "version": "23.0.4",
                "internal_url": "http://127.0.0.1:8081",
                "api_key": None,  # Placeholder - will be replaced
            },
            "telegram": {
                "bot_token": None,  # Placeholder - will be replaced
                "webhook_path": "/webhook/test",
                "webhook_secret": None,  # Placeholder - will be replaced
                "webhook_secret_required": True,
            },
            "domains": {
                "base": "test.com",
            },
            "secrets_refs": {
                "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_TEST_INSTANCE",
                "dolibarr_api_key": "env:DOLIBARR_API_KEY_TEST_INSTANCE",
                "telegram_bot_token": "env:TELEGRAM_BOT_TOKEN_TEST_INSTANCE",
                "telegram_webhook_secret": "env:TELEGRAM_WEBHOOK_SECRET_TEST_INSTANCE",
            },
        }

        # Apply secrets
        resolved_config = resolver.apply_secrets_to_config(config_data, config_data["secrets_refs"])

        # Verify secrets were injected
        assert resolved_config["database"]["password"] == "test_db_password_123"
        assert resolved_config["dolibarr"]["api_key"] == "test_api_key_456"
        assert resolved_config["telegram"]["bot_token"] == "123456789:ABCdefGhIjKlMnOpQrStUvWxYz"
        assert resolved_config["telegram"]["webhook_secret"] == "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

        # Original config_data should be unchanged (deep copy)
        assert config_data["database"]["password"] is None
        assert config_data["dolibarr"]["api_key"] is None
        assert config_data["telegram"]["bot_token"] is None
        assert config_data["telegram"]["webhook_secret"] is None

    def test_different_instances_resolve_own_secrets(self, temp_instances_root):
        """Test 5: Different instances resolve their own secrets."""
        # Create instance A
        instance_a = temp_instances_root / "instance_a"
        instance_a.mkdir(parents=True)
        (instance_a / "instance.env").write_text("""DOLIBARR_DB_PASSWORD_INSTANCE_A=secret_a_db
DOLIBARR_API_KEY_INSTANCE_A=secret_a_api
TELEGRAM_BOT_TOKEN_INSTANCE_A=111:AAA
TELEGRAM_WEBHOOK_SECRET_INSTANCE_A=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""")

        # Create instance B
        instance_b = temp_instances_root / "instance_b"
        instance_b.mkdir(parents=True)
        (instance_b / "instance.env").write_text("""DOLIBARR_DB_PASSWORD_INSTANCE_B=secret_b_db
DOLIBARR_API_KEY_INSTANCE_B=secret_b_api
TELEGRAM_BOT_TOKEN_INSTANCE_B=222:BBB
TELEGRAM_WEBHOOK_SECRET_INSTANCE_B=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""")

        resolver_a = SecretResolver("instance_a", temp_instances_root)
        resolver_b = SecretResolver("instance_b", temp_instances_root)

        secrets_refs_a = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_INSTANCE_A",
            "dolibarr_api_key": "env:DOLIBARR_API_KEY_INSTANCE_A",
            "telegram_bot_token": "env:TELEGRAM_BOT_TOKEN_INSTANCE_A",
            "telegram_webhook_secret": "env:TELEGRAM_WEBHOOK_SECRET_INSTANCE_A",
        }

        secrets_refs_b = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_INSTANCE_B",
            "dolibarr_api_key": "env:DOLIBARR_API_KEY_INSTANCE_B",
            "telegram_bot_token": "env:TELEGRAM_BOT_TOKEN_INSTANCE_B",
            "telegram_webhook_secret": "env:TELEGRAM_WEBHOOK_SECRET_INSTANCE_B",
        }

        resolved_a = resolver_a.resolve_secrets(secrets_refs_a)
        resolved_b = resolver_b.resolve_secrets(secrets_refs_b)

        # Instance A gets its own secrets
        assert resolved_a["dolibarr_db_password"] == "secret_a_db"
        assert resolved_a["dolibarr_api_key"] == "secret_a_api"
        assert resolved_a["telegram_bot_token"] == "111:AAA"
        assert resolved_a["telegram_webhook_secret"] == "a" * 64

        # Instance B gets its own secrets
        assert resolved_b["dolibarr_db_password"] == "secret_b_db"
        assert resolved_b["dolibarr_api_key"] == "secret_b_api"
        assert resolved_b["telegram_bot_token"] == "222:BBB"
        assert resolved_b["telegram_webhook_secret"] == "b" * 64

        # Cross-contamination check
        assert resolved_a["dolibarr_db_password"] != resolved_b["dolibarr_db_password"]
        assert resolved_a["telegram_bot_token"] != resolved_b["telegram_bot_token"]

    def test_no_cross_contamination_between_companies(self, temp_instances_root):
        """Test 6: No contamination between companies (instances)."""
        # This is essentially the same as test 5 but more explicit about isolation
        instance_a = temp_instances_root / "company_alpha"
        instance_a.mkdir(parents=True)
        (instance_a / "instance.env").write_text("""DOLIBARR_DB_PASSWORD_COMPANY_ALPHA=alpha_db_secret
DOLIBARR_API_KEY_COMPANY_ALPHA=alpha_api_secret
TELEGRAM_BOT_TOKEN_COMPANY_ALPHA=111:ALPHA
TELEGRAM_WEBHOOK_SECRET_COMPANY_ALPHA=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
""")

        instance_b = temp_instances_root / "company_beta"
        instance_b.mkdir(parents=True)
        (instance_b / "instance.env").write_text("""DOLIBARR_DB_PASSWORD_COMPANY_BETA=beta_db_secret
DOLIBARR_API_KEY_COMPANY_BETA=beta_api_secret
TELEGRAM_BOT_TOKEN_COMPANY_BETA=222:BETA
TELEGRAM_WEBHOOK_SECRET_COMPANY_BETA=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
""")

        resolver_alpha = SecretResolver("company_alpha", temp_instances_root)
        resolver_beta = SecretResolver("company_beta", temp_instances_root)

        refs_alpha = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_COMPANY_ALPHA",
            "dolibarr_api_key": "env:DOLIBARR_API_KEY_COMPANY_ALPHA",
            "telegram_bot_token": "env:TELEGRAM_BOT_TOKEN_COMPANY_ALPHA",
            "telegram_webhook_secret": "env:TELEGRAM_WEBHOOK_SECRET_COMPANY_ALPHA",
        }

        refs_beta = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_COMPANY_BETA",
            "dolibarr_api_key": "env:DOLIBARR_API_KEY_COMPANY_BETA",
            "telegram_bot_token": "env:TELEGRAM_BOT_TOKEN_COMPANY_BETA",
            "telegram_webhook_secret": "env:TELEGRAM_WEBHOOK_SECRET_COMPANY_BETA",
        }

        resolved_alpha = resolver_alpha.resolve_secrets(refs_alpha)
        resolved_beta = resolver_beta.resolve_secrets(refs_beta)

        # Alpha secrets
        assert "alpha" in resolved_alpha["dolibarr_db_password"]
        assert "alpha" in resolved_alpha["dolibarr_api_key"]
        assert "ALPHA" in resolved_alpha["telegram_bot_token"]

        # Beta secrets
        assert "beta" in resolved_beta["dolibarr_db_password"]
        assert "beta" in resolved_beta["dolibarr_api_key"]
        assert "BETA" in resolved_beta["telegram_bot_token"]

        # No leakage
        assert "beta" not in resolved_alpha["dolibarr_db_password"]
        assert "alpha" not in resolved_beta["dolibarr_db_password"]

    def test_invalid_ref_format_fails(self, instance_dir):
        """Test invalid secret reference format fails."""
        resolver = SecretResolver("test_instance", instance_dir.parent)

        secrets_refs = {
            "dolibarr_db_password": "vault:path/to/secret",  # Not supported
        }

        with pytest.raises(SecretResolutionError) as exc_info:
            resolver.resolve_secrets(secrets_refs)

        assert "Invalid secret reference" in str(exc_info.value)
        assert "Only 'env:VAR_NAME' format" in str(exc_info.value)

    def test_empty_env_var_name_fails(self, instance_dir):
        """Test empty environment variable name fails."""
        resolver = SecretResolver("test_instance", instance_dir.parent)

        secrets_refs = {
            "dolibarr_db_password": "env:",  # Empty
        }

        with pytest.raises(SecretResolutionError) as exc_info:
            resolver.resolve_secrets(secrets_refs)

        assert "Empty environment variable name" in str(exc_info.value)

    def test_missing_required_secrets_ref_fails(self, instance_dir):
        """Test missing required secret references fails validation."""
        resolver = SecretResolver("test_instance", instance_dir.parent)

        # Missing telegram secrets
        secrets_refs = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_TEST_INSTANCE",
            "dolibarr_api_key": "env:DOLIBARR_API_KEY_TEST_INSTANCE",
            # telegram_bot_token and telegram_webhook_secret missing
        }

        with pytest.raises(SecretResolutionError) as exc_info:
            resolver.validate_required_secrets(secrets_refs)

        assert "missing required secret references" in str(exc_info.value)
        assert "telegram_bot_token" in str(exc_info.value)
        assert "telegram_webhook_secret" in str(exc_info.value)

    def test_env_var_precedence_instance_env_over_os_environ(self, instance_dir, monkeypatch):
        """Test instance.env takes precedence over os.environ."""
        # Set OS environment variable
        monkeypatch.setenv("DOLIBARR_DB_PASSWORD_TEST_INSTANCE", "from_os_environ")

        resolver = SecretResolver("test_instance", instance_dir.parent)

        secrets_refs = {
            "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_TEST_INSTANCE",
        }

        resolved = resolver.resolve_secrets(secrets_refs)

        # Should use instance.env value, not os.environ
        assert resolved["dolibarr_db_password"] == "test_db_password_123"
        assert resolved["dolibarr_db_password"] != "from_os_environ"

    def test_apply_secrets_preserves_other_config(self, instance_dir):
        """Test that applying secrets preserves other config fields."""
        resolver = SecretResolver("test_instance", instance_dir.parent)

        config_data = {
            "instance_id": "test_instance",
            "company_name": "Test Company",
            "database": {
                "host": "127.0.0.1",
                "port": 3306,
                "name": "dolibarr_test",
                "user": "db_test",
                "password": None,
            },
            "dolibarr": {
                "version": "23.0.4",
                "internal_url": "http://127.0.0.1:8081",
                "api_key": None,
                "documents_path": "/var/lib/dolibarr/documents/test",
            },
            "telegram": {
                "bot_token": None,
                "webhook_path": "/webhook/test",
                "webhook_secret": None,
                "webhook_secret_required": True,
                "allowed_user_ids": [123, 456],
                "max_file_size_mb": 10,
            },
            "domains": {
                "base": "test.com",
                "dolibarr": "dolibarr.test.com",
            },
            "secrets_refs": {
                "dolibarr_db_password": "env:DOLIBARR_DB_PASSWORD_TEST_INSTANCE",
                "dolibarr_api_key": "env:DOLIBARR_API_KEY_TEST_INSTANCE",
                "telegram_bot_token": "env:TELEGRAM_BOT_TOKEN_TEST_INSTANCE",
                "telegram_webhook_secret": "env:TELEGRAM_WEBHOOK_SECRET_TEST_INSTANCE",
            },
        }

        resolved_config = resolver.apply_secrets_to_config(config_data, config_data["secrets_refs"])

        # Secrets injected
        assert resolved_config["database"]["password"] == "test_db_password_123"
        assert resolved_config["dolibarr"]["api_key"] == "test_api_key_456"
        assert resolved_config["telegram"]["bot_token"] == "123456789:ABCdefGhIjKlMnOpQrStUvWxYz"
        assert resolved_config["telegram"]["webhook_secret"] == "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"

        # Other fields preserved
        assert resolved_config["instance_id"] == "test_instance"
        assert resolved_config["company_name"] == "Test Company"
        assert resolved_config["database"]["host"] == "127.0.0.1"
        assert resolved_config["database"]["port"] == 3306
        assert resolved_config["dolibarr"]["version"] == "23.0.4"
        assert resolved_config["telegram"]["allowed_user_ids"] == [123, 456]
        assert resolved_config["telegram"]["max_file_size_mb"] == 10
        assert resolved_config["domains"]["dolibarr"] == "dolibarr.test.com"