"""Rotate Dolibarr Database User Password."""

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
    generate_password,
)


class DolibarrDbPasswordRotation(RotationBase):
    """Rotate Dolibarr database user password."""

    def __init__(self, instance_id: str, dry_run: bool = False, verbose: bool = False):
        super().__init__(instance_id, dry_run, verbose)
        if not instance_id:
            raise ValueError("instance_id required")

    @property
    def name(self) -> str:
        return f"Dolibarr DB Password ({self.instance_id})"

    @property
    def description(self) -> str:
        return "Rotate Dolibarr database user password. Updates MariaDB and instance.env/Dolibarr conf.php."

    def preflight(self) -> None:
        # Check instance exists
        env_file = get_instance_env_path(self.instance_id)
        if not env_file.exists():
            raise PreflightError(f"Instance not found: {self.instance_id}")

        env = load_env_file(env_file)
        db_pass_key = f"DOLIBARR_DB_PASSWORD_{self.instance_id.upper()}"
        if db_pass_key not in env:
            raise PreflightError(f"No DB password found in {env_file}")

        # Check MariaDB is accessible with root (unix_socket)
        res = run_cmd(["mariadb", "-u", "root", "-e", "SELECT 1"], check=False, sudo=True)
        if res.returncode != 0:
            raise PreflightError("No se puede acceder a MariaDB como root (unix_socket)")

        # Check Dolibarr DB user exists
        db_name = f"dolibarr_{self.instance_id}"
        db_user = f"dolibarr_{self.instance_id}"
        res = run_cmd([
            "mariadb", "-u", "root", "-e",
            f"SELECT User, Host FROM mysql.global_priv WHERE User='{db_user}'"
        ], check=False, sudo=True)
        if db_user not in res.stdout:
            raise PreflightError(f"Usuario DB {db_user} no existe")

        print(f"    Instance: {self.instance_id}")
        print(f"    DB: {db_name}")
        print(f"    User: {db_user}")
        print(f"    Current password: {env[db_pass_key][:10]}...")

    def backup(self) -> BackupInfo:
        backup = BackupInfo(timestamp=datetime.now().isoformat())

        # Backup instance.env
        env_file = get_instance_env_path(self.instance_id)
        backup.files[env_file] = env_file.read_text()

        # Backup Dolibarr conf.php
        conf_path = Path(f"/var/www/dolibarr/{self.instance_id}/htdocs/conf/conf.php")
        if conf_path.exists():
            backup.files[conf_path] = conf_path.read_text()

        # Backup current DB password hash (for reference)
        db_user = f"dolibarr_{self.instance_id}"
        res = run_cmd([
            "mariadb", "-u", "root", "-e",
            f"SELECT User, Host, JSON_EXTRACT(Priv, '$.authentication_string') FROM mysql.global_priv WHERE User='{db_user}'"
        ], check=False, sudo=True)
        backup.db_state["db_user_priv"] = res.stdout

        return backup

    def change(self, backup: BackupInfo) -> RotationResult:
        result = RotationResult(success=True, message="")

        # Generate new password
        print("    Generando nueva contraseña segura...")
        new_password = generate_password(32)
        print(f"    Nueva contraseña generada (guárdala): {new_password}")

        db_name = f"dolibarr_{self.instance_id}"
        db_user = f"dolibarr_{self.instance_id}"

        # Update MariaDB
        if not self.dry_run:
            print("    Actualizando MariaDB...")
            cmd = f"ALTER USER '{db_user}'@'%' IDENTIFIED BY '{new_password}'; FLUSH PRIVILEGES;"
            res = run_cmd(["mariadb", "-u", "root", "-e", cmd], check=False, sudo=True)
            if res.returncode != 0:
                raise Exception(f"MariaDB update failed: {res.stderr}")

        # Update instance.env
        db_pass_key = f"DOLIBARR_DB_PASSWORD_{self.instance_id.upper()}"
        env_file = get_instance_env_path(self.instance_id)
        changed = update_env_file(env_file, {db_pass_key: new_password}, backup)
        result.changed_files.extend(changed)

        # Update Dolibarr conf.php
        conf_path = Path(f"/var/www/dolibarr/{self.instance_id}/htdocs/conf/conf.php")
        if conf_path.exists():
            if not self.dry_run:
                content = conf_path.read_text()
                # Replace password line
                lines = content.splitlines()
                for i, line in enumerate(lines):
                    if line.strip().startswith("$dolibarr_main_db_pass"):
                        lines[i] = f"$dolibarr_main_db_pass='{new_password}';"
                        break
                conf_path.write_text("\n".join(lines) + "\n")
            result.changed_files.append(conf_path)

        result.message = f"Dolibarr DB password rotated for {self.instance_id}"
        return result

    def validate(self, result: RotationResult) -> None:
        if self.dry_run:
            print("    [DRY RUN] Saltando validación de conexión real")
            return

        # Test new password works
        env_file = get_instance_env_path(self.instance_id)
        env = load_env_file(env_file)
        new_password = env[f"DOLIBARR_DB_PASSWORD_{self.instance_id.upper()}"]
        db_user = f"dolibarr_{self.instance_id}"
        db_name = f"dolibarr_{self.instance_id}"

        print("    Probando nueva contraseña en MariaDB...")
        res = run_cmd([
            "mariadb", "-u", db_user, f"-p{new_password}", "-h", "127.0.0.1", "-e", "SELECT 1"
        ], check=False, sudo=False)

        if res.returncode != 0:
            raise ValidationError(f"Nueva contraseña no funciona: {res.stderr}")

        print("    ✅ Conexión MariaDB OK")

        # Test Dolibarr web
        print("    Probando Dolibarr web...")
        res = run_cmd(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:8081/"], check=False)
        if res.returncode != 0 or res.stdout != "200":
            raise ValidationError("Dolibarr web no responde tras cambio")

        print("    ✅ Dolibarr web OK")

    def post_check(self, result: RotationResult) -> None:
        # Test Dolibarr REST
        pass


from datetime import datetime