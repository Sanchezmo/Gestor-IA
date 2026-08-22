"""
Identity models for multi-user support.

TelegramIdentity: External identity linking (Telegram -> Dolibarr user)
UserContext: Authenticated user context within a company (CompanyContext + Dolibarr user + permissions)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class TelegramIdentity:
    """
    Links a Telegram user to a Dolibarr user within a specific instance.

    Compound key: (instance_id, telegram_user_id)
    Unique constraint: (instance_id, dolibarr_user_id) for 1:1 mapping
    """

    instance_id: str
    telegram_user_id: int
    dolibarr_user_id: int
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen_at: datetime | None = None
    username_cache: str | None = None
    first_name_cache: str | None = None
    last_name_cache: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> TelegramIdentity:
        """Create from database row."""
        # created_at is NOT NULL in schema, so _parse_datetime will return datetime
        created_at = _parse_datetime(row["created_at"])
        assert created_at is not None
        return cls(
            instance_id=row["instance_id"],
            telegram_user_id=row["telegram_user_id"],
            dolibarr_user_id=row["dolibarr_user_id"],
            enabled=bool(row["enabled"]),
            created_at=created_at,
            last_seen_at=_parse_datetime(row["last_seen_at"]) if row.get("last_seen_at") else None,
            username_cache=row.get("username_cache"),
            first_name_cache=row.get("first_name_cache"),
            last_name_cache=row.get("last_name_cache"),
        )

    def to_row(self) -> dict[str, Any]:
        """Convert to database row."""
        return {
            "instance_id": self.instance_id,
            "telegram_user_id": self.telegram_user_id,
            "dolibarr_user_id": self.dolibarr_user_id,
            "enabled": int(self.enabled),
            "created_at": self.created_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "username_cache": self.username_cache,
            "first_name_cache": self.first_name_cache,
            "last_name_cache": self.last_name_cache,
        }

    def with_enabled(self, enabled: bool) -> TelegramIdentity:
        """Return copy with updated enabled status."""
        return TelegramIdentity(
            instance_id=self.instance_id,
            telegram_user_id=self.telegram_user_id,
            dolibarr_user_id=self.dolibarr_user_id,
            enabled=enabled,
            created_at=self.created_at,
            last_seen_at=self.last_seen_at,
            username_cache=self.username_cache,
            first_name_cache=self.first_name_cache,
            last_name_cache=self.last_name_cache,
        )

    def with_last_seen(self, last_seen_at: datetime) -> TelegramIdentity:
        """Return copy with updated last_seen_at."""
        return TelegramIdentity(
            instance_id=self.instance_id,
            telegram_user_id=self.telegram_user_id,
            dolibarr_user_id=self.dolibarr_user_id,
            enabled=self.enabled,
            created_at=self.created_at,
            last_seen_at=last_seen_at,
            username_cache=self.username_cache,
            first_name_cache=self.first_name_cache,
            last_name_cache=self.last_name_cache,
        )

    def with_metadata(
        self,
        username: str | None = None,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> TelegramIdentity:
        """Return copy with updated metadata cache."""
        return TelegramIdentity(
            instance_id=self.instance_id,
            telegram_user_id=self.telegram_user_id,
            dolibarr_user_id=self.dolibarr_user_id,
            enabled=self.enabled,
            created_at=self.created_at,
            last_seen_at=self.last_seen_at,
            username_cache=username if username is not None else self.username_cache,
            first_name_cache=first_name if first_name is not None else self.first_name_cache,
            last_name_cache=last_name if last_name is not None else self.last_name_cache,
        )


@dataclass(frozen=True, slots=True)
class DolibarrUser:
    """Dolibarr user from REST API."""

    id: int
    login: str
    firstname: str
    lastname: str
    email: str
    active: bool
    entity: int
    rights: dict[str, Any] = field(default_factory=dict)  # module -> submodule -> perm -> level
    user_group_list: list[DolibarrGroup] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.firstname} {self.lastname}".strip()


@dataclass(frozen=True, slots=True)
class DolibarrGroup:
    """Dolibarr user group."""

    id: int
    name: str
    entity: int
    rights: dict[str, Any] | None = None  # loaded on demand


def _flatten_permissions(rights: dict[str, Any]) -> frozenset[str]:
    """
    Convert Dolibarr rights dict to flat permission strings.

    Dolibarr format: {module: {submodule: {permission: level}}}
    Output format: "module.submodule.permission"
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


def _merge_permissions(base: dict[str, Any], additional: dict[str, Any]) -> dict[str, Any]:
    """
    Merge two Dolibarr rights dicts (user + group permissions).
    Group permissions are additive (OR logic).
    """
    import copy

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

    _merge_dict(result, additional)
    return result


@dataclass(frozen=True, slots=True)
class UserContext:
    """
    Authenticated user context within a company.

    Combines CompanyContext (instance, actor) with Dolibarr user identity,
    groups, ERP permissions, and Gestor-IA specific roles.
    """

    instance_id: str
    telegram_user_id: int
    dolibarr_user_id: int
    dolibarr_user: DolibarrUser
    dolibarr_groups: list[DolibarrGroup]
    dolibarr_permissions: dict[str, Any]  # merged user + group rights
    gestor_roles: frozenset[str] = field(default_factory=frozenset)
    effective_permissions: frozenset[str] = field(init=False)

    def __post_init__(self) -> None:
        # Flatten Dolibarr permissions
        erp_perms = _flatten_permissions(self.dolibarr_permissions)
        # Union with Gestor-IA roles
        object.__setattr__(self, "effective_permissions", erp_perms | self.gestor_roles)

    def has_permission(self, permission: str) -> bool:
        """Check if user has a specific permission."""
        return permission in self.effective_permissions

    def require_permission(self, permission: str) -> None:
        """Raise if user lacks permission."""
        if not self.has_permission(permission):
            from core.hermes.authorization import ForbiddenError

            raise ForbiddenError(permission, self)


# =========================================================================
# GESTOR-IA PERMISSION CONSTANTS
# =========================================================================

class GestorPermissions:
    """Gestor-IA specific permissions (not from Dolibarr ERP)."""

    AI_USE = "ai.use"
    AI_EXTERNAL_PROVIDER = "ai.external_provider"
    AUDIT_READ = "audit.read"
    TELEGRAM_MANAGE = "telegram.manage"
    INSTANCE_MANAGE = "instance.manage"
    CONTENT_GENERATE = "content.generate"
    ADMIN = "admin"

    # All Gestor-IA permissions
    ALL: frozenset[str] = frozenset([
        AI_USE,
        AI_EXTERNAL_PROVIDER,
        AUDIT_READ,
        TELEGRAM_MANAGE,
        INSTANCE_MANAGE,
        CONTENT_GENERATE,
        ADMIN,
    ])


# =========================================================================
# HELPERS
# =========================================================================


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO datetime string to timezone-aware datetime."""
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt
