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
