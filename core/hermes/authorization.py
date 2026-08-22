"""
Authorization service for multi-user permission checks.

Centralizes all permission logic with default-deny principle.
"""

from __future__ import annotations

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
    - Single source of truth: UserContext.effective_permissions
    - Clean API: can() for checks, require() for enforcement
    """

    def __init__(self) -> None:
        pass

    def can(self, user_context: UserContext, permission: str) -> bool:
        """
        Check if user has permission.

        Args:
            user_context: Authenticated user context
            permission: Permission string (e.g., "thirdparty.read", "ai.use")

        Returns:
            True if allowed, False if denied (default deny)
        """
        return user_context.has_permission(permission)

    def require(self, user_context: UserContext, permission: str) -> None:
        """
        Require permission, raise ForbiddenError if not granted.

        Args:
            user_context: Authenticated user context
            permission: Permission string

        Raises:
            ForbiddenError: If permission not in effective_permissions
        """
        if not self.can(user_context, permission):
            raise ForbiddenError(permission, user_context)

    def get_effective_permissions(self, user_context: UserContext) -> frozenset[str]:
        """Get all effective permissions for a user."""
        return user_context.effective_permissions

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
