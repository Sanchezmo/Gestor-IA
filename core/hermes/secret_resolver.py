"""
Secret Resolver for InstanceConfig.

Loads secrets from gitignored instance.env files and resolves
secrets_refs references to environment variables.

FAIL CLOSED: If a required secret is missing, raises an exception.
No defaults, no fallbacks, no logging of secret values.
"""

import os
from pathlib import Path
from typing import Any

from core.hermes.utils import get_instances_root


class SecretResolutionError(Exception):
    """Raised when secret resolution fails (FAIL CLOSED)."""

    pass


class SecretResolver:
    """
    Resolves secrets for a specific instance.

    Reads instance.env (gitignored) and resolves secrets_refs from config.yml.
    All secrets must be present - FAIL CLOSED if any are missing.
    """

    # Required secret keys that must be resolved for each instance
    REQUIRED_SECRETS = {
        "dolibarr_db_password",
        "dolibarr_api_key",
        "telegram_bot_token",
        "telegram_webhook_secret",
    }

    def __init__(self, instance_id: str, instances_root: Path | None = None):
        self.instance_id = instance_id
        self.instances_root = instances_root or get_instances_root()
        self.instance_dir = self.instances_root / instance_id
        self.env_file = self.instance_dir / "instance.env"
        self._env_cache: dict[str, str] | None = None

    def _load_env_file(self) -> dict[str, str]:
        """Load environment variables from instance.env file."""
        if self._env_cache is not None:
            return self._env_cache

        env_vars: dict[str, str] = {}

        if self.env_file.exists():
            with self.env_file.open(encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip()
        else:
            # FAIL CLOSED: instance.env must exist for instances with secrets_refs
            raise SecretResolutionError(
                f"Instance env file not found: {self.env_file}. "
                f"Create it with required secrets for instance '{self.instance_id}'."
            )

        self._env_cache = env_vars
        return env_vars

    def _get_env_value(self, env_key: str) -> str:
        """Get environment variable value, checking instance.env first, then os.environ."""
        # First check instance.env
        env_vars = self._load_env_file()
        if env_key in env_vars:
            return env_vars[env_key]

        # Then check process environment
        if env_key in os.environ:
            return os.environ[env_key]

        # FAIL CLOSED
        raise SecretResolutionError(
            f"Required secret '{env_key}' not found for instance '{self.instance_id}'. "
            f"Define it in {self.env_file} or as environment variable."
        )

    def resolve_secrets(self, secrets_refs: dict[str, str]) -> dict[str, str]:
        """
        Resolve all secret references to their actual values.

        Args:
            secrets_refs: Dict mapping secret names to references (e.g., "env:VAR_NAME")

        Returns:
            Dict mapping secret names to resolved values

        Raises:
            SecretResolutionError: If any required secret is missing or reference is invalid
        """
        resolved: dict[str, str] = {}

        for secret_name, ref in secrets_refs.items():
            if not ref.startswith("env:"):
                raise SecretResolutionError(
                    f"Invalid secret reference for '{secret_name}': '{ref}'. "
                    f"Only 'env:VAR_NAME' format is supported."
                )

            env_key = ref[4:]  # Remove 'env:' prefix
            if not env_key:
                raise SecretResolutionError(
                    f"Empty environment variable name in secret reference for '{secret_name}'"
                )

            resolved[secret_name] = self._get_env_value(env_key)

        return resolved

    def validate_required_secrets(self, secrets_refs: dict[str, str]) -> None:
        """
        Validate that all required secrets are referenced in secrets_refs.

        Args:
            secrets_refs: Dict mapping secret names to references

        Raises:
            SecretResolutionError: If any required secret is not referenced
        """
        missing = self.REQUIRED_SECRETS - set(secrets_refs.keys())
        if missing:
            raise SecretResolutionError(
                f"Instance '{self.instance_id}' missing required secret references: {sorted(missing)}. "
                f"Add them to secrets_refs in config.yml."
            )

    def get_resolved_config_values(self, secrets_refs: dict[str, str]) -> dict[str, Any]:
        """
        Get resolved secret values mapped to config field paths.

        Returns dict with keys matching InstanceConfig field paths:
        - database.password
        - dolibarr.api_key
        - telegram.bot_token
        - telegram.webhook_secret
        """
        resolved = self.resolve_secrets(secrets_refs)

        return {
            "database.password": resolved.get("dolibarr_db_password"),
            "dolibarr.api_key": resolved.get("dolibarr_api_key"),
            "telegram.bot_token": resolved.get("telegram_bot_token"),
            "telegram.webhook_secret": resolved.get("telegram_webhook_secret"),
        }

    def apply_secrets_to_config(
        self,
        config_data: dict[str, Any],
        secrets_refs: dict[str, str],
    ) -> dict[str, Any]:
        """
        Apply resolved secrets to config data dict, returning a new dict with secrets injected.

        This creates a deep copy and injects secrets at the correct nested paths.
        """
        import copy

        config = copy.deepcopy(config_data)
        resolved = self.get_resolved_config_values(secrets_refs)

        # Inject database.password
        if "database.password" in resolved and resolved["database.password"] is not None:
            if "database" not in config:
                config["database"] = {}
            config["database"]["password"] = resolved["database.password"]

        # Inject dolibarr.api_key
        if "dolibarr.api_key" in resolved and resolved["dolibarr.api_key"] is not None:
            if "dolibarr" not in config:
                config["dolibarr"] = {}
            config["dolibarr"]["api_key"] = resolved["dolibarr.api_key"]

        # Inject telegram.bot_token
        if "telegram.bot_token" in resolved and resolved["telegram.bot_token"] is not None:
            if "telegram" not in config:
                config["telegram"] = {}
            config["telegram"]["bot_token"] = resolved["telegram.bot_token"]

        # Inject telegram.webhook_secret
        if "telegram.webhook_secret" in resolved and resolved["telegram.webhook_secret"] is not None:
            if "telegram" not in config:
                config["telegram"] = {}
            config["telegram"]["webhook_secret"] = resolved["telegram.webhook_secret"]

        return config


def create_secret_resolver(instance_id: str, instances_root: Path | None = None) -> SecretResolver:
    """Factory function to create a SecretResolver for an instance."""
    return SecretResolver(instance_id, instances_root)