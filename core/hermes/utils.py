"""
Utilidad central para resolución robusta de PROJECT_ROOT.

Estrategia de prioridad:
1. Variable de entorno GESTOR_IA_ROOT (explícita, máxima prioridad)
2. Resolución desde __file__ del paquete (cuando se ejecuta como módulo)
3. Path.cwd() como fallback (solo para compatibilidad)

NO usar Path.cwd() como única fuente de verdad.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_project_root() -> Path:
    """
    Resolver PROJECT_ROOT de forma robusta e independiente del cwd.

    Prioridad:
    1. GESTOR_IA_ROOT env var (explícita)
    2. Resolución desde ubicación del paquete core/hermes
    3. Path.cwd() como último recurso

    Returns:
        Path absoluto al root del proyecto Gestor-IA
    """
    # 1. Variable de entorno explícita (máxima prioridad)
    env_root = os.getenv("GESTOR_IA_ROOT")
    if env_root:
        return Path(env_root).expanduser().resolve()

    # 2. Resolución desde __file__ del paquete
    # Este archivo está en: core/hermes/utils.py
    # PROJECT_ROOT = parents[3] (core/hermes/utils.py -> core/hermes -> core -> root)
    try:
        package_root = Path(__file__).resolve().parents[3]
        # Validar que parece un root válido (tiene directorio instances/, core/, etc.)
        if (package_root / "core").exists() and (package_root / "instances").exists():
            return package_root
    except Exception:
        pass

    # 3. Fallback a cwd (compatibilidad, pero no como única fuente)
    return Path.cwd().resolve()


def get_instances_root(project_root: Path | None = None) -> Path:
    """
    Obtener directorio de instancias.

    Args:
        project_root: Root del proyecto (si None, se resuelve automáticamente)

    Returns:
        Path al directorio instances/
    """
    if project_root is None:
        project_root = get_project_root()
    return project_root / "instances"


def get_global_env_path(project_root: Path | None = None) -> Path:
    """
    Obtener ruta al archivo .env global.

    Args:
        project_root: Root del proyecto (si None, se resuelve automáticamente)

    Returns:
        Path al archivo .env
    """
    if project_root is None:
        project_root = get_project_root()
    return project_root / ".env"
