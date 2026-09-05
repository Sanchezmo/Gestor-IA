#!/usr/bin/env python3
"""
Script interactivo para rotación de secretos de Gestor-IA.

- Pide secretos EXTERNOS (Telegram token desde BotFather)
- Genera secretos INTERNOS automáticamente (passwords, keys)
- Actualiza .env e instances/*/instance.env
- Opcional: reinicia servicios y verifica
"""

import os
import secrets
import subprocess
import sys
from pathlib import Path
from getpass import getpass


PROJECT_ROOT = Path(__file__).parent.parent


def run(cmd: list[str], check=True) -> subprocess.CompletedProcess:
    """Ejecutar comando y retornar resultado."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def generate_password(length: int = 32) -> str:
    """Generar password seguro (sin caracteres que rompan URLs)."""
    # Evitar: @ : / ? # [ ] @ % + = & ' " ` $ ! * ( ) , ; 
    # Estos rompen URLs de conexión: mysql://user:pass@host
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_fernet_key() -> str:
    """Generar clave Fernet."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def prompt_external(prompt: str, hidden: bool = True) -> str:
    """Pedir secreto externo al usuario."""
    if hidden:
        return getpass(f"{prompt}: ").strip()
    return input(f"{prompt}: ").strip()


def confirm(prompt: str) -> bool:
    """Confirmación sí/no."""
    resp = input(f"{prompt} [s/N]: ").strip().lower()
    return resp in ("s", "si", "sí", "y", "yes")


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Actualizar archivo .env manteniendo comentarios y formato."""
    lines = []
    if path.exists():
        with path.open() as f:
            lines = f.readlines()

    # Mapa de claves existentes
    existing = {}
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped and not line_stripped.startswith("#") and "=" in line_stripped:
            key, _ = line_stripped.split("=", 1)
            existing[key] = i

    # Actualizar o añadir
    for key, value in updates.items():
        if key in existing:
            lines[existing[key]] = f"{key}={value}\n"
        else:
            lines.append(f"{key}={value}\n")

    with path.open("w") as f:
        f.writelines(lines)
    print(f"  ✅ {path}")


def main():
    print("=" * 60)
    print("  ROTACIÓN DE SECRETOS - Gestor-IA")
    print("=" * 60)
    print()

    if not confirm("¿Continuar con la rotación de secretos?"):
        print("Cancelado.")
        return

    # ============================================================
    # 1. SECRETOS EXTERNOS (usuario debe proporcionar)
    # ============================================================
    print("\n📋 SECRETOS EXTERNOS (proporciona tú):")
    print("-" * 40)

    telegram_token = prompt_external("Nuevo Telegram Bot Token (de BotFather /revoke)")
    if not telegram_token or ":" not in telegram_token:
        print("❌ Token inválido (debe contener ':')")
        sys.exit(1)

    # ============================================================
    # 2. GENERAR SECRETOS INTERNOS
    # ============================================================
    print("\n🔐 GENERANDO SECRETOS INTERNOS...")
    print("-" * 40)

    secrets_gen = {
        # MariaDB
        "MARIADB_ROOT_PASSWORD": generate_password(48),
        "DOLIBARR_DB_PASSWORD_DEVELOPMENT": generate_password(32),
        "DOLIBARR_DB_PASSWORD_EMPRESA_A": generate_password(32),

        # Redis
        "REDIS_PASSWORD": generate_password(32),

        # Security
        "JWT_SECRET_KEY": secrets.token_urlsafe(64),
        "FERNET_KEY": generate_fernet_key(),
        "GESTOR_IA_ADMIN_TOKEN": secrets.token_urlsafe(48),
        "BACKUP_ENCRYPTION_KEY": secrets.token_urlsafe(32),

        # Telegram
        "TELEGRAM_BOT_TOKEN_DEVELOPMENT": telegram_token,
        "TELEGRAM_BOT_TOKEN_EMPRESA_A": telegram_token,
        "TELEGRAM_WEBHOOK_SECRET_DEVELOPMENT": secrets.token_hex(32),
        "TELEGRAM_WEBHOOK_SECRET_EMPRESA_A": secrets.token_hex(32),
    }

    for k, v in secrets_gen.items():
        print(f"  ✅ {k}")

    # ============================================================
    # 3. DOLIBARR API KEYS (requieren regeneración manual en UI)
    # ============================================================
    print("\n📋 DOLIBARR API KEYS (regenera en Dolibarr UI):")
    print("-" * 40)
    print("  Ve a: http://localhost:8081 (admin/admin123)")
    print("  Usuarios → admin → Editar → Regenerar clave API")

    dolibarr_api_key_dev = prompt_external("Nueva Dolibarr API Key (development)", hidden=False)
    dolibarr_api_key_emp = prompt_external("Nueva Dolibarr API Key (empresa_a)", hidden=False)

    if not dolibarr_api_key_dev or not dolibarr_api_key_emp:
        print("❌ API keys requeridas")
        sys.exit(1)

    secrets_gen["DOLIBARR_API_KEY_DEVELOPMENT"] = dolibarr_api_key_dev
    secrets_gen["DOLIBARR_API_KEY_EMPRESA_A"] = dolibarr_api_key_emp

    # ============================================================
    # 4. CONFIRMACIÓN
    # ============================================================
    print("\n" + "=" * 60)
    print("RESUMEN DE CAMBIOS:")
    print("-" * 60)
    print(f"  .env                          → {len([k for k in secrets_gen if not k.startswith('DOLIBARR_') and not k.startswith('TELEGRAM_')])} secrets")
    print(f"  instances/development/instance.env → 4 secrets")
    print(f"  instances/empresa_a/instance.env  → 4 secrets")
    print()

    if not confirm("¿Aplicar cambios en archivos?"):
        print("Cancelado.")
        return

    # ============================================================
    # 5. ACTUALIZAR ARCHIVOS
    # ============================================================
    print("\n📝 ACTUALIZANDO ARCHIVOS...")
    print("-" * 40)

    # .env (infraestructura global)
    update_env_file(PROJECT_ROOT / ".env", {
        "MARIADB_ROOT_PASSWORD": secrets_gen["MARIADB_ROOT_PASSWORD"],
        "REDIS_PASSWORD": secrets_gen["REDIS_PASSWORD"],
        "JWT_SECRET_KEY": secrets_gen["JWT_SECRET_KEY"],
        "FERNET_KEY": secrets_gen["FERNET_KEY"],
        "GESTOR_IA_ADMIN_TOKEN": secrets_gen["GESTOR_IA_ADMIN_TOKEN"],
        "BACKUP_ENCRYPTION_KEY": secrets_gen["BACKUP_ENCRYPTION_KEY"],
        # Cloudflare token NO cambiar (verificar que no está en git)
    })

    # instances/development/instance.env
    update_env_file(PROJECT_ROOT / "instances/development/instance.env", {
        "DOLIBARR_DB_PASSWORD_DEVELOPMENT": secrets_gen["DOLIBARR_DB_PASSWORD_DEVELOPMENT"],
        "DOLIBARR_API_KEY_DEVELOPMENT": secrets_gen["DOLIBARR_API_KEY_DEVELOPMENT"],
        "TELEGRAM_BOT_TOKEN_DEVELOPMENT": secrets_gen["TELEGRAM_BOT_TOKEN_DEVELOPMENT"],
        "TELEGRAM_WEBHOOK_SECRET_DEVELOPMENT": secrets_gen["TELEGRAM_WEBHOOK_SECRET_DEVELOPMENT"],
    })

    # instances/empresa_a/instance.env
    update_env_file(PROJECT_ROOT / "instances/empresa_a/instance.env", {
        "DOLIBARR_DB_PASSWORD_EMPRESA_A": secrets_gen["DOLIBARR_DB_PASSWORD_EMPRESA_A"],
        "DOLIBARR_API_KEY_EMPRESA_A": secrets_gen["DOLIBARR_API_KEY_EMPRESA_A"],
        "TELEGRAM_BOT_TOKEN_EMPRESA_A": secrets_gen["TELEGRAM_BOT_TOKEN_EMPRESA_A"],
        "TELEGRAM_WEBHOOK_SECRET_EMPRESA_A": secrets_gen["TELEGRAM_WEBHOOK_SECRET_EMPRESA_A"],
    })

    # ============================================================
    # 6. ACTUALIZAR MARIADB
    # ============================================================
    print("\n🗄️  ACTUALIZANDO MARIADB...")
    print("-" * 40)

    mysql_cmds = [
        f"ALTER USER 'root'@'localhost' IDENTIFIED BY '{secrets_gen['MARIADB_ROOT_PASSWORD']}';",
        f"ALTER USER 'dolibarr_development'@'%' IDENTIFIED BY '{secrets_gen['DOLIBARR_DB_PASSWORD_DEVELOPMENT']}';",
        f"ALTER USER 'dolibarr_demo'@'%' IDENTIFIED BY '{secrets_gen['DOLIBARR_DB_PASSWORD_EMPRESA_A']}';",
        "FLUSH PRIVILEGES;",
    ]

    for cmd in mysql_cmds:
        result = run(["mysql", "-u", "root", "-p" + secrets_gen["MARIADB_ROOT_PASSWORD"], "-e", cmd], check=False)
        if result.returncode != 0:
            print(f"  ⚠️  {cmd[:50]}... (falló: {result.stderr.strip()[:80]})")
        else:
            print(f"  ✅ {cmd[:50]}...")

    # ============================================================
    # 7. ACTUALIZAR REDIS
    # ============================================================
    print("\n🔴 ACTUALIZANDO REDIS...")
    print("-" * 40)

    result = run(["redis-cli", "CONFIG", "SET", "requirepass", secrets_gen["REDIS_PASSWORD"]], check=False)
    if result.returncode == 0:
        print("  ✅ requirepass actualizado")
        run(["redis-cli", "CONFIG", "REWRITE"], check=False)
        print("  ✅ CONFIG REWRITE")
    else:
        print(f"  ⚠️  redis-cli falló: {result.stderr.strip()[:80]}")

    # ============================================================
    # 8. REINICIAR SERVICIOS
    # ============================================================
    print("\n🔄 REINICIANDO SERVICIOS...")
    print("-" * 40)

    for svc in ["mariadb", "redis-server", "hermes-gestor-ia"]:
        result = run(["sudo", "systemctl", "restart", svc], check=False)
        if result.returncode == 0:
            print(f"  ✅ {svc} reiniciado")
        else:
            print(f"  ⚠️  {svc}: {result.stderr.strip()[:80]}")

    # Esperar a que estén listos
    import time
    print("  ⏳ Esperando servicios...")
    time.sleep(5)

    # ============================================================
    # 9. VERIFICAR
    # ============================================================
    print("\n✅ VERIFICANDO...")
    print("-" * 40)

    # Healthcheck
    result = run([str(PROJECT_ROOT / ".venv/bin/python"), "-m", "core.hermes.cli", "healthcheck"], check=False)
    if result.returncode == 0:
        print("  ✅ Healthcheck OK")
    else:
        print(f"  ⚠️  Healthcheck: {result.stdout[-200:]}")

    # Dev status
    result = run(["make", "dev-status"], check=False)
    print(result.stdout)

    # ============================================================
    # 10. TEST E2E
    # ============================================================
    print("\n🧪 TEST E2E (/terceros via webhook)...")
    print("-" * 40)

    # Webhook secret actualizado
    webhook_secret_dev = secrets_gen["TELEGRAM_WEBHOOK_SECRET_DEVELOPMENT"]

    import hmac
    import hashlib
    test_body = '{"update_id":1,"message":{"message_id":1,"from":{"id":6136981104},"chat":{"id":6136981104},"date":1,"text":"/terceros"}}'
    sig = hmac.new(webhook_secret_dev.encode(), test_body.encode(), hashlib.sha256).hexdigest()

    import httpx
    try:
        resp = httpx.post(
            "https://telegram-staging.mascotalegal.com/webhook/development",
            json={
                "update_id": 99999,
                "message": {
                    "message_id": 1,
                    "from": {"id": 6136981104, "is_bot": False, "first_name": "Test"},
                    "chat": {"id": 6136981104, "type": "private"},
                    "date": 1700000000,
                    "text": "/terceros"
                }
            },
            headers={
                "Content-Type": "application/json",
                "X-Telegram-Bot-Api-Secret-Token": webhook_secret_dev
            },
            timeout=10
        )
        if resp.status_code == 200 and resp.json().get("success"):
            print("  ✅ E2E test PASSED")
        else:
            print(f"  ❌ E2E test FAILED: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"  ⚠️  E2E test error: {e}")

    # ============================================================
    # FIN
    # ============================================================
    print("\n" + "=" * 60)
    print("  ROTACIÓN COMPLETADA")
    print("=" * 60)
    print("\n⚠️  IMPORTANTE:")
    print("  1. Verifica que NO hay secretos en git: git status")
    print("  2. Si hay cambios en config.yml, NO commitearlos")
    print("  3. Haz commit solo de .gitignore / scripts si cambió")
    print("  4. Si todo OK → PASO 23: limpieza historial git")


if __name__ == "__main__":
    main()