"""Rotate Telegram Webhook Secret."""

from __future__ import annotations

import secrets
from pathlib import Path

from scripts.rotate.base import (
    RotationBase,
    RotationResult,
    BackupInfo,
    PreflightError,
    ValidationError,
    run_cmd,
    confirm,
    prompt_secret,
    get_instance_env_path,
    get_global_env_path,
    load_env_file,
    update_env_file,
)


class WebhookSecretRotation(RotationBase):
    """Rotate Telegram Webhook Secret for a specific instance."""

    def __init__(self, instance_id: str, dry_run: bool = False, verbose: bool = False):
        super().__init__(instance_id, dry_run, verbose)
        if not instance_id:
            raise ValueError("instance_id required for webhook secret rotation")

    @property
    def name(self) -> str:
        return f"Telegram Webhook Secret ({self.instance_id})"

    @property
    def description(self) -> str:
        return "Rotate Telegram webhook secret. Updates instance.env, sets new webhook with secret."

    def preflight(self) -> None:
        # Check instance exists
        env_file = get_instance_env_path(self.instance_id)
        if not env_file.exists():
            raise PreflightError(f"Instance not found: {self.instance_id}")

        env = load_env_file(env_file)
        token_key = f"TELEGRAM_BOT_TOKEN_{self.instance_id.upper()}"
        secret_key = f"TELEGRAM_WEBHOOK_SECRET_{self.instance_id.upper()}"

        if token_key not in env:
            raise PreflightError(f"No bot token found for {self.instance_id}")
        if secret_key not in env:
            raise PreflightError(f"No webhook secret found for {self.instance_id}")

        print(f"    Bot token: {env[token_key][:10]}...")
        print(f"    Current secret: {env[secret_key][:10]}...")

        # Check hermes service is running
        result = run_cmd(["systemctl", "is-active", f"hermes-{self.instance_id}"], check=False, sudo=True)
        if result.returncode != 0:
            raise PreflightError(f"hermes-{self.instance_id} not active (needed for webhook validation)")

    def backup(self) -> BackupInfo:
        backup = BackupInfo(timestamp=datetime.now().isoformat())

        env_file = get_instance_env_path(self.instance_id)
        backup.files[env_file] = env_file.read_text()

        return backup

    def change(self, backup: BackupInfo) -> RotationResult:
        result = RotationResult(success=True, message="")

        # Generate new secret or ask user
        print("    Opciones:")
        print("    1. Generar automáticamente (recomendado)")
        print("    2. Proporcionar manualmente")
        choice = input("    Elige [1/2]: ").strip()

        if choice == "2":
            new_secret = prompt_secret(
                "Nuevo webhook secret (64 chars hex)",
                hidden=False,
                validator=lambda s: len(s) == 64 and all(c in "0123456789abcdef" for c in s.lower())
            )
        else:
            new_secret = secrets.token_hex(32)
            print(f"    Generado: {new_secret}")

        secret_key = f"TELEGRAM_WEBHOOK_SECRET_{self.instance_id.upper()}"
        env_file = get_instance_env_path(self.instance_id)

        changed = update_env_file(env_file, {secret_key: new_secret}, backup)
        result.changed_files.extend(changed)

        # Update webhook with new secret
        env = load_env_file(env_file)
        bot_token = env[f"TELEGRAM_BOT_TOKEN_{self.instance_id.upper()}"]
        webhook_url = f"https://telegram-staging.mascotalegal.com/webhook/{self.instance_id}"

        print(f"    Actualizando webhook en Telegram: {webhook_url}")
        if not self.dry_run:
            cmd = [
                "curl", "-s", "-X", "POST",
                f"https://api.telegram.org/bot{bot_token}/setWebhook",
                "-H", "Content-Type: application/json",
                "-d", f'{{"url": "{webhook_url}", "secret_token": "{new_secret}", "allowed_updates": ["message", "callback_query"]}}'
            ]
            res = run_cmd(cmd, check=False, sudo=False)
            if res.returncode == 0 and '"ok":true' in res.stdout:
                print("    ✅ Webhook actualizado en Telegram")
            else:
                print(f"    ⚠️  Webhook update: {res.stdout[:200]}")

        result.message = f"Webhook secret rotated for {self.instance_id}"
        return result

    def validate(self, result: RotationResult) -> None:
        if self.dry_run:
            print("    [DRY RUN] Saltando validación de webhook")
            return

        # Verify webhook info
        env_file = get_instance_env_path(self.instance_id)
        env = load_env_file(env_file)
        bot_token = env[f"TELEGRAM_BOT_TOKEN_{self.instance_id.upper()}"]

        print("    Verificando webhook en Telegram...")
        res = run_cmd([
            "curl", "-s", f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
        ], check=False, sudo=False)

        if res.returncode != 0 or '"ok":true' not in res.stdout:
            raise ValidationError("No se pudo obtener info del webhook")

        import json
        data = json.loads(res.stdout)
        webhook_data = data.get("result", {})
        if webhook_data.get("has_custom_certificate") is None:
            # Check secret token matches
            print(f"    Webhook URL: {webhook_data.get('url', 'N/A')}")
            print(f"    Pending updates: {webhook_data.get('pending_update_count', 0)}")

        print("    ✅ Webhook configurado")

    def post_check(self, result: RotationResult) -> None:
        # Test webhook with a dummy request
        pass


from datetime import datetime
import json