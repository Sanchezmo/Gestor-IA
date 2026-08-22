"""
Tests for audit extensions with user identity fields.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.hermes.audit import AuditActions, AuditLog, AuditLogger
from core.hermes.context import CompanyContext
from core.hermes.identity import DolibarrUser, UserContext
from core.hermes.instance_config import (
    AIConfig,
    DatabaseConfig,
    DolibarrConfig,
    DomainConfig,
    InstanceConfig,
    TelegramConfig,
)


class TestAuditActions:
    """Tests for AuditActions constants."""

    def test_identity_actions_defined(self):
        assert AuditActions.TELEGRAM_IDENTITY_UNKNOWN == "telegram.identity.unknown"
        assert AuditActions.TELEGRAM_IDENTITY_DISABLED == "telegram.identity.disabled"
        assert AuditActions.DOLIBARR_USER_DISABLED == "dolibarr.user.disabled"

    def test_authorization_actions_defined(self):
        assert AuditActions.AUTHORIZATION_DENIED == "authorization.denied"

    def test_user_management_actions_defined(self):
        assert AuditActions.USER_LINKED == "user.linked"
        assert AuditActions.USER_UNLINKED == "user.unlinked"
        assert AuditActions.USER_ENABLED == "user.enabled"
        assert AuditActions.USER_DISABLED == "user.disabled"

    def test_critical_actions_includes_identity(self):
        assert AuditActions.TELEGRAM_IDENTITY_UNKNOWN in AuditActions.CRITICAL_ACTIONS
        assert AuditActions.TELEGRAM_IDENTITY_DISABLED in AuditActions.CRITICAL_ACTIONS
        assert AuditActions.DOLIBARR_USER_DISABLED in AuditActions.CRITICAL_ACTIONS

    def test_critical_actions_includes_authorization(self):
        assert AuditActions.AUTHORIZATION_DENIED in AuditActions.CRITICAL_ACTIONS

    def test_critical_actions_includes_user_mgmt(self):
        assert AuditActions.USER_LINKED in AuditActions.CRITICAL_ACTIONS
        assert AuditActions.USER_UNLINKED in AuditActions.CRITICAL_ACTIONS
        assert AuditActions.USER_ENABLED in AuditActions.CRITICAL_ACTIONS
        assert AuditActions.USER_DISABLED in AuditActions.CRITICAL_ACTIONS


class TestAuditLogModel:
    """Tests for AuditLog model with new fields."""

    def test_auditlog_has_dolibarr_user_id_column(self):
        """Verify dolibarr_user_id column exists."""
        cols = {c.name: c for c in AuditLog.__table__.columns}
        assert "dolibarr_user_id" in cols
        assert isinstance(cols["dolibarr_user_id"].type.python_type, type)  # type check
        assert cols["dolibarr_user_id"].nullable is True

    def test_auditlog_has_telegram_user_id_column(self):
        """Verify telegram_user_id column exists."""
        cols = {c.name: c for c in AuditLog.__table__.columns}
        assert "telegram_user_id" in cols
        assert isinstance(cols["telegram_user_id"].type.python_type, type)  # type check
        assert cols["telegram_user_id"].nullable is True

    def test_auditlog_has_indexes_for_user_ids(self):
        """Verify indexes exist for user identity columns."""
        indexes = [idx.name for idx in AuditLog.__table__.indexes]
        assert "ix_audit_instance_dolibarr_user" in indexes
        assert "ix_audit_instance_telegram_user" in indexes


class TestAuditLoggerExtensions:
    """Tests for AuditLogger with user identity fields."""

    @pytest.fixture
    def mock_session(self):
        return MagicMock()

    @pytest.fixture
    def audit_logger(self, mock_session):
        with patch("core.hermes.audit.create_engine") as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            logger = AuditLogger("mysql://user:pass@localhost/db")
            logger.Session = MagicMock(return_value=mock_session)
            return logger

    def test_log_accepts_dolibarr_user_id(self, audit_logger):
        """Verify log method accepts dolibarr_user_id parameter."""
        import inspect

        sig = inspect.signature(audit_logger.log)
        params = list(sig.parameters.keys())
        assert "dolibarr_user_id" in params

    def test_log_accepts_telegram_user_id(self, audit_logger):
        """Verify log method accepts telegram_user_id parameter."""
        import inspect

        sig = inspect.signature(audit_logger.log)
        params = list(sig.parameters.keys())
        assert "telegram_user_id" in params

    def test_log_from_context_passes_user_ids(self):
        """Verify log_from_context passes user IDs from CompanyContext."""
        with patch("core.hermes.audit.create_engine") as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            logger = AuditLogger("mysql://user:pass@localhost/db")

            # Create mock CompanyContext with user_context
            mock_user_context = MagicMock(spec=UserContext)
            mock_user_context.dolibarr_user_id = 17
            mock_user_context.telegram_user_id = 123456

            mock_ctx = MagicMock(spec=CompanyContext)
            mock_ctx.instance_id = "empresa_a"
            mock_ctx.actor_type = "telegram_user"
            mock_ctx.actor_id = "123456"
            mock_ctx.request_id = "req-123"
            mock_ctx.correlation_id = "corr-123"
            mock_ctx.method = "POST"
            mock_ctx.endpoint = "/api/test"
            mock_ctx.ip_address = "127.0.0.1"
            mock_ctx.user_agent = "test-agent"
            mock_ctx.dolibarr_user_id = 17
            mock_ctx.telegram_user_id = 123456

            mock_session = MagicMock()
            logger.Session = MagicMock(return_value=mock_session)
            mock_session.execute = MagicMock()
            mock_session.commit = MagicMock()
            mock_session.close = MagicMock()

            # Mock _get_last_hash
            with patch.object(logger, "_get_last_hash", return_value="genesis"):
                import asyncio

                asyncio.run(logger.log_from_context(mock_ctx, "test.action"))

            # Verify the call included user IDs
            call_args = mock_session.execute.call_args
            params = call_args[0][1]
            assert params["dolibarr_user_id"] == 17
            assert params["telegram_user_id"] == 123456


class TestAuditCleanupWithCriticalActions:
    """Tests for audit cleanup respecting critical actions."""

    def test_cleanup_query_uses_critical_actions(self):
        """Verify cleanup uses AuditActions.CRITICAL_ACTIONS."""
        with patch("core.hermes.audit.create_engine") as mock_create_engine:
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            logger = AuditLogger("mysql://user:pass@localhost/db")

            mock_session = MagicMock()
            logger.Session = MagicMock(return_value=mock_session)
            mock_session.execute = MagicMock()
            mock_session.commit = MagicMock()
            mock_session.close = MagicMock()

            logger.cleanup_old_logs("empresa_a", retention_days=90)

            # Verify query was executed with critical actions
            call_args = mock_session.execute.call_args
            query_str = str(call_args[0][0])
            # Check that critical actions are in the NOT IN clause
            assert "telegram.identity.unknown" in query_str
            assert "authorization.denied" in query_str
            assert "user.linked" in query_str


class TestAuditLogIntegration:
    """Integration-style tests for audit with user identity."""

    @pytest.fixture
    def company_config(self):
        return InstanceConfig(
            instance_id="empresa_a",
            company_name="Empresa A",
            database=DatabaseConfig(host="127.0.0.1", port=3306, name="dolibarr_a", user="db_a", password="pass"),
            dolibarr=DolibarrConfig(
                version="23.0.4",
                internal_url="http://localhost:8081",
                api_key="key_a",
                documents_path="/docs",
            ),
            telegram=TelegramConfig(bot_token="token_a", webhook_path="/webhook/a", webhook_secret="secret_a"),
            domains=DomainConfig(base="a.com"),
            ai=AIConfig(ollama_model="test-model"),
        ).resolve_paths()

    def test_company_context_has_user_id_properties(self, company_config):
        """Verify CompanyContext has user identity properties."""
        ctx = CompanyContext(
            instance_config=company_config,
            actor_type="telegram_user",
            actor_id="123456",
        )
        # Without user_context
        assert ctx.is_authenticated is False
        assert ctx.telegram_user_id is None
        assert ctx.dolibarr_user_id is None

        # With user_context
        user = DolibarrUser(
            id=17,
            login="test",
            firstname="Test",
            lastname="User",
            email="test@test.com",
            active=True,
            entity=1,
        )
        user_ctx = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=user,
            dolibarr_groups=[],
            dolibarr_permissions={},
        )
        ctx_with_user = CompanyContext(
            instance_config=company_config,
            actor_type="telegram_user",
            actor_id="123456",
            user_context=user_ctx,
        )
        assert ctx_with_user.is_authenticated is True
        assert ctx_with_user.telegram_user_id == 123456
        assert ctx_with_user.dolibarr_user_id == 17

    def test_audit_dict_includes_user_ids(self, company_config):
        """Verify to_audit_dict includes user IDs when authenticated."""
        user = DolibarrUser(
            id=17,
            login="test",
            firstname="Test",
            lastname="User",
            email="test@test.com",
            active=True,
            entity=1,
        )
        user_ctx = UserContext(
            instance_id="empresa_a",
            telegram_user_id=123456,
            dolibarr_user_id=17,
            dolibarr_user=user,
            dolibarr_groups=[],
            dolibarr_permissions={},
        )
        ctx_with_user = CompanyContext(
            instance_config=company_config,
            actor_type="telegram_user",
            actor_id="123456",
            user_context=user_ctx,
        )

        audit_dict = ctx_with_user.to_audit_dict()
        assert "telegram_user_id" in audit_dict
        assert "dolibarr_user_id" in audit_dict
        assert audit_dict["telegram_user_id"] == 123456
        assert audit_dict["dolibarr_user_id"] == 17


# Import at bottom to avoid circular import issues
