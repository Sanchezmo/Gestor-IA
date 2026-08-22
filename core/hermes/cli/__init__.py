"""
CLI interno para gestión de instancias - Reemplaza lógica inline en Makefile.
Uso: python -m core.hermes.cli <command> [args]
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import mysql.connector
import redis
import yaml

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


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python -m core.hermes.cli <command> [args]")
        print("Comandos:")
        print("  list-instances")
        print("  instance-status <instance_id>")
        print("  instance-enable <instance_id>")
        print("  instance-disable <instance_id>")
        print("  check-instance <instance_id>")
        return 1

    command = sys.argv[1]
    project_root = Path.cwd()

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
    else:
        print(f"Comando desconocido: {command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
