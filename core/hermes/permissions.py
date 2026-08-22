"""
Centralized Dolibarr permission merging utilities.

Provides a single implementation for merging Dolibarr rights structures
to avoid duplication across the codebase.
"""

from __future__ import annotations

import copy
from typing import Any


def merge_dolibarr_permissions(base: dict[str, Any], additional: dict[str, Any]) -> dict[str, Any]:
    """
    Merge two Dolibarr rights dictionaries (user + group permissions).

    Group permissions are additive (OR logic - take max level).

    Args:
        base: Base permissions dict (typically user's direct permissions)
        additional: Additional permissions to merge (typically group permissions)

    Returns:
        Merged permissions dictionary with deduplicated keys and max levels
    """
    result = copy.deepcopy(base)

    def _merge_dict(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if key not in target:
                target[key] = copy.deepcopy(value)
            elif isinstance(target[key], dict) and isinstance(value, dict):
                _merge_dict(target[key], value)
            elif isinstance(target[key], (int, float, bool)) and isinstance(value, (int, float, bool)):
                # Take max level (OR logic for permissions)
                target[key] = max(target[key], value)
            # Ignore incompatible types silently (default deny)

    _merge_dict(result, additional)
    return result


def flatten_dolibarr_permissions(rights: dict[str, Any]) -> frozenset[str]:
    """
    Convert Dolibarr rights dict to flat permission strings.

    Dolibarr format: {module: {submodule: {permission: level}}}
    Output format: "module.submodule.permission"

    Only includes permissions with truthy levels (default deny).
    """
    permissions: set[str] = set()

    def _traverse(obj: Any, prefix: str = "") -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                new_prefix = f"{prefix}.{key}" if prefix else key
                _traverse(value, new_prefix)
        elif isinstance(obj, (int, float, bool)):
            # Leaf node with permission level - include if truthy
            if obj:
                permissions.add(prefix)
        # Ignore other types (lists, None, etc.)

    _traverse(rights)
    return frozenset(permissions)
