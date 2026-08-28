"""
Authorization service for multi-user permission checks.

Centralizes all permission logic with default-deny principle.

Hermes ONLY manages Hermes-specific capabilities.
Dolibarr is the SOLE AUTHORITY for ERP permissions.
"""

from __future__ import annotations

from core.hermes.capabilities import get_capability_resolver
from core.hermes.identity import UserContext


class ForbiddenError(Exception):
    """Raised when a permission check fails."""

    def __init__(self, permission: str, user_context: UserContext):
        self.permission = permission
        self.user_context = user_context
        self.instance_id = user_context.instance_id
        self.telegram_user_id = user_context.telegram_user_id
        self.dolibarr_user_id = user_context.dolibarr_user_id
        super().__init__(
            f"Permission denied: {permission} "
            f"(instance={self.instance_id}, "
            f"telegram_user={self.telegram_user_id}, "
            f"dolibarr_user={self.dolibarr_user_id})"
        )


class AuthorizationService:
    """
    Centralized authorization service.

    Principles:
    - Default deny: if permission not provable -> DENY
    - Single source of truth: UserContext.gestor_roles for Hermes capabilities
    - Clean API: can() for checks, require() for enforcement
    - ERP permissions are NOT checked here - Dolibarr enforces them
    """

    def __init__(self) -> None:
        pass

    def can(self, user_context: UserContext, permission: str) -> bool:
        """
        Check if user has permission.

        Args:
            user_context: Authenticated user context
            permission: Permission string (e.g., "ai.use", "admin")

        Returns:
            True if allowed, False if denied (default deny)
            
        Note:
            Only checks Hermes-specific capabilities.
            ERP permissions (thirdparty.read, product.read, etc.) are NOT checked here.
            Dolibarr will return 403 if the user lacks ERP permissions.
        """
        # Use CapabilityResolver to check Hermes capabilities only
        resolver = get_capability_resolver()
        # User's effective_permissions includes both ERP permissions (flattened) and gestor_roles
        # We only check gestor_roles for Hermes capabilities
        return resolver.resolve(permission, user_context.gestor_roles)

    def require(self, user_context: UserContext, permission: str) -> None:
        """
        Require permission, raise ForbiddenError if not granted.

        Args:
            user_context: Authenticated user context
            permission: Permission string

        Raises:
            ForbiddenError: If permission not in gestor_roles
        """
        if not self.can(user_context, permission):
            raise ForbiddenError(permission, user_context)

    def get_hermes_capabilities(self, user_context: UserContext) -> frozenset[str]:
        """Get all Hermes capabilities granted to the user."""
        return user_context.gestor_roles

    def require_any(self, user_context: UserContext, permissions: list[str]) -> None:
        """
        Require at least one of the given permissions.

        Args:
            user_context: Authenticated user context
            permissions: List of permission strings

        Raises:
            ForbiddenError: If none of the permissions are granted
        """
        if not any(self.can(user_context, p) for p in permissions):
            raise ForbiddenError(f"one of {permissions}", user_context)

    def require_all(self, user_context: UserContext, permissions: list[str]) -> None:
        """
        Require all of the given permissions.

        Args:
            user_context: Authenticated user context
            permissions: List of permission strings

        Raises:
            ForbiddenError: If any permission is missing
        """
        for permission in permissions:
            self.require(user_context, permission)
