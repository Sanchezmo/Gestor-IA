"""
Command Layer V1 - Command Executor.

Orchestrates preview → confirmation → execution → audit.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from core.hermes.audit import AuditLogger
from core.hermes.authorization import AuthorizationService
from core.hermes.commands.base import CommandRegistry
from core.hermes.commands.models import (
    CommandIntent,
    CommandPreview,
    CommandResult,
    CommandStatus,
    CommandType,
    PendingCommand,
)
from core.hermes.commands.policy import get_company_policy
from core.hermes.commands.store import PendingCommandStore
from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.integrations.dolibarr.client import DolibarrException


class CommandExecutor:
    """Orchestrates command preview → confirmation → execution → audit."""

    def __init__(
        self,
        registry: CommandRegistry,
        store: PendingCommandStore,
        audit_logger: AuditLogger,
        company_context: CompanyContext,
        user_context: UserContext,
    ) -> None:
        self.registry = registry
        self.store = store
        self.audit = audit_logger
        self.ctx = company_context
        self.user = user_context
        self.auth = AuthorizationService()
        self.policy = get_company_policy(company_context.instance_id)

    async def preview(self, intent: CommandIntent) -> CommandPreview:
        """Generate preview for confirmation. Checks auth + policy."""
        # 1. Get handler
        handler = self.registry.get_handler(self.ctx.instance_id, intent.command_type)
        if not handler:
            raise ValueError(f"No handler for {intent.command_type}")

        # 2. Check permission BEFORE preview (skip if no Hermes permission required)
        required = handler.required_permission
        if required and not self.auth.can(self.user, required):
            await self._audit_preview_denied(intent, required)
            raise PermissionError(f"Requiere permiso: {required}")

        # 3. Validate payload
        try:
            validated_payload = handler.validate_payload(intent.payload)
        except ValueError as e:
            await self._audit_preview_denied(intent, "INVALID_PAYLOAD", str(e))
            raise

        # 4. Apply company policy
        validated_cmd = self.policy.validate_command(intent.command_type, validated_payload, self.ctx)
        enriched_payload = self.policy.enrich_command(intent.command_type, validated_cmd.payload, self.ctx)

        # 5. Generate preview
        preview = handler.generate_preview(enriched_payload, self.ctx)

        # 6. Store pending command
        pending = PendingCommand(
            command_id=preview.command_id,
            instance_id=self.ctx.instance_id,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
            command_type=intent.command_type,
            validated_payload=enriched_payload,
            status=CommandStatus.PENDING,
            created_at=datetime.now(),
            expires_at=datetime.now() + __import__("datetime").timedelta(hours=24),
            idempotency_key=str(preview.command_id),
        )
        self.store.create(pending)

        # 7. Audit preview
        await self._audit_preview_created(preview, enriched_payload)

        return preview

    async def confirm(self, command_id: UUID, telegram_user_id: int) -> CommandResult:
        """Confirm and execute pending command."""
        # 1. Load and validate pending command atomically
        pending = self.store.confirm(command_id, telegram_user_id)
        if not pending:
            # Check if it exists but wrong user/state
            existing = self.store.get(command_id)
            if existing:
                if existing.telegram_user_id != telegram_user_id:
                    return CommandResult(
                        success=False,
                        error_code="FORBIDDEN",
                        error_message="Solo el usuario original puede confirmar",
                    )
                if existing.status == CommandStatus.EXECUTED:
                    return CommandResult(
                        success=True,
                        idempotent=True,
                        resource_id=existing.result.get("resource_id") if existing.result else None,
                        data=existing.result,
                    )
                return CommandResult(
                    success=False,
                    error_code="INVALID_STATE",
                    error_message=f"Comando en estado {existing.status.value}",
                )
            return CommandResult(
                success=False,
                error_code="NOT_FOUND",
                error_message="Confirmación no encontrada o expirada",
            )

        # 2. RECHECK authorization (skip if no Hermes permission required)
        handler = self.registry.get_handler(self.ctx.instance_id, pending.command_type)
        required = handler.required_permission
        if required and not self.auth.can(self.user, required):
            self.store.update_status(
                command_id, CommandStatus.FAILED, error_code="PERMISSION_REVOKED", error_message="Permiso revocado"
            )
            await self._audit_execution_failed(pending, "PERMISSION_REVOKED", "Permiso revocado antes de ejecutar")
            return CommandResult(success=False, error_code="PERMISSION_REVOKED", error_message="Permiso revocado")

        # 3. Audit confirmation
        await self._audit_confirmed(pending)

        # 4. Execute
        try:
            result = await handler.execute(self.ctx, self.user, pending.validated_payload)

            # 4a. Update to EXECUTED
            result_data = {
                "resource_id": result.resource_id,
                "resource_type": result.resource_type,
                "data": result.data,
                "idempotent": result.idempotent,
            }
            self.store.update_status(
                command_id,
                CommandStatus.EXECUTED,
                executed_at=datetime.now(),
                result=result_data,
            )

            # 4b. Audit success
            await self._audit_execution_success(pending, result)

            return result

        except DolibarrException as e:
            # Handle 409 as idempotent success
            if e.status_code == 409:
                existing_id = await self._find_existing_resource(pending)
                if existing_id:
                    result = CommandResult(
                        success=True,
                        resource_id=existing_id,
                        resource_type=pending.command_type.value.replace("create_", ""),
                        idempotent=True,
                    )
                    self.store.update_status(
                        command_id,
                        CommandStatus.EXECUTED,
                        executed_at=datetime.now(),
                        result={"resource_id": existing_id, "idempotent": True},
                    )
                    await self._audit_execution_success(pending, result)
                    return result

            # Other Dolibarr errors
            self.store.update_status(
                command_id, CommandStatus.FAILED, error_code=f"DOLIBARR_{e.status_code}", error_message=e.message
            )
            await self._audit_execution_failed(pending, f"DOLIBARR_{e.status_code}", e.message)
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido completar la operación",
            )

        except Exception as e:
            self.store.update_status(
                command_id, CommandStatus.FAILED, error_code="INTERNAL_ERROR", error_message=str(e)
            )
            await self._audit_execution_failed(pending, "INTERNAL_ERROR", str(e))
            return CommandResult(success=False, error_code="INTERNAL_ERROR", error_message="Error interno")

    async def cancel(self, command_id: UUID, telegram_user_id: int) -> CommandResult:
        """Cancel pending command."""
        cancelled = self.store.cancel(command_id, telegram_user_id)
        if not cancelled:
            existing = self.store.get(command_id)
            if not existing:
                return CommandResult(success=False, error_code="NOT_FOUND", error_message="Comando no encontrado")
            if existing.telegram_user_id != telegram_user_id:
                return CommandResult(
                    success=False, error_code="FORBIDDEN", error_message="Solo el usuario original puede cancelar"
                )
            return CommandResult(success=False, error_code="INVALID_STATE", error_message="No se puede cancelar")

        await self._audit_cancelled(cancelled)
        return CommandResult(success=True)

    # Audit helpers

    async def _audit_preview_created(self, preview: CommandPreview, payload: dict) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.preview",
            resource_type=preview.command_type.value,
            resource_id=str(preview.command_id),
            success=True,
            new_state={"preview": preview.summary, "payload": payload},
            idempotency_key=str(preview.command_id),
        )

    async def _audit_preview_denied(self, intent: CommandIntent, error_code: str, error_message: str = "") -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.preview.denied",
            resource_type=intent.command_type.value,
            success=False,
            error_code=error_code,
            error_message=error_message,
            idempotency_key=intent.request_id,
        )

    async def _audit_confirmed(self, pending: PendingCommand) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.confirm",
            resource_type=pending.command_type.value,
            resource_id=str(pending.command_id),
            success=True,
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    async def _audit_execution_success(self, pending: PendingCommand, result: CommandResult) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.execute.success",
            resource_type=pending.command_type.value,
            resource_id=str(result.resource_id) if result.resource_id else str(pending.command_id),
            success=True,
            new_state={"resource_id": result.resource_id, "idempotent": result.idempotent},
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    async def _audit_execution_failed(self, pending: PendingCommand, error_code: str, error_message: str) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.execute.failure",
            resource_type=pending.command_type.value,
            resource_id=str(pending.command_id),
            success=False,
            error_code=error_code,
            error_message=error_message,
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    async def _audit_cancelled(self, pending: PendingCommand) -> None:
        await self.audit.log_from_context(
            self.ctx,
            action="command.cancel",
            resource_type=pending.command_type.value,
            resource_id=str(pending.command_id),
            success=True,
            idempotency_key=pending.idempotency_key,
            telegram_user_id=self.user.telegram_user_id,
            dolibarr_user_id=self.user.dolibarr_user_id,
        )

    async def _find_existing_resource(self, pending: PendingCommand) -> int | None:
        """Try to find existing resource for 409 errors."""
        dolibarr = self.ctx.create_dolibarr_client()
        async with dolibarr as client:
            if pending.command_type == CommandType.CREATE_THIRDPARTY:
                vat = pending.validated_payload.get("vat_number")
                if vat:
                    existing = await client.find_thirdparty_by_tax_id(vat)
                    return existing.get("id") if existing else None
            elif pending.command_type in (CommandType.CREATE_PRODUCT, CommandType.CREATE_SERVICE):
                ref = pending.validated_payload.get("ref")
                if ref:
                    existing = await client.get_product_by_ref(ref)
                    return existing.get("id") if existing else None
        return None
