"""Rotate Internal Secrets (JWT, Fernet, Admin Token, Backup Encryption)."""

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
    get_global_env_path,
    load_env_file,
    update_env_file,
    generate_password,
    generate_fernet_key,
)


class InternalSecretsRotation(RotationBase):
    """Rotate internal cryptographic secrets."""

    @property
    def name(self) -> str:
        return "Internal Secrets (JWT, Fernet, Admin Token, Backup Key)"

    @property
    def description(self) -> str:
        return "Rotate internal cryptographic secrets. Updates .env and restarts Hermes."

    def preflight(self) -> None:
        # Check .env exists
        env_file = get_global_env_path()
        if not env_file.exists():
            raise PreflightError(".env not found")

        env = load_env_file(env_file)
        required = ["JWT_SECRET_KEY", "FERNET_KEY", "GESTOR_IA_ADMIN_TOKEN", "BACKUP_ENCRYPTION_KEY"]
        missing = [k for k in required if k not in env]
        if missing:
            raise PreflightError(f"Missing keys in .env: {missing}")

        # Check Hermes service
        res = run_cmd(["systemctl", "status", "hermes-gestor-ia"], check=False, sudo=True)
        if res.returncode not in (0, 3):
            raise PreflightError("hermes-gestor-ia service not found")

        print("    Current secrets:")
        for k in required:
            print(f"      {k}: {env[k][:10]}...")

    def backup(self) -> BackupInfo:
        backup = BackupInfo(timestamp=datetime.now().isoformat())

        env_file = get_global_env_path()
        backup.files[env_file] = env_file.read_text()

        res = run_cmd(["systemctl", "is-active", "hermes-gestor-ia"], check=False, sudo=True)
        backup.service_state["hermes-gestor-ia"] = res.returncode == 0

        return backup

    def change(self, backup: BackupInfo) -> RotationResult:
        result = RotationResult(success=True, message="")

        print("    Generando nuevos secretos...")

        new_secrets = {
            "JWT_SECRET_KEY": secrets.token_urlsafe(64),
            "FERNET_KEY": generate_fernet_key(),
            "GESTOR_IA_ADMIN_TOKEN": secrets.token_urlsafe(48),
            "BACKUP_ENCRYPTION_KEY": secrets.token_urlsafe(32),
        }

        for k, v in new_secrets.items():
            print(f"      {k}: {v[:10]}...")

        if not self.dry_run:
            env_file = get_global_env_path()
            changed = update_env_file(env_file, new_secrets, backup)
            result.changed_files.extend(changed)

            # Restart Hermes
            run_cmd(["systemctl", "restart", "hermes-gestor-ia"], check=False, sudo=True)
            time.sleep(3)
            result.changed_services.append("hermes-gestor-ia")

        result.message = "Internal secrets rotated"
        return result

    def validate(self, result: RotationResult) -> None:
        # Check Hermes health
        print("    Verificando Hermes health...")
        time.sleep(2)
        res = run_cmd(["curl", "-s", "http://localhost:8000/health"], check=False)
        if res.returncode != 0 or "healthy" not in res.stdout:
            raise ValidationError(f"Hermes health check failed: {res.stdout}")

        print("    ✅ Hermes healthy")

    def post_check(self, result: RotationResult) -> None:
        pass


from datetime import datetime
import time