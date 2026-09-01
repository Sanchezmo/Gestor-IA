"""
Unit tests for AuthorizationService.

AuthorizationService ONLY checks Hermes-specific capabilities.
ERP permissions are delegated to Dolibarr (sole authority).
"""

from __future__ import annotations

import pytest

from core.hermes.authorization import AuthorizationService, ForbiddenError
from core.hermes.identity import (
    DolibarrGroup,
    DolibarrUser,
    GestorPermissions,
    UserContext,
)


def create_sample_user() -> DolibarrUser:
    return DolibarrUser(
        id=17,
        login="juan.perez",
        firstname="Juan",
        lastname="Perez",
        email="juan@empresa.com",
        active=True,
        entity=1,
        rights={
            "thirdparty": {"read": 1, "create": 1},
            "invoice": {"read": 1},
        },
        user_group_list=[DolibarrGroup(id=5, name="Comercial", entity=1)],
    )


def create_user_context(gestor_roles: frozenset[str] = frozenset()) -> UserContext:
    user = create_sample_user()
    return UserContext(
        instance_id="empresa_a",
        telegram_user_id=123456,
        dolibarr_user_id=17,
        dolibarr_user=user,
        dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
        dolibarr_permissions=user.rights,
        gestor_roles=gestor_roles,
    )


class TestAuthorizationService:
    """Tests for AuthorizationService."""

    @pytest.fixture
    def auth_service(self):
        return AuthorizationService()

    @pytest.fixture
    def user_context(self):
        return create_user_context()

    @pytest.fixture
    def admin_context(self):
        return create_user_context(frozenset([GestorPermissions.ADMIN]))

    @pytest.fixture
    def ai_user_context(self):
        return create_user_context(frozenset([GestorPermissions.AI_USE]))

    def test_can_with_hermes_capability_admin(self, auth_service, admin_context):
        """Admin role grants all Hermes capabilities."""
        assert auth_service.can(admin_context, GestorPermissions.ADMIN) is True
        assert auth_service.can(admin_context, GestorPermissions.AI_USE) is True
        assert auth_service.can(admin_context, GestorPermissions.AI_EXTERNAL_PROVIDER) is True
        assert auth_service.can(admin_context, GestorPermissions.TELEGRAM_MANAGE) is True

    def test_can_with_hermes_capability_ai_use(self, auth_service, ai_user_context):
        """AI_USE role grants AI capabilities."""
        assert auth_service.can(ai_user_context, GestorPermissions.AI_USE) is True
        assert auth_service.can(ai_user_context, GestorPermissions.AI_EXTERNAL_PROVIDER) is False

    def test_can_denies_missing_hermes_capability(self, auth_service, user_context):
        """Default deny for Hermes capabilities not in gestor_roles."""
        assert auth_service.can(user_context, GestorPermissions.ADMIN) is False
        assert auth_service.can(user_context, GestorPermissions.AI_USE) is False
        assert auth_service.can(user_context, GestorPermissions.AI_EXTERNAL_PROVIDER) is False
        assert auth_service.can(user_context, GestorPermissions.TELEGRAM_MANAGE) is False
        assert auth_service.can(user_context, "nonexistent.capability") is False

    def test_can_erp_permissions_not_checked(self, auth_service, user_context):
        """ERP permissions are NOT checked by AuthorizationService.
        
        Dolibarr is the sole authority for ERP permissions.
        These return False (default deny) - let Dolibarr decide via 403.
        """
        # These are ERP permissions, not Hermes capabilities
        assert auth_service.can(user_context, "thirdparty.read") is False
        assert auth_service.can(user_context, "thirdparty.create") is False
        assert auth_service.can(user_context, "invoice.read") is False
        assert auth_service.can(user_context, "invoice.create") is False
        assert auth_service.can(user_context, "product.read") is False

    def test_require_allows_granted_hermes_capability(self, auth_service, admin_context):
        """Should not raise for granted Hermes capabilities."""
        auth_service.require(admin_context, GestorPermissions.ADMIN)
        auth_service.require(admin_context, GestorPermissions.AI_USE)
        auth_service.require(admin_context, GestorPermissions.TELEGRAM_MANAGE)

    def test_require_raises_on_denied_hermes_capability(self, auth_service, user_context):
        """Raise ForbiddenError for denied Hermes capabilities."""
        with pytest.raises(ForbiddenError) as exc_info:
            auth_service.require(user_context, GestorPermissions.ADMIN)

        error = exc_info.value
        assert error.permission == GestorPermissions.ADMIN
        assert error.instance_id == "empresa_a"
        assert error.telegram_user_id == 123456
        assert error.dolibarr_user_id == 17

    def test_require_raises_on_erp_permission(self, auth_service, user_context):
        """ERP permissions are denied by AuthorizationService (delegated to Dolibarr)."""
        with pytest.raises(ForbiddenError):
            auth_service.require(user_context, "thirdparty.read")

    def test_require_any_allows_if_one_granted(self, auth_service, ai_user_context):
        """At least one granted Hermes capability passes."""
        auth_service.require_any(ai_user_context, [GestorPermissions.AI_USE, GestorPermissions.ADMIN])

    def test_require_any_raises_if_none_granted(self, auth_service, user_context):
        """Raise if none of the Hermes capabilities are granted."""
        with pytest.raises(ForbiddenError) as exc_info:
            auth_service.require_any(user_context, [GestorPermissions.ADMIN, GestorPermissions.AI_USE])

        assert "one of" in str(exc_info.value)

    def test_require_all_allows_if_all_granted(self, auth_service, admin_context):
        """All Hermes capabilities granted passes."""
        auth_service.require_all(admin_context, [GestorPermissions.ADMIN, GestorPermissions.AI_USE])

    def test_require_all_raises_if_any_missing(self, auth_service, ai_user_context):
        """Raise if any Hermes capability is missing."""
        with pytest.raises(ForbiddenError):
            auth_service.require_all(ai_user_context, [GestorPermissions.ADMIN, GestorPermissions.AI_USE])

    def test_get_hermes_capabilities(self, auth_service, user_context, admin_context):
        """Get Hermes capabilities from gestor_roles."""
        assert auth_service.get_hermes_capabilities(user_context) == frozenset()
        assert auth_service.get_hermes_capabilities(admin_context) == frozenset([GestorPermissions.ADMIN])


class TestForbiddenError:
    """Tests for ForbiddenError exception."""

    def test_contains_permission_info(self):
        context = create_user_context()
        error = ForbiddenError(GestorPermissions.ADMIN, context)

        assert error.permission == GestorPermissions.ADMIN
        assert error.instance_id == "empresa_a"
        assert error.telegram_user_id == 123456
        assert error.dolibarr_user_id == 17

    def test_error_message_format(self):
        context = create_user_context()
        error = ForbiddenError(GestorPermissions.ADMIN, context)

        msg = str(error)
        assert GestorPermissions.ADMIN in msg
        assert "empresa_a" in msg
        assert "123456" in msg
        assert "17" in msg

    def test_erp_permission_error_format(self):
        """ERP permission errors also include context."""
        context = create_user_context()
        error = ForbiddenError("thirdparty.read", context)

        msg = str(error)
        assert "thirdparty.read" in msg
        assert "empresa_a" in msg