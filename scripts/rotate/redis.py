"""Rotate Redis Password."""

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
    get_global_env_path,
    load_env_file,
    update_env_file,
    generate_password,
)


class RedisRotation(RotationBase):
    """Rotate Redis password."""

    @property
    def name(self) -> str:
        return "Redis Password"

    @property
    def description(self) -> str:
        return "Rotate Redis password. Updates redis.conf, .env, and restarts redis-server."

    def preflight(self) -> None:
        # Check Redis service exists
        res = run_cmd(["systemctl", "status", "redis-server"], check=False, sudo=True)
        if res.returncode not in (0, 3):
            raise PreflightError("redis-server service not found")

        # Check current password in .env
        env_file = get_global_env_path()
        env = load_env_file(env_file)
        if "REDIS_PASSWORD" not in env:
            raise PreflightError("REDIS_PASSWORD not found in .env")

        current = env["REDIS_PASSWORD"]
        print(f"    Current password: {current[:10]}...")

        # Test current connection
        res = run_cmd(["redis-cli", "-a", current, "ping"], check=False)
        if res.returncode != 0 or "PONG" not in res.stdout:
            raise PreflightError("No se puede conectar a Redis con contraseña actual")

        print("    ✅ Conexión Redis actual OK")

    def backup(self) -> BackupInfo:
        backup = BackupInfo(timestamp=datetime.now().isoformat())

        # Backup .env
        env_file = get_global_env_path()
        backup.files[env_file] = env_file.read_text()

        # Backup redis.conf
        redis_conf = Path("/etc/redis/redis.conf")
        if redis_conf.exists():
            backup.files[redis_conf] = redis_conf.read_text()

        # Backup service state
        res = run_cmd(["systemctl", "is-active", "redis-server"], check=False, sudo=True)
        backup.service_state["redis-server"] = res.returncode == 0

        return backup

    def change(self, backup: BackupInfo) -> RotationResult:
        result = RotationResult(success=True, message="")

        # Generate new password
        print("    Generando nueva contraseña...")
        new_password = generate_password(32)
        print(f"    Nueva contraseña: {new_password}")

        # Update redis.conf (requirepass)
        redis_conf = Path("/etc/redis/redis.conf")
        if not self.dry_run:
            content = redis_conf.read_text()
            lines = content.splitlines()
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("requirepass"):
                    lines[i] = f"requirepass {new_password}"
                    break
            else:
                # Add if not found
                lines.append(f"requirepass {new_password}")
            redis_conf.write_text("\n".join(lines) + "\n")

            # Apply immediately via CONFIG SET
            run_cmd(["redis-cli", "-a", load_env_file(get_global_env_path())["REDIS_PASSWORD"], "CONFIG", "SET", "requirepass", new_password], check=False)
            run_cmd(["redis-cli", "-a", new_password, "CONFIG", "REWRITE"], check=False)

        result.changed_files.append(redis_conf)

        # Update .env
        env_file = get_global_env_path()
        changed = update_env_file(env_file, {"REDIS_PASSWORD": new_password}, backup)
        result.changed_files.extend(changed)

        # Restart redis-server
        if not self.dry_run:
            run_cmd(["systemctl", "restart", "redis-server"], check=False, sudo=True)
            time.sleep(3)
        result.changed_services.append("redis-server")

        result.message = "Redis password rotated"
        return result

    def validate(self, result: RotationResult) -> None:
        if self.dry_run:
            print("    [DRY RUN] Saltando validación de Redis")
            return

        # Test new password
        env_file = get_global_env_path()
        env = load_env_file(env_file)
        new_password = env["REDIS_PASSWORD"]

        print("    Probando nueva contraseña...")
        res = run_cmd(["redis-cli", "-a", new_password, "ping"], check=False)
        if res.returncode != 0 or "PONG" not in res.stdout:
            raise ValidationError(f"Nueva contraseña no funciona: {res.stdout}")

        print("    ✅ Redis acepta nueva contraseña")

        # Test service is active
        res = run_cmd(["systemctl", "is-active", "redis-server"], check=False, sudo=True)
        if res.returncode != 0:
            raise ValidationError("redis-server no activo tras reinicio")

        print("    ✅ redis-server activo")

    def post_check(self, result: RotationResult) -> None:
        # Verify Hermes can connect (would need Hermes restart)
        pass


from datetime import datetime
import time