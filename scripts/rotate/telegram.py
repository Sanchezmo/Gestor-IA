"""Rotate Telegram Bot Token."""

from __future__ import annotations

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
    load_env_file,
    update_env_file,
)


class TelegramRotation(RotationBase):
    """Rotate Telegram Bot Token for a specific instance."""

    def __init__(self, instance_id: str, dry_run: bool = False, verbose: bool = False):
        super().__init__(instance_id, dry_run, verbose)
        if not instance_id:
            raise ValueError("instance_id required for Telegram rotation")

    @property
    def name(self) -> str:
        return f"Telegram Bot Token ({self.instance_id})"

    @property
    def description(self) -> str:
        return "Rotate Telegram Bot Token. Updates instance.env and validates with Telegram API."

    def preflight(self) -> None:
        # Check instance exists
        env_file = get_instance_env_path(self.instance_id)
        if not env_file.exists():
            raise PreflightError(f"Instance not found: {self.instance_id}")

        # Check current token exists
        env = load_env_file(env_file)
        token_key = f"TELEGRAM_BOT_TOKEN_{self.instance_id.upper()}"
        if token_key not in env:
            raise PreflightError(f"No current token found in {env_file}")

        current = env[token_key]
        print(f"    Current token: {current[:10]}...")

        # Validate current token works with Telegram
        print("    Verificando token actual con Telegram API...")
        result = run_cmd([
            "curl", "-s", f"https://api.telegram.org/bot{current}/getMe"
        ], check=False, sudo=False)
        if result.returncode == 0 and '"ok":true' in result.stdout:
            print("    ✅ Token actual válido")
        else:
            print("    ⚠️  Token actual podría estar inválido")

    def backup(self) -> BackupInfo:
        backup = BackupInfo(timestamp=datetime.now().isoformat())

        env_file = get_instance_env_path(self.instance_id)
        backup.files[env_file] = env_file.read_text()

        return backup

    def change(self, backup: BackupInfo) -> RotationResult:
        result = RotationResult(success=True, message="")

        print("    Obtén el nuevo token desde BotFather:")
        print("    /revoke → elige bot → copia nuevo token")
        new_token = prompt_secret(
            "Nuevo Telegram Bot Token",
            hidden=True,
            validator=lambda t: ":" in t and len(t.split(":")[0]) > 5
        )

        token_key = f"TELEGRAM_BOT_TOKEN_{self.instance_id.upper()}"
        env_file = get_instance_env_path(self.instance_id)

        changed = update_env_file(env_file, {token_key: new_token}, backup)
        result.changed_files.extend(changed)

        result.message = f"Telegram token rotated for {self.instance_id}"
        return result

    def validate(self, result: RotationResult) -> None:
        if self.dry_run:
            print("    [DRY RUN] Saltando validación con Telegram API")
            return

        # Read new token from env
        env_file = get_instance_env_path(self.instance_id)
        env = load_env_file(env_file)
        token_key = f"TELEGRAM_BOT_TOKEN_{self.instance_id.upper()}"
        new_token = env.get(token_key)

        if not new_token:
            raise ValidationError("Token not found in env after rotation")

        # Validate with Telegram API
        print("    Verificando nuevo token con Telegram API...")
        result = run_cmd([
            "curl", "-s", f"https://api.telegram.org/bot{new_token}/getMe"
        ], check=False, sudo=False)

        if result.returncode != 0 or '"ok":true' not in result.stdout:
            raise ValidationError(f"Nuevo token inválido: {result.stdout[:200]}")

        print("    ✅ Nuevo token válido en Telegram")

    def post_check(self, result: RotationResult) -> None:
        # Verify webhook can be set (optional)
        pass


from datetime import datetime