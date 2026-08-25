"""
CLI interno para gestión de instancias - Reemplaza lógica inline en Makefile.
Uso: python -m core.hermes.cli <command> [args]
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import mysql.connector
import redis
import yaml

from core.hermes.identity import TelegramIdentity
from core.hermes.identity_store import IdentityStore
from core.hermes.instance_config import clear_config_cache, list_instances, load_instance_config


def cmd_list_instances(project_root: Path) -> int:
    """Listar instancias configuradas."""
    instances_root = project_root / "instances"
    for iid in list_instances(instances_root):
        cfg = load_instance_config(iid, instances_root)
        if cfg:
            status = "active" if cfg.active else "inactive"
            print(f"  {cfg.instance_id:20s} | {cfg.company_name:30s} | {status} | {cfg.domains.base}")
    return 0


def cmd_instance_status(project_root: Path, instance_id: str) -> int:
    """Ver estado de una instancia."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"Instance '{instance_id}' not found")
        return 1

    print(f"Instance ID: {cfg.instance_id}")
    print(f"Company:     {cfg.company_name}")
    print(f"Active:      {cfg.active}")
    print(f"Dolibarr:    {cfg.dolibarr.internal_url} (DB: {cfg.database.name})")
    print(f"Telegram:    {cfg.telegram.webhook_path}")
    print(f"Domain:      {cfg.domains.base}")
    if cfg.domains.dolibarr:
        print(f"  Dolibarr:  {cfg.domains.dolibarr}")
    if cfg.domains.hermes:
        print(f"  Hermes:    {cfg.domains.hermes}")
    if cfg.enabled_agents:
        print(f"Agents:      {', '.join(cfg.enabled_agents)}")
    if cfg.enabled_workflows:
        print(f"Workflows:   {', '.join(cfg.enabled_workflows)}")
    if cfg.enabled_tools:
        print(f"Tools:       {', '.join(cfg.enabled_tools)}")
    return 0


def cmd_instance_enable(project_root: Path, instance_id: str) -> int:
    """Habilitar instancia."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"Instance '{instance_id}' not found")
        return 1

    cfg.active = True
    config_path = project_root / "instances" / instance_id / "config.yml"
    with config_path.open("w") as f:
        yaml.dump(cfg.model_dump(mode="json"), f, default_flow_style=False, sort_keys=False)

    clear_config_cache()
    print("Instance enabled")
    return 0


def cmd_instance_disable(project_root: Path, instance_id: str) -> int:
    """Deshabilitar instancia."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"Instance '{instance_id}' not found")
        return 1

    cfg.active = False
    config_path = project_root / "instances" / instance_id / "config.yml"
    with config_path.open("w") as f:
        yaml.dump(cfg.model_dump(mode="json"), f, default_flow_style=False, sort_keys=False)

    clear_config_cache()
    print("Instance disabled")
    return 0


def cmd_check_instance(project_root: Path, instance_id: str) -> int:
    """Verificar configuración de instancia (DB, Redis, Dolibarr API)."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"ERROR: Instance '{instance_id}' not found")
        return 1

    errors = []

    # Verificar MariaDB
    try:
        conn = mysql.connector.connect(
            host=cfg.database.host,
            port=cfg.database.port,
            user=cfg.database.user,
            password=cfg.database.password,
            database=cfg.database.name,
        )
        conn.close()
        print("OK: MariaDB connection")
    except Exception as e:
        errors.append(f"MariaDB: {e}")

    # Verificar Redis (opcional)
    try:
        r = redis.Redis(host="127.0.0.1", port=6379, db=cfg.get_redis_db(), decode_responses=True)
        r.ping()
        r.close()
        print("OK: Redis connection")
    except Exception as e:
        errors.append(f"Redis: {e}")

    # Verificar Dolibarr API
    try:

        async def check():
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(
                    f"{cfg.dolibarr.internal_url}/api/index.php/thirdparties?limit=1",
                    headers={"DOLAPIKEY": cfg.dolibarr.api_key},
                )
                r.raise_for_status()

        asyncio.run(check())
        print("OK: Dolibarr API")
    except Exception as e:
        errors.append(f"Dolibarr API: {e}")

    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        return 1

    print("All checks passed")
    return 0


# =========================================================================
# USER MANAGEMENT COMMANDS
# =========================================================================


def cmd_user_link(
    project_root: Path,
    instance_id: str,
    telegram_user: int,
    dolibarr_user: int,
) -> int:
    """Vincular usuario Telegram a usuario Dolibarr."""
    # Validate instance exists
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"ERROR: Instance '{instance_id}' not found")
        return 1

    # Validate Dolibarr user exists via API
    try:

        async def check_dolibarr_user():
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{cfg.dolibarr.internal_url}/api/index.php/users/{dolibarr_user}",
                    headers={"DOLAPIKEY": cfg.dolibarr.api_key},
                )
                r.raise_for_status()
                return r.json()

        user_data = asyncio.run(check_dolibarr_user())
        if not user_data:
            print(f"ERROR: Dolibarr user {dolibarr_user} not found")
            return 1
        print(f"OK: Dolibarr user '{user_data.get('login', dolibarr_user)}' exists")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            print(f"ERROR: Dolibarr user {dolibarr_user} not found")
            return 1
        print(f"ERROR: Dolibarr API error: {e}")
        return 1
    except Exception as e:
        print(f"ERROR: Dolibarr API connection failed: {e}")
        return 1

    # Create identity in SQLite
    store = IdentityStore(instance_id, project_root / "instances")
    if store.exists(telegram_user):
        print(f"ERROR: Telegram user {telegram_user} already linked in {instance_id}")
        return 1

    if store.get_by_dolibarr_user(dolibarr_user):
        print(f"ERROR: Dolibarr user {dolibarr_user} already linked in {instance_id}")
        return 1

    identity = TelegramIdentity(
        instance_id=instance_id,
        telegram_user_id=telegram_user,
        dolibarr_user_id=dolibarr_user,
        enabled=True,
        created_at=datetime.now(UTC),
    )
    store.create(identity)

    print(f"OK: Linked Telegram user {telegram_user} -> Dolibarr user {dolibarr_user} in {instance_id}")
    return 0


def cmd_user_unlink(project_root: Path, instance_id: str, telegram_user: int) -> int:
    """Desvincular usuario Telegram."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"ERROR: Instance '{instance_id}' not found")
        return 1

    store = IdentityStore(instance_id, project_root / "instances")
    if not store.exists(telegram_user):
        print(f"ERROR: Telegram user {telegram_user} not linked in {instance_id}")
        return 1

    store.delete(telegram_user)
    print(f"OK: Unlinked Telegram user {telegram_user} from {instance_id}")
    return 0


def cmd_user_enable(project_root: Path, instance_id: str, telegram_user: int) -> int:
    """Habilitar identidad de usuario."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"ERROR: Instance '{instance_id}' not found")
        return 1

    store = IdentityStore(instance_id, project_root / "instances")
    if not store.exists(telegram_user):
        print(f"ERROR: Telegram user {telegram_user} not linked in {instance_id}")
        return 1

    store.set_enabled(telegram_user, True)
    print(f"OK: Enabled Telegram user {telegram_user} in {instance_id}")
    return 0


def cmd_user_disable(project_root: Path, instance_id: str, telegram_user: int) -> int:
    """Deshabilitar identidad de usuario."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"ERROR: Instance '{instance_id}' not found")
        return 1

    store = IdentityStore(instance_id, project_root / "instances")
    if not store.exists(telegram_user):
        print(f"ERROR: Telegram user {telegram_user} not linked in {instance_id}")
        return 1

    store.set_enabled(telegram_user, False)
    print(f"OK: Disabled Telegram user {telegram_user} in {instance_id}")
    return 0


def cmd_user_list(project_root: Path, instance_id: str) -> int:
    """Listar usuarios vinculados en una instancia."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"ERROR: Instance '{instance_id}' not found")
        return 1

    store = IdentityStore(instance_id, project_root / "instances")
    identities = store.list_all()

    if not identities:
        print(f"No users linked in {instance_id}")
        return 0

    print(f"Users in {instance_id} ({cfg.company_name}):")
    print(f"  {'Telegram ID':>15} | {'Dolibarr ID':>12} | {'Enabled':>7} | {'Created':>19} | {'Last Seen':>19}")
    print(f"  {'-' * 15} | {'-' * 12} | {'-' * 7} | {'-' * 19} | {'-' * 19}")
    for identity in identities:
        created = identity.created_at.strftime("%Y-%m-%d %H:%M:%S") if identity.created_at else "N/A"
        last_seen = identity.last_seen_at.strftime("%Y-%m-%d %H:%M:%S") if identity.last_seen_at else "N/A"
        enabled = "yes" if identity.enabled else "no"
        tg_id = f"{identity.telegram_user_id:>15}"
        dol_id = f"{identity.dolibarr_user_id:>12}"
        en = f"{enabled:>7}"
        cr = f"{created:>19}"
        ls = f"{last_seen:>19}"
        print(f"  {tg_id} | {dol_id} | {en} | {cr} | {ls}")
    return 0


def cmd_user_show(project_root: Path, instance_id: str, telegram_user: int) -> int:
    """Mostrar detalles de un usuario vinculado."""
    cfg = load_instance_config(instance_id, project_root / "instances")
    if not cfg:
        print(f"ERROR: Instance '{instance_id}' not found")
        return 1

    store = IdentityStore(instance_id, project_root / "instances")
    identity = store.get(telegram_user)
    if not identity:
        print(f"ERROR: Telegram user {telegram_user} not linked in {instance_id}")
        return 1

    print(f"Identity for Telegram user {telegram_user} in {instance_id}:")
    print(f"  Instance:       {identity.instance_id}")
    print(f"  Telegram ID:    {identity.telegram_user_id}")
    print(f"  Dolibarr ID:    {identity.dolibarr_user_id}")
    print(f"  Enabled:        {'yes' if identity.enabled else 'no'}")
    print(f"  Created:        {identity.created_at.isoformat() if identity.created_at else 'N/A'}")
    print(f"  Last Seen:      {identity.last_seen_at.isoformat() if identity.last_seen_at else 'N/A'}")
    if identity.username_cache:
        print(f"  Username:       @{identity.username_cache}")
    if identity.first_name_cache or identity.last_name_cache:
        name = f"{identity.first_name_cache or ''} {identity.last_name_cache or ''}".strip()
        print(f"  Name:           {name}")

    # Try to fetch Dolibarr user info
    try:

        async def get_dolibarr_user():
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    f"{cfg.dolibarr.internal_url}/api/index.php/users/{identity.dolibarr_user_id}?includepermissions=1",
                    headers={"DOLAPIKEY": cfg.dolibarr.api_key},
                )
                r.raise_for_status()
                return r.json()

        user_data = asyncio.run(get_dolibarr_user())
        if user_data:
            print("\nDolibarr User Info:")
            print(f"  Login:          {user_data.get('login', 'N/A')}")
            print(f"  Name:           {user_data.get('firstname', '')} {user_data.get('lastname', '')}".strip())
            print(f"  Email:          {user_data.get('email', 'N/A')}")
            print(f"  Active:         {'yes' if user_data.get('status') else 'no'}")
            if user_data.get("rights"):
                print(f"  Permissions:    {len(user_data['rights'])} modules")
    except Exception:
        pass  # Silently ignore Dolibarr fetch errors

    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python -m core.hermes.cli <command> [args]")
        print("Comandos:")
        print("  list-instances")
        print("  instance-status <instance_id>")
        print("  instance-enable <instance_id>")
        print("  instance-disable <instance_id>")
        print("  check-instance <instance_id>")
        print("  user-link --instance <instance_id> --telegram-user <id> --dolibarr-user <id>")
        print("  user-unlink --instance <instance_id> --telegram-user <id>")
        print("  user-enable --instance <instance_id> --telegram-user <id>")
        print("  user-disable --instance <instance_id> --telegram-user <id>")
        print("  user-list --instance <instance_id>")
        print("  user-show --instance <instance_id> --telegram-user <id>")
        return 1

    command = sys.argv[1]
    from core.hermes.utils import get_project_root

    project_root = get_project_root()

    if command == "list-instances":
        return cmd_list_instances(project_root)
    elif command == "instance-status":
        if len(sys.argv) < 3:
            print("ERROR: Especificar instance_id")
            return 1
        return cmd_instance_status(project_root, sys.argv[2])
    elif command == "instance-enable":
        if len(sys.argv) < 3:
            print("ERROR: Especificar instance_id")
            return 1
        return cmd_instance_enable(project_root, sys.argv[2])
    elif command == "instance-disable":
        if len(sys.argv) < 3:
            print("ERROR: Especificar instance_id")
            return 1
        return cmd_instance_disable(project_root, sys.argv[2])
    elif command == "check-instance":
        if len(sys.argv) < 3:
            print("ERROR: Especificar instance_id")
            return 1
        return cmd_check_instance(project_root, sys.argv[2])
    elif command == "user-link":
        if len(sys.argv) < 7:
            print("ERROR: user-link requiere --instance, --telegram-user, --dolibarr-user")
            return 1
        args = dict(zip(sys.argv[2::2], sys.argv[3::2], strict=False))
        instance_id = args.get("--instance")
        telegram_user = int(args.get("--telegram-user", 0))
        dolibarr_user = int(args.get("--dolibarr-user", 0))
        if not instance_id or not telegram_user or not dolibarr_user:
            print("ERROR: Faltan argumentos requeridos")
            return 1
        return cmd_user_link(project_root, instance_id, telegram_user, dolibarr_user)
    elif command == "user-unlink":
        if len(sys.argv) < 5:
            print("ERROR: user-unlink requiere --instance, --telegram-user")
            return 1
        args = dict(zip(sys.argv[2::2], sys.argv[3::2], strict=False))
        instance_id = args.get("--instance")
        telegram_user = int(args.get("--telegram-user", 0))
        if not instance_id or not telegram_user:
            print("ERROR: Faltan argumentos requeridos")
            return 1
        return cmd_user_unlink(project_root, instance_id, telegram_user)
    elif command == "user-enable":
        if len(sys.argv) < 5:
            print("ERROR: user-enable requiere --instance, --telegram-user")
            return 1
        args = dict(zip(sys.argv[2::2], sys.argv[3::2], strict=False))
        instance_id = args.get("--instance")
        telegram_user = int(args.get("--telegram-user", 0))
        if not instance_id or not telegram_user:
            print("ERROR: Faltan argumentos requeridos")
            return 1
        return cmd_user_enable(project_root, instance_id, telegram_user)
    elif command == "user-disable":
        if len(sys.argv) < 5:
            print("ERROR: user-disable requiere --instance, --telegram-user")
            return 1
        args = dict(zip(sys.argv[2::2], sys.argv[3::2], strict=False))
        instance_id = args.get("--instance")
        telegram_user = int(args.get("--telegram-user", 0))
        if not instance_id or not telegram_user:
            print("ERROR: Faltan argumentos requeridos")
            return 1
        return cmd_user_disable(project_root, instance_id, telegram_user)
    elif command == "user-list":
        if len(sys.argv) < 3:
            print("ERROR: user-list requiere --instance")
            return 1
        args = dict(zip(sys.argv[2::2], sys.argv[3::2], strict=False))
        instance_id = args.get("--instance")
        if not instance_id:
            print("ERROR: Falta --instance")
            return 1
        return cmd_user_list(project_root, instance_id)
    elif command == "healthcheck":
        return cmd_healthcheck(project_root)
    elif command == "user-show":
        if len(sys.argv) < 5:
            print("ERROR: user-show requiere --instance, --telegram-user")
            return 1
        args = dict(zip(sys.argv[2::2], sys.argv[3::2], strict=False))
        instance_id = args.get("--instance")
        telegram_user = int(args.get("--telegram-user", 0))
        if not instance_id or not telegram_user:
            print("ERROR: Faltan argumentos requeridos")
            return 1
        return cmd_user_show(project_root, instance_id, telegram_user)
    else:
        print(f"Comando desconocido: {command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

# =========================================================================
# HEALTHCHECK COMMAND
# =========================================================================


def _run_cmd(cmd: str, timeout: int = 10) -> tuple[bool, str]:
    """Run shell command and return (success, output)."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        return result.returncode == 0, result.stdout.decode().strip()
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def cmd_healthcheck(project_root: Path) -> int:
    """Healthcheck completo de todos los servicios del sistema."""
    import subprocess
    
    print("==========================================")
    print("  HEALTHCHECK GESTOR-IA")
    print("==========================================")
    print("")
    
    errors = 0
    warnings = 0
    
    def _run_cmd_local(cmd: str, timeout: int = 10) -> tuple[bool, str]:
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
            if result.returncode == 0:
                return True, result.stdout.decode().strip()
            else:
                return False, result.stderr.decode().strip()
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT"
        except Exception as e:
            return False, str(e)
    
    def check(cmd: str, desc: str, critical: bool = True) -> bool:
        nonlocal errors, warnings
        success, output = _run_cmd_local(cmd)
        if success:
            print(f"[OK] {desc}")
            return True
        else:
            detail = output.strip() if output else "(no output)"
            msg = f"[FAIL] {desc}: {detail}"
            if critical:
                errors += 1
                print(msg)
            else:
                warnings += 1
                print(f"[WARN] {desc}: {detail}")
            return False
    
    def warn(cmd: str, desc: str) -> bool:
        nonlocal warnings
        success, output = _run_cmd_local(cmd)
        if success:
            print(f"[OK] {desc}")
            return True
        else:
            warnings += 1
            detail = output.strip() if output else "(no output)"
            print(f"[WARN] {desc}: {detail}")
            return False
    
    print("")
    print("=== Sistema base ===")
    check("command -v python3", "Python3 instalado")
    check("command -v mysql", "MariaDB client instalado")
    check("command -v redis-cli", "Redis client instalado")
    check("command -v curl", "curl instalado")
    
    print("")
    print("=== Entorno Python ===")
    if Path(project_root / ".venv").exists():
        print("[OK] Virtualenv existe")
        check(f"{project_root}/.venv/bin/python -c \"import fastapi, pydantic, httpx, sqlalchemy, redis, structlog, yaml\"", "Dependencias core instaladas")
    else:
        print("[FAIL] Virtualenv no encontrado")
        errors += 1
    
    print("")
    print("=== Configuración ===")
    check(f"test -f {project_root}/.env", "Archivo .env existe")
    
    print("")
    print("=== Servicios ===")
    for svc in ["mariadb", "redis", "ollama"]:
        warn(f"systemctl is-active --quiet {svc}", f"{svc}: systemd service")
    
    print("")
    print("=== Conectividad bases de datos ===")
    check("redis-cli -a ***REMOVED*** ping", "Redis: CONEXIÓN OK")
    
    print("")
    print("=== Ollama ===")
    if not check("curl -sf http://127.0.0.1:11434/api/tags", "Ollama API: RESPONDE", critical=False):
        warnings += 1
        print("[WARN] Ollama API: NO RESPONDE")
    
    print("")
    print("=== MariaDB ===")
    if not check("mysqladmin -u root -p***REMOVED*** ping", "MariaDB: CONEXIÓN OK", critical=False):
        warnings += 1
        print("[WARN] MariaDB: CONEXIÓN FALLÓ (¿docker compose up?)")
    
    print("")
    print("=== Hermes API ===")
    if not check("curl -sf -o /dev/null http://localhost:8000/health", "Hermes API: HEALTHY", critical=False):
        warnings += 1
        print("[WARN] Hermes API: NO RESPONDE (¿make dev-start?)")
    
    print("")
    print("=== Dolibarr ERP ===")
    if not check("curl -sf -o /dev/null http://localhost:8081/index.php", "Dolibarr: ACCESIBLE", critical=False):
        warnings += 1
        print("[WARN] Dolibarr: NO ACCESIBLE (¿docker compose up?)")
    else:
        if not check("curl -sf -o /dev/null -H 'DOLAPIKEY: demo_dolibarr_api_key_123' 'http://localhost:8081/api/index.php/thirdparties?limit=1'", "Dolibarr API: OK", critical=False):
            warnings += 1
            print("[WARN] Dolibarr API: NO RESPONDE")
    
    print("")
    print("=== Instancias ===")
    instances_root = project_root / "instances"
    for instance_dir in instances_root.iterdir():
        if instance_dir.is_dir() and (instance_dir / "config.yml").exists():
            instance = instance_dir.name
            print(f"Instancia: {instance}")
            if (instance_dir / "instance.env").exists():
                print("  instance.env: EXISTE")
            else:
                print("  instance.env: FALTA")
                warnings += 1
    
    print("")
    print("==========================================")
    print("  RESUMEN")
    print("==========================================")
    if errors == 0 and warnings == 0:
        print("[OK] TODO OK - Sistema listo para demo")
        return 0
    elif errors == 0:
        print(f"[WARN] {warnings} advertencias - Sistema funcional pero revisar opcionales")
        return 0
    else:
        print(f"[FAIL] {errors} errores críticos - CORREGIR ANTES DE DEMO")
        print(f"Advertencias: {warnings}")
        return 1

