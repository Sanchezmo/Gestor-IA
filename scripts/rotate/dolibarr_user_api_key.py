"""Rotate Dolibarr User API Key."""

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


class DolibarrUserApiKeyRotation(RotationBase):
    """Rotate Dolibarr API Key for a specific user in an instance."""

    def __init__(self, instance_id: str, dolibarr_user_id: int | None = None, dry_run: bool = False, verbose: bool = False):
        super().__init__(instance_id, dry_run, verbose)
        if not instance_id:
            raise ValueError("instance_id required")
        self.dolibarr_user_id = dolibarr_user_id or 1  # default to admin user

    @property
    def name(self) -> str:
        return f"Dolibarr User API Key ({self.instance_id}, user_id={self.dolibarr_user_id})"

    @property
    def description(self) -> str:
        return "Rotate Dolibarr API key for a specific user. Updates instance.env and identities.db."

    def preflight(self) -> None:
        # Check instance exists
        env_file = get_instance_env_path(self.instance_id)
        if not env_file.exists():
            raise PreflightError(f"Instance not found: {self.instance_id}")

        env = load_env_file(env_file)
        api_key_key = f"DOLIBARR_API_KEY_{self.instance_id.upper()}"
        if api_key_key not in env:
            raise PreflightError(f"No API key found in {env_file}")

        # Check identities.db exists
        identities_db = Path(f"/home/saulo/Gestor-IA/instances/{self.instance_id}/identities.db")
        if not identities_db.exists():
            raise PreflightError(f"identities.db not found for {self.instance_id}")

        # Check Dolibarr is accessible
        dolibarr_url = f"http://localhost:8081"  # Could be made configurable
        res = run_cmd(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", dolibarr_url], check=False)
        if res.returncode != 0 or res.stdout != "200":
            raise PreflightError("Dolibarr no accesible en localhost:8081")

        print(f"    Instance: {self.instance_id}")
        print(f"    Dolibarr user ID: {self.dolibarr_user_id}")
        print(f"    Current API key: {env[api_key_key][:10]}...")

    def backup(self) -> BackupInfo:
        backup = BackupInfo(timestamp=datetime.now().isoformat())

        # Backup instance.env
        env_file = get_instance_env_path(self.instance_id)
        backup.files[env_file] = env_file.read_text()

        # Backup identities.db
        identities_db = Path(f"/home/saulo/Gestor-IA/instances/{self.instance_id}/identities.db")
        backup.files[identities_db] = identities_db.read_bytes()

        return backup

    def change(self, backup: BackupInfo) -> RotationResult:
        result = RotationResult(success=True, message="")

        print("    Instrucciones:")
        print("    1. Accede a Dolibarr UI: http://localhost:8081")
        print("    2. Login como admin")
        print("    3. Usuarios → Editar usuario → Regenerar clave API")
        print("    4. Copia la nueva clave API")

        new_api_key = prompt_secret(
            "Nueva Dolibarr API Key",
            hidden=False,
            validator=lambda k: len(k) >= 32
        )

        api_key_key = f"DOLIBARR_API_KEY_{self.instance_id.upper()}"
        env_file = get_instance_env_path(self.instance_id)

        changed = update_env_file(env_file, {api_key_key: new_api_key}, backup)
        result.changed_files.extend(changed)

        # Update identities.db
        if not self.dry_run:
            self._update_identities_db(new_api_key)
        result.changed_files.append(Path(f"instances/{self.instance_id}/identities.db"))

        result.message = f"Dolibarr API key rotated for {self.instance_id} user {self.dolibarr_user_id}"
        return result

    def _update_identities_db(self, new_api_key: str) -> None:
        """Update the API key in identities.db for the configured user."""
        import sqlite3

        identities_db = Path(f"/home/saulo/Gestor-IA/instances/{self.instance_id}/identities.db")

        with sqlite3.connect(identities_db) as conn:
            # Find the telegram_user_id linked to this dolibarr_user_id
            cursor = conn.execute(
                "SELECT telegram_user_id FROM telegram_identities WHERE dolibarr_user_id = ?",
                (self.dolibarr_user_id,)
            )
            row = cursor.fetchone()

            if row:
                telegram_user_id = row[0]
                conn.execute(
                    "UPDATE telegram_identities SET dolibarr_api_key = ? WHERE telegram_user_id = ?",
                    (new_api_key, telegram_user_id)
                )
                conn.commit()
                print(f"    ✅ Actualizado identities.db para telegram_user_id={telegram_user_id}")
            else:
                print(f"    ⚠️  No se encontró identidad para dolibarr_user_id={self.dolibarr_user_id}")

    def validate(self, result: RotationResult) -> None:
        # Validate new API key with Dolibarr REST
        env_file = get_instance_env_path(self.instance_id)
        env = load_env_file(env_file)
        new_api_key = env[f"DOLIBARR_API_KEY_{self.instance_id.upper()}"]

        print("    Validando nueva API key con Dolibarr REST...")
        res = run_cmd([
            "curl", "-s", "-H", f"DOLAPIKEY: {new_api_key}",
            "http://localhost:8081/api/index.php/users/1"
        ], check=False, sudo=False)

        if res.returncode != 0 or '"error"' in res.stdout:
            raise ValidationError(f"API key inválida: {res.stdout[:200]}")

        print("    ✅ API key válida en Dolibarr")

        # Verify identities.db has the new key
        identities_db = Path(f"/home/saulo/Gestor-IA/instances/{self.instance_id}/identities.db")
        import sqlite3
        with sqlite3.connect(identities_db) as conn:
            cursor = conn.execute(
                "SELECT dolibarr_api_key FROM telegram_identities WHERE dolibarr_user_id = ?",
                (self.dolibarr_user_id,)
            )
            row = cursor.fetchone()
            if row and row[0] == new_api_key:
                print("    ✅ identities.db sincronizado")
            else:
                raise ValidationError("identities.db no tiene la nueva API key")

    def post_check(self, result: RotationResult) -> None:
        # Test Hermes can use it
        pass


from datetime import datetime