"""
Identity resolution pipeline: TelegramIdentity -> DolibarrUser -> UserContext.
"""

from __future__ import annotations

from collections.abc import Callable

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.identity_store import IdentityStore
from core.hermes.permissions import merge_dolibarr_permissions
from core.integrations.dolibarr.client import DolibarrClient, DolibarrException

# =========================================================================
# EXCEPTIONS
# =========================================================================


class IdentityError(Exception):
    """Base exception for identity resolution errors."""

    def __init__(self, message: str, instance_id: str, telegram_user_id: int):
        self.instance_id = instance_id
        self.telegram_user_id = telegram_user_id
        super().__init__(message)


class IdentityNotFoundError(IdentityError):
    """Telegram user not linked in this instance."""

    def __init__(self, instance_id: str, telegram_user_id: int):
        super().__init__(
            f"Telegram user {telegram_user_id} not linked in instance {instance_id}",
            instance_id,
            telegram_user_id,
        )


class IdentityDisabledError(IdentityError):
    """Telegram identity is disabled."""

    def __init__(self, instance_id: str, telegram_user_id: int):
        super().__init__(
            f"Telegram identity disabled for user {telegram_user_id} in instance {instance_id}",
            instance_id,
            telegram_user_id,
        )


class DolibarrUserNotFoundError(IdentityError):
    """Dolibarr user not found."""

    def __init__(self, instance_id: str, telegram_user_id: int, dolibarr_user_id: int):
        self.dolibarr_user_id = dolibarr_user_id
        super().__init__(
            f"Dolibarr user {dolibarr_user_id} not found for Telegram user {telegram_user_id} "
            f"in instance {instance_id}",
            instance_id,
            telegram_user_id,
        )


class DolibarrUserDisabledError(IdentityError):
    """Dolibarr user is inactive."""

    def __init__(self, instance_id: str, telegram_user_id: int, dolibarr_user_id: int):
        self.dolibarr_user_id = dolibarr_user_id
        super().__init__(
            f"Dolibarr user {dolibarr_user_id} is inactive for Telegram user {telegram_user_id} "
            f"in instance {instance_id}",
            instance_id,
            telegram_user_id,
        )


class DolibarrConnectionError(IdentityError):
    """Dolibarr API connection failed."""

    def __init__(self, instance_id: str, telegram_user_id: int, original_error: Exception):
        self.original_error = original_error
        super().__init__(
            f"Dolibarr connection failed for Telegram user {telegram_user_id} "
            f"in instance {instance_id}: {original_error}",
            instance_id,
            telegram_user_id,
        )


# =========================================================================
# IDENTITY RESOLVER
# =========================================================================


class IdentityResolver:
    """
    Resolves Telegram user identity to full UserContext.

    Pipeline:
    1. Load TelegramIdentity from SQLite (instance_id + telegram_user_id)
    2. Validate identity exists and is enabled
    3. Load DolibarrUser via DolibarrClient.get_user() with permissions
    4. Validate Dolibarr user exists and is active
    5. Load user groups and group permissions
    6. Merge user + group permissions
    7. Build UserContext
    8. Update last_seen_at in SQLite
    """

    def __init__(
        self,
        identity_store: IdentityStore,
        dolibarr_client_factory: Callable[[CompanyContext], DolibarrClient],
    ):
        self._store = identity_store
        self._client_factory = dolibarr_client_factory

    async def resolve(
        self,
        company_context: CompanyContext,
        telegram_user_id: int,
    ) -> UserContext:
        """
        Resolve full user context from Telegram user ID.

        Args:
            company_context: Resolved company context (instance already determined)
            telegram_user_id: Telegram user ID from update

        Returns:
            UserContext with Dolibarr user, groups, permissions, and Gestor-IA roles

        Raises:
            IdentityNotFoundError: Telegram user not linked in this instance
            IdentityDisabledError: Telegram identity disabled
            DolibarrUserNotFoundError: Dolibarr user not found
            DolibarrUserDisabledError: Dolibarr user inactive
            DolibarrConnectionError: API connection failed
        """
        instance_id = company_context.instance_id

        # 1. Load TelegramIdentity
        identity = self._store.get(telegram_user_id)
        if not identity:
            raise IdentityNotFoundError(instance_id, telegram_user_id)

        if not identity.enabled:
            raise IdentityDisabledError(instance_id, telegram_user_id)

        # 2. Load Dolibarr user
        client = self._client_factory(company_context)
        try:
            async with client as dolibarr:
                user = await dolibarr.get_user(identity.dolibarr_user_id, include_permissions=True)
        except DolibarrException as e:
            if e.status_code == 404:
                raise DolibarrUserNotFoundError(instance_id, telegram_user_id, identity.dolibarr_user_id)
            # Other errors (5xx, timeout, etc.) -> connection error
            raise DolibarrConnectionError(instance_id, telegram_user_id, e)

        if not user:
            raise DolibarrUserNotFoundError(instance_id, telegram_user_id, identity.dolibarr_user_id)

        if not user.active:
            raise DolibarrUserDisabledError(instance_id, telegram_user_id, identity.dolibarr_user_id)

        # 3. Load groups and permissions
        async with client as dolibarr:
            groups = await dolibarr.get_user_groups(identity.dolibarr_user_id)

            # Merge user permissions + group permissions
            all_permissions = user.rights or {}
            for group in groups:
                if group.rights:
                    all_permissions = merge_dolibarr_permissions(all_permissions, group.rights)

        # 4. Build UserContext
        # Assign Gestor-IA roles based on Dolibarr user
        gestor_roles = frozenset()
        if user.id == 1:  # Dolibarr admin user gets admin role
            gestor_roles = frozenset(["admin"])

        user_context = UserContext(
            instance_id=instance_id,
            telegram_user_id=telegram_user_id,
            dolibarr_user_id=identity.dolibarr_user_id,
            dolibarr_user=user,
            dolibarr_groups=groups,
            dolibarr_permissions=all_permissions,
            gestor_roles=gestor_roles,
        )

        # 5. Update last_seen_at
        from datetime import UTC, datetime

        updated_identity = identity.with_last_seen(datetime.now(UTC))
        self._store.update(updated_identity)

        return user_context
