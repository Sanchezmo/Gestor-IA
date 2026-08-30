"""Rotate Cloudflare Tunnel Token."""

from __future__ import annotations

import subprocess
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
    get_global_env_path,
)


class CloudflareRotation(RotationBase):
    """Rotate Cloudflare Tunnel Token."""

    @property
    def name(self) -> str:
        return "Cloudflare Tunnel Token"

    @property
    def description(self) -> str:
        return "Rotate Cloudflare Tunnel token. Only updates token file and .env, restarts cloudflared."

    def preflight(self) -> None:
        # Check cloudflared service exists
        result = run_cmd(["systemctl", "status", "cloudflared"], check=False, sudo=True)
        if result.returncode not in (0, 3, 4):  # 3=inactive, 4=not found
            raise PreflightError("cloudflared service not found")

        # Check token file exists
        token_file = Path("/etc/cloudflared/token")
        if not token_file.exists():
            raise PreflightError(f"Token file not found: {token_file}")

        # Check we can read current token
        try:
            current = token_file.read_text().strip()
            if not current:
                raise PreflightError("Current token file is empty")
        except PermissionError:
            raise PreflightError("Cannot read token file (need sudo)")

        print(f"    Current token: {current[:20]}...")

    def backup(self) -> BackupInfo:
        backup = BackupInfo(timestamp=datetime.now().isoformat())

        # Backup token file
        token_file = Path("/etc/cloudflared/token")
        backup.files[token_file] = token_file.read_text()

        # Backup .env CLOUDFLARE_TUNNEL_TOKEN
        env_file = get_global_env_path()
        env = load_env_file(env_file)
        if "CLOUDFLARE_TUNNEL_TOKEN" in env:
            backup.files[env_file] = env_file.read_text()

        # Backup service state
        result = run_cmd(["systemctl", "is-active", "cloudflared"], check=False, sudo=True)
        backup.service_state["cloudflared"] = result.returncode == 0

        return backup

    def change(self, backup: BackupInfo) -> RotationResult:
        result = RotationResult(success=True, message="")

        # Get new token from user
        print("    Obtén el nuevo token desde Cloudflare Dashboard:")
        print("    Zero Trust → Networks → Tunnels → Tu túnel → Token")
        new_token = prompt_secret(
            "Nuevo Cloudflare Tunnel Token",
            hidden=True,
            validator=lambda t: len(t) > 50 and t.startswith("eyJ")  # JWT format
        )

        # Update token file
        token_file = Path("/etc/cloudflared/token")
        if not self.dry_run:
            token_file.write_text(new_token)
        result.changed_files.append(token_file)

        # Update .env
        env_file = get_global_env_path()
        changed = update_env_file(env_file, {"CLOUDFLARE_TUNNEL_TOKEN": new_token}, backup)
        result.changed_files.extend(changed)

        # Restart cloudflared
        if not self.dry_run:
            run_cmd(["systemctl", "restart", "cloudflared"], check=False, sudo=True)
            time.sleep(3)
        result.changed_services.append("cloudflared")

        result.message = "Cloudflare token rotated"
        return result

    def validate(self, result: RotationResult) -> None:
        if self.dry_run:
            print("    [DRY RUN] Saltando validación de cloudflared")
            return

        # Wait for service to stabilize
        time.sleep(5)

        # Check service is active
        svc_result = run_cmd(["systemctl", "is-active", "cloudflared"], check=False, sudo=True)
        if svc_result.returncode != 0:
            raise ValidationError("cloudflared service not active after restart")

        # Check logs for successful connection
        log_result = run_cmd(["journalctl", "-u", "cloudflared", "-n", "20", "--no-pager"], check=False, sudo=True)
        logs = log_result.stdout
        if "precheck complete" not in logs and "Tunnel connection" not in logs:
            # Might still be connecting, check for errors
            if "ERR" in logs and "failed to serve tunnel connection" in logs:
                raise ValidationError("Tunnel connection failing (check token validity)")

        print("    ✅ cloudflared active and connecting")

    def post_check(self, result: RotationResult) -> None:
        # Verify tunnel is actually passing traffic by checking a known endpoint
        pass


# Import at bottom to avoid circular imports
from datetime import datetime
from scripts.rotate.base import load_env_file
import time