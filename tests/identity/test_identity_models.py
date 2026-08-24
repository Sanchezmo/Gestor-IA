"""
Unit tests for identity models.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.hermes.identity import (
    DolibarrGroup,
    DolibarrUser,
    GestorPermissions,
    TelegramIdentity,
    UserContext,
    flatten_dolibarr_permissions,
    merge_dolibarr_permissions,
)
from core.hermes.identity_store import IdentityStore


class TestTelegramIdentity:
    """Tests for TelegramIdentity model."""

    def test_create_minimal(self):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        assert identity.instance_id == "empresa_a"
        assert identity.telegram_user_id == 123456
        assert identity.dolibarr_user_id == 17
        assert identity.enabled is True
        assert identity.created_at is not None
        assert identity.last_seen_at is None

    def test_create_full(self):
        now = datetime.now(UTC)
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            enabled=False,
            created_at=now,
            last_seen_at=now,
            username_cache="juanperez",
            first_name_cache="Juan",
            last_name_cache="Perez",
        )
        assert identity.enabled is False
        assert identity.username_cache == "juanperez"

    def test_from_row(self):
        row = {
            "instance_id": "empresa_a",
            "telegram_user_id": 123456,
            "dolibarr_user_id": 17,
            "enabled": 1,
            "created_at": "2024-01-15T10:30:00+00:00",
            "last_seen_at": "2024-01-15T12:00:00+00:00",
            "username_cache": "juanperez",
            "first_name_cache": "Juan",
            "last_name_cache": "Perez",
        }
        identity = TelegramIdentity.from_row(row)
        assert identity.instance_id == "empresa_a"
        assert identity.telegram_user_id == 123456
        assert identity.dolibarr_user_id == 17
        assert identity.enabled is True
        assert identity.username_cache == "juanperez"

    def test_to_row(self):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            enabled=True,
            created_at=datetime(2024, 1, 15, 10, 30, tzinfo=UTC),
            last_seen_at=datetime(2024, 1, 15, 12, 0, tzinfo=UTC),
            username_cache="juanperez",
        )
        row = identity.to_row()
        assert row["instance_id"] == "empresa_a"
        assert row["telegram_user_id"] == 123456
        assert row["dolibarr_user_id"] == 17
        assert row["enabled"] == 1
        assert row["username_cache"] == "juanperez"

    def test_with_enabled(self):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            enabled=True,
        )
        disabled = identity.with_enabled(False)
        assert disabled.enabled is False
        assert disabled.instance_id == identity.instance_id
        assert disabled.telegram_user_id == identity.telegram_user_id

    def test_with_last_seen(self):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        now = datetime.now(UTC)
        updated = identity.with_last_seen(now)
        assert updated.last_seen_at == now
        assert updated.telegram_user_id == identity.telegram_user_id

    def test_with_metadata(self):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        updated = identity.with_metadata(username="juan", first_name="Juan")
        assert updated.username_cache == "juan"
        assert updated.first_name_cache == "Juan"
        assert updated.last_name_cache is None  # unchanged


class TestDolibarrUser:
    """Tests for DolibarrUser model."""

    def test_create(self):
        user = DolibarrUser(
            id=17,
            login="juan.perez",
            firstname="Juan",
            lastname="Perez",
            email="juan@empresa.com",
            active=True,
            entity=1,
        )
        assert user.id == 17
        assert user.login == "juan.perez"
        assert user.full_name == "Juan Perez"

    def test_full_name_with_empty_firstname(self):
        user = DolibarrUser(
            id=17,
            login="jperez",
            firstname="",
            lastname="Perez",
            email="juan@empresa.com",
            active=True,
            entity=1,
        )
        assert user.full_name == "Perez"


class TestDolibarrGroup:
    """Tests for DolibarrGroup model."""

    def test_create(self):
        group = DolibarrGroup(id=5, name="Comercial", entity=1)
        assert group.id == 5
        assert group.name == "Comercial"
        assert group.entity == 1


class TestFlattenPermissions:
    """Tests for flatten_dolibarr_permissions helper."""

    def test_simple_permissions(self):
        rights = {
            "thirdparty": {
                "read": 1,
                "write": 0,
                "create": 1,
            }
        }
        perms = flatten_dolibarr_permissions(rights)
        assert "thirdparty.read" in perms
        assert "thirdparty.create" in perms
        assert "thirdparty.write" not in perms  # level 0

    def test_nested_permissions(self):
        rights = {
            "invoice": {
                "customer": {
                    "read": 1,
                    "create": 1,
                },
                "supplier": {
                    "read": 1,
                },
            }
        }
        perms = flatten_dolibarr_permissions(rights)
        assert "invoice.customer.read" in perms
        assert "invoice.customer.create" in perms
        assert "invoice.supplier.read" in perms

    def test_empty_permissions(self):
        perms = flatten_dolibarr_permissions({})
        assert perms == frozenset()

    def test_none_values_ignored(self):
        rights = {"module": {"perm": None}}
        perms = flatten_dolibarr_permissions(rights)
        assert perms == frozenset()


class TestMergePermissions:
    """Tests for merge_dolibarr_permissions helper."""

    def test_merge_user_and_group(self):
        user_rights = {
            "thirdparty": {"read": 1, "write": 0},
        }
        group_rights = {
            "thirdparty": {"write": 1, "create": 1},
            "invoice": {"read": 1},
        }
        merged = merge_dolibarr_permissions(user_rights, group_rights)
        # Group permissions are additive (OR logic)
        assert merged["thirdparty"]["read"] == 1
        assert merged["thirdparty"]["write"] == 1  # max(0, 1)
        assert merged["thirdparty"]["create"] == 1
        assert merged["invoice"]["read"] == 1

    def test_merge_no_conflict(self):
        user_rights = {"thirdparty": {"read": 1}}
        group_rights = {"invoice": {"read": 1}}
        merged = merge_dolibarr_permissions(user_rights, group_rights)
        assert merged["thirdparty"]["read"] == 1
        assert merged["invoice"]["read"] == 1


class TestUserContext:
    """Tests for UserContext model."""

    def create_sample_user(self) -> DolibarrUser:
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

    def test_effective_permissions_from_dolibarr(self):
        user = self.create_sample_user()
        context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=user,
            dolibarr_groups=[DolibarrGroup(id=5, name="Comercial", entity=1)],
            dolibarr_permissions=user.rights,
        )
        assert "thirdparty.read" in context.effective_permissions
        assert "thirdparty.create" in context.effective_permissions
        assert "invoice.read" in context.effective_permissions

    def test_effective_permissions_with_gestor_roles(self):
        user = self.create_sample_user()
        context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=user,
            dolibarr_groups=[],
            dolibarr_permissions=user.rights,
            gestor_roles=frozenset([GestorPermissions.AI_USE, GestorPermissions.ADMIN]),
        )
        assert "thirdparty.read" in context.effective_permissions
        assert GestorPermissions.AI_USE in context.effective_permissions
        assert GestorPermissions.ADMIN in context.effective_permissions

    def test_has_permission(self):
        user = self.create_sample_user()
        context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=user,
            dolibarr_groups=[],
            dolibarr_permissions=user.rights,
        )
        assert context.has_permission("thirdparty.read") is True
        assert context.has_permission("thirdparty.write") is False
        assert context.has_permission("nonexistent") is False

    def test_require_permission_raises(self):
        user = self.create_sample_user()
        context = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=user,
            dolibarr_groups=[],
            dolibarr_permissions=user.rights,
        )
        with pytest.raises(Exception) as exc_info:
            context.require_permission("thirdparty.write")
        assert "thirdparty.write" in str(exc_info.value)


class TestGestorPermissions:
    """Tests for Gestor-IA permission constants."""

    def test_all_permissions_defined(self):
        expected = {
            "ai.use",
            "ai.external_provider",
            "audit.read",
            "telegram.manage",
            "instance.manage",
            "content.generate",
            "admin",
            "product.read",
        }
        assert GestorPermissions.ALL == expected

    def test_individual_constants(self):
        assert GestorPermissions.AI_USE == "ai.use"
        assert GestorPermissions.ADMIN == "admin"


class TestIdentityStore:
    """Integration tests for IdentityStore with real SQLite."""

    @pytest.fixture
    def temp_instances_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def store(self, temp_instances_root):
        return IdentityStore("empresa_a", temp_instances_root)

    def test_create_and_get(self, store):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        store.create(identity)

        retrieved = store.get(123456)
        assert retrieved is not None
        assert retrieved.instance_id == "empresa_a"
        assert retrieved.telegram_user_id == 123456
        assert retrieved.dolibarr_user_id == 17
        assert retrieved.enabled is True

    def test_get_nonexistent(self, store):
        result = store.get(999999)
        assert result is None

    def test_get_by_dolibarr_user(self, store):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        store.create(identity)

        retrieved = store.get_by_dolibarr_user(17)
        assert retrieved is not None
        assert retrieved.telegram_user_id == 123456

    def test_update(self, store):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        store.create(identity)

        identity = identity.with_enabled(False)
        store.update(identity)

        retrieved = store.get(123456)
        assert retrieved.enabled is False

    def test_delete(self, store):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        store.create(identity)

        store.delete(123456)
        assert store.get(123456) is None

    def test_list_all(self, store):
        for i in range(3):
            identity = TelegramIdentity(
                instance_id="empresa_a",
                telegram_user_id=1000 + i,
                dolibarr_user_id=10 + i,
            )
            store.create(identity)

        all_identities = store.list_all()
        assert len(all_identities) == 3

    def test_set_enabled(self, store):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        store.create(identity)

        store.set_enabled(123456, False)
        assert store.get(123456).enabled is False

        store.set_enabled(123456, True)
        assert store.get(123456).enabled is True

    def test_update_last_seen(self, store):
        identity = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        store.create(identity)

        store.update_last_seen(123456)
        retrieved = store.get(123456)
        assert retrieved.last_seen_at is not None

    def test_unique_constraint_instance_telegram(self, store):
        identity1 = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        identity2 = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,  # Same telegram_user_id
            dolibarr_user_id=18,  # Different dolibarr_user_id
        )
        store.create(identity1)
        with pytest.raises(sqlite3.IntegrityError):
            store.create(identity2)

    def test_unique_constraint_instance_dolibarr(self, store):
        identity1 = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        identity2 = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123457,  # Different telegram_user_id
            dolibarr_user_id=17,  # Same dolibarr_user_id
        )
        store.create(identity1)
        with pytest.raises(sqlite3.IntegrityError):
            store.create(identity2)

    def test_cross_instance_isolation(self, temp_instances_root):
        """Same telegram_user_id can exist in different instances."""
        store_a = IdentityStore("empresa_a", temp_instances_root)
        store_b = IdentityStore("empresa_b", temp_instances_root)

        identity_a = TelegramIdentity(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
        )
        identity_b = TelegramIdentity(
            instance_id="empresa_b",
            telegram_user_id=123456,  # Same Telegram ID
            dolibarr_user_id=8,  # Different Dolibarr user
        )

        store_a.create(identity_a)
        store_b.create(identity_b)

        assert store_a.get(123456).dolibarr_user_id == 17
        assert store_b.get(123456).dolibarr_user_id == 8

    def test_wal_mode_enabled(self, store):
        """Verify WAL mode is enabled."""
        with store._connection() as conn:
            cursor = conn.execute("PRAGMA journal_mode;")
            mode = cursor.fetchone()[0]
            assert mode.upper() == "WAL"


# Import at bottom to avoid circular import in test discovery
import sqlite3
