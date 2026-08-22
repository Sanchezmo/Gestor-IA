"""
Tests for DolibarrClient user/group/permission extensions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.hermes.identity import DolibarrGroup, DolibarrUser
from core.integrations.dolibarr.client import DolibarrClient, DolibarrException


class TestDolibarrClientUserExtensions:
    """Tests for new user management methods in DolibarrClient."""

    @pytest.fixture
    def client(self):
        return DolibarrClient(base_url="http://localhost:8081", api_key="test_key")

    @pytest.fixture
    def mock_response_user(self):
        return {
            "id": "17",
            "login": "juan.perez",
            "firstname": "Juan",
            "lastname": "Perez",
            "email": "juan@empresa.com",
            "status": "1",
            "entity": "1",
            "rights": {
                "thirdparty": {"read": 1, "create": 1},
                "invoice": {"read": 1},
            },
            "user_group_list": [
                {"id": "5", "name": "Comercial", "entity": "1"}
            ],
        }

    @pytest.fixture
    def mock_response_groups(self):
        return [
            {"id": "5", "name": "Comercial", "entity": "1"},
            {"id": "6", "name": "Administrativos", "entity": "1"},
        ]

    @pytest.fixture
    def mock_response_group_perms(self):
        return {
            "id": "5",
            "name": "Comercial",
            "entity": "1",
            "rights": {
                "thirdparty": {"write": 1},
                "invoice": {"create": 1},
            },
        }

    @pytest.mark.asyncio
    async def test_get_user_success(self, client, mock_response_user):
        # Mock the internal _request method directly
        client._request = AsyncMock(return_value=mock_response_user)

        async with client as c:
            user = await c.get_user(17)

        assert isinstance(user, DolibarrUser)
        assert user.id == 17
        assert user.login == "juan.perez"
        assert user.firstname == "Juan"
        assert user.lastname == "Perez"
        assert user.active is True
        assert user.rights.get("thirdparty", {}).get("read") == 1

    @pytest.mark.asyncio
    async def test_get_user_with_permissions(self, client, mock_response_user):
        client._request = AsyncMock(return_value=mock_response_user)

        async with client as c:
            _ = await c.get_user(17, include_permissions=True)

        # Verify includepermissions=1 was passed
        client._request.assert_called_once()
        call_args = client._request.call_args
        assert call_args[1]["params"]["includepermissions"] == 1

    @pytest.mark.asyncio
    async def test_get_user_without_permissions(self, client, mock_response_user):
        client._request = AsyncMock(return_value=mock_response_user)

        async with client as c:
            _ = await c.get_user(17, include_permissions=False)

        call_args = client._request.call_args
        assert "includepermissions" not in call_args[1]["params"]

    @pytest.mark.asyncio
    async def test_get_user_not_found(self, client):
        client._request = AsyncMock(side_effect=DolibarrException(
            message="User not found", endpoint="users/999", status_code=404
        ))

        async with client as c:
            with pytest.raises(DolibarrException) as exc_info:
                await c.get_user(999)

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_user_by_login(self, client, mock_response_user):
        client._request = AsyncMock(return_value=mock_response_user)

        async with client as c:
            user = await c.get_user_by_login("juan.perez")

        assert user.login == "juan.perez"
        call_args = client._request.call_args
        assert "login/juan.perez" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_get_user_groups(self, client, mock_response_groups):
        client._request = AsyncMock(return_value=mock_response_groups)

        async with client as c:
            groups = await c.get_user_groups(17)

        assert len(groups) == 2
        assert all(isinstance(g, DolibarrGroup) for g in groups)
        assert groups[0].name == "Comercial"
        assert groups[1].name == "Administrativos"

    @pytest.mark.asyncio
    async def test_get_user_groups_empty(self, client):
        client._request = AsyncMock(return_value=[])

        async with client as c:
            groups = await c.get_user_groups(17)

        assert groups == []

    @pytest.mark.asyncio
    async def test_get_group_permissions(self, client, mock_response_group_perms):
        client._request = AsyncMock(return_value=mock_response_group_perms)

        async with client as c:
            perms = await c.get_group_permissions(5)

        assert "thirdparty" in perms
        assert "invoice" in perms
        assert perms["thirdparty"]["write"] == 1

    @pytest.mark.asyncio
    async def test_get_group_permissions_empty(self, client):
        client._request = AsyncMock(return_value={})

        async with client as c:
            perms = await c.get_group_permissions(5)

        assert perms == {}

    @pytest.mark.asyncio
    async def test_map_dolibarr_user(self, client, mock_response_user):
        user = DolibarrClient._map_dolibarr_user(mock_response_user)
        assert user.id == 17
        assert user.login == "juan.perez"
        assert user.firstname == "Juan"
        assert user.lastname == "Perez"
        assert user.active is True
        assert len(user.user_group_list) == 1
        assert user.user_group_list[0].name == "Comercial"

    @pytest.mark.asyncio
    async def test_map_dolibarr_group(self, client):
        group_data = {"id": "5", "name": "Comercial", "entity": "1", "rights": {"read": 1}}
        group = DolibarrClient._map_dolibarr_group(group_data)
        assert group.id == 5
        assert group.name == "Comercial"
        assert group.entity == 1
        assert group.rights == {"read": 1}

    @pytest.mark.asyncio
    async def test_map_dolibarr_group_with_rowid(self, client):
        # Dolibarr sometimes uses rowid instead of id
        group_data = {"rowid": "5", "nom": "Comercial", "entity": "1"}
        group = DolibarrClient._map_dolibarr_group(group_data)
        assert group.id == 5
        assert group.name == "Comercial"


class TestDolibarrClientUserIntegration:
    """Integration-style tests with mocked _request."""

    @pytest.mark.asyncio
    async def test_full_user_resolution_flow(self):
        """Test resolving user -> groups -> merged permissions."""
        client = DolibarrClient(base_url="http://localhost:8081", api_key="test_key")

        user_data = {
            "id": "17",
            "login": "juan.perez",
            "firstname": "Juan",
            "lastname": "Perez",
            "email": "juan@empresa.com",
            "status": "1",
            "entity": "1",
            "rights": {
                "thirdparty": {"read": 1},
            },
            "user_group_list": [
                {"id": "5", "name": "Comercial", "entity": "1"}
            ],
        }

        groups_data = [
            {"id": "5", "name": "Comercial", "entity": "1"}
        ]

        group_perms = {
            "id": "5",
            "name": "Comercial",
            "entity": "1",
            "rights": {
                "thirdparty": {"write": 1, "create": 1},
                "invoice": {"read": 1},
            },
        }

        client._request = AsyncMock()
        client._request.side_effect = [user_data, groups_data, group_perms]

        async with client as c:
            user = await c.get_user(17)
            groups = await c.get_user_groups(17)
            perms = await c.get_group_permissions(groups[0].id)

        # Verify user permissions + group permissions
        assert user.rights["thirdparty"]["read"] == 1
        assert perms["thirdparty"]["write"] == 1
        assert perms["invoice"]["read"] == 1
