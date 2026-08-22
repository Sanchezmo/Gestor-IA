"""
Unit tests for AuthorizationService.
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

    def test_can_with_erp_permission(self, auth_service, user_context):
        assert auth_service.can(user_context, "thirdparty.read") is True
        assert auth_service.can(user_context, "thirdparty.create") is True
        assert auth_service.can(user_context, "invoice.read") is True

    def test_can_denies_missing_permission(self, auth_service, user_context):
        assert auth_service.can(user_context, "thirdparty.write") is False
        assert auth_service.can(user_context, "invoice.create") is False
        assert auth_service.can(user_context, "nonexistent.permission") is False

    def test_can_with_gestor_permission(self, auth_service, admin_context):
        assert auth_service.can(admin_context, GestorPermissions.ADMIN) is True
        assert auth_service.can(admin_context, GestorPermissions.AI_USE) is False

    def test_require_allows_granted_permission(self, auth_service, user_context):
        # Should not raise
        auth_service.require(user_context, "thirdparty.read")

    def test_require_raises_on_denied(self, auth_service, user_context):
        with pytest.raises(ForbiddenError) as exc_info:
            auth_service.require(user_context, "thirdparty.write")

        error = exc_info.value
        assert error.permission == "thirdparty.write"
        assert error.instance_id == "empresa_a"
        assert error.telegram_user_id == 123456
        assert error.dolibarr_user_id == 17

    def test_require_any_allows_if_one_granted(self, auth_service, user_context):
        # User has thirdparty.read but not invoice.create
        auth_service.require_any(user_context, ["thirdparty.read", "invoice.create"])

    def test_require_any_raises_if_none_granted(self, auth_service, user_context):
        with pytest.raises(ForbiddenError) as exc_info:
            auth_service.require_any(user_context, ["thirdparty.write", "invoice.create"])

        assert "one of" in str(exc_info.value)

    def test_require_all_allows_if_all_granted(self, auth_service, user_context):
        auth_service.require_all(user_context, ["thirdparty.read", "invoice.read"])

    def test_require_all_raises_if_any_missing(self, auth_service, user_context):
        with pytest.raises(ForbiddenError):
            auth_service.require_all(user_context, ["thirdparty.read", "thirdparty.write"])

    def test_get_effective_permissions(self, auth_service, user_context):
        perms = auth_service.get_effective_permissions(user_context)
        assert "thirdparty.read" in perms
        assert "thirdparty.create" in perms
        assert "invoice.read" in perms

    def test_get_effective_permissions_includes_gestor_roles(self, auth_service, admin_context):
        perms = auth_service.get_effective_permissions(admin_context)
        assert GestorPermissions.ADMIN in perms
        assert "thirdparty.read" in perms


class TestForbiddenError:
    """Tests for ForbiddenError exception."""

    def test_contains_permission_info(self):
        context = create_user_context()
        error = ForbiddenError("thirdparty.write", context)

        assert error.permission == "thirdparty.write"
        assert error.instance_id == "empresa_a"
        assert error.telegram_user_id == 123456
        assert error.dolibarr_user_id == 17

    def test_error_message_format(self):
        context = create_user_context()
        error = ForbiddenError("thirdparty.write", context)

        msg = str(error)
        assert "thirdparty.write" in msg
        assert "empresa_a" in msg
        assert "123456" in msg
        assert "17" in msg
