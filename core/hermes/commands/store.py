"""
Command Layer V1 - Pending Command Store.

Redis-backed storage with atomic operations and TTL.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import redis

from core.hermes.config import get_global_settings

from .models import CommandStatus, CommandType, PendingCommand


class PendingCommandStore:
    """Redis-backed store for pending commands with TTL and atomic operations."""

    KEY_PREFIX = "pending:command:"
    DEFAULT_TTL = timedelta(hours=24)

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        settings = get_global_settings()
        self._redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD or None,
            db=instance_id,  # Use instance-specific Redis DB
            decode_responses=True,
        )

    def _key(self, command_id: UUID) -> str:
        return f"{self.KEY_PREFIX}{command_id}"

    def create(self, pending: PendingCommand) -> None:
        """Create pending command. Atomic SET NX EX for idempotency."""
        key = self._key(pending.command_id)
        data = self._serialize(pending)
        # Atomic: only create if not exists, with TTL
        if not self._redis.set(key, json.dumps(data), nx=True, ex=int(self.DEFAULT_TTL.total_seconds())):
            raise ValueError(f"Command {pending.command_id} already exists")

    def get(self, command_id: UUID) -> PendingCommand | None:
        """Get pending command by ID."""
        key = self._key(command_id)
        data = self._redis.get(key)
        if not data:
            return None
        return self._deserialize(json.loads(data))

    def update_status(self, command_id: UUID, status: CommandStatus, **extra: Any) -> bool:
        """
        Atomic status update with WATCH/MULTI/EXEC.
        Returns False if command not found or in terminal state.
        """
        key = self._key(command_id)
        with self._redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    current = pipe.get(key)
                    if not current:
                        return False

                    pending = self._deserialize(json.loads(current))

                    # Terminal states cannot be updated
                    if pending.status in (
                        CommandStatus.EXECUTED,
                        CommandStatus.CANCELLED,
                        CommandStatus.EXPIRED,
                        CommandStatus.FAILED,
                    ):
                        return False

                    # Apply updates
                    updated = self._apply_updates(pending, status, **extra)

                    pipe.multi()
                    pipe.set(key, json.dumps(self._serialize(updated)), ex=int(self.DEFAULT_TTL.total_seconds()))
                    pipe.execute()
                    return True

                except redis.WatchError:
                    continue
                finally:
                    pipe.unwatch()

    def confirm(self, command_id: UUID, telegram_user_id: int) -> PendingCommand | None:
        """
        Atomic confirm: validate user + instance + not expired + PENDING → CONFIRMED.
        Returns updated command or None if validation failed.
        """
        key = self._key(command_id)
        with self._redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    current = pipe.get(key)
                    if not current:
                        return None

                    pending = self._deserialize(json.loads(current))

                    # Validate: same user, PENDING status, not expired
                    if pending.telegram_user_id != telegram_user_id:
                        return None
                    if pending.status != CommandStatus.PENDING:
                        return None
                    if pending.expires_at < datetime.now():
                        return None

                    # Update to CONFIRMED
                    updated = self._apply_updates(pending, CommandStatus.CONFIRMED, confirmed_at=datetime.now())

                    pipe.multi()
                    pipe.set(key, json.dumps(self._serialize(updated)), ex=int(self.DEFAULT_TTL.total_seconds()))
                    pipe.execute()
                    return updated

                except redis.WatchError:
                    continue
                finally:
                    pipe.unwatch()

    def cancel(self, command_id: UUID, telegram_user_id: int) -> PendingCommand | None:
        """Atomic cancel: validate user + PENDING → CANCELLED."""
        key = self._key(command_id)
        with self._redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    current = pipe.get(key)
                    if not current:
                        return None

                    pending = self._deserialize(json.loads(current))

                    if pending.telegram_user_id != telegram_user_id:
                        return None
                    if pending.status != CommandStatus.PENDING:
                        return None

                    updated = self._apply_updates(pending, CommandStatus.CANCELLED)

                    pipe.multi()
                    pipe.set(key, json.dumps(self._serialize(updated)), ex=int(self.DEFAULT_TTL.total_seconds()))
                    pipe.execute()
                    return updated

                except redis.WatchError:
                    continue
                finally:
                    pipe.unwatch()

    def _serialize(self, pending: PendingCommand) -> dict[str, Any]:
        return {
            "command_id": str(pending.command_id),
            "instance_id": pending.instance_id,
            "telegram_user_id": pending.telegram_user_id,
            "dolibarr_user_id": pending.dolibarr_user_id,
            "command_type": pending.command_type.value,
            "validated_payload": pending.validated_payload,
            "status": pending.status.value,
            "created_at": pending.created_at.isoformat(),
            "expires_at": pending.expires_at.isoformat(),
            "confirmed_at": pending.confirmed_at.isoformat() if pending.confirmed_at else None,
            "executed_at": pending.executed_at.isoformat() if pending.executed_at else None,
            "idempotency_key": pending.idempotency_key,
            "result": pending.result,
            "error_code": pending.error_code,
            "error_message": pending.error_message,
        }

    def _deserialize(self, data: dict[str, Any]) -> PendingCommand:
        return PendingCommand(
            command_id=UUID(data["command_id"]),
            instance_id=data["instance_id"],
            telegram_user_id=data["telegram_user_id"],
            dolibarr_user_id=data["dolibarr_user_id"],
            command_type=CommandType(data["command_type"]),
            validated_payload=data["validated_payload"],
            status=CommandStatus(data["status"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            expires_at=datetime.fromisoformat(data["expires_at"]),
            confirmed_at=datetime.fromisoformat(data["confirmed_at"]) if data["confirmed_at"] else None,
            executed_at=datetime.fromisoformat(data["executed_at"]) if data["executed_at"] else None,
            idempotency_key=data["idempotency_key"],
            result=data["result"],
            error_code=data["error_code"],
            error_message=data["error_message"],
        )

    def _apply_updates(self, pending: PendingCommand, status: CommandStatus, **extra: Any) -> PendingCommand:
        updates = {"status": status}
        updates.update(extra)
        if status == CommandStatus.CONFIRMED:
            updates["confirmed_at"] = datetime.now()
        elif status == CommandStatus.EXECUTED:
            updates["executed_at"] = datetime.now()
        return PendingCommand(**{**self._serialize(pending), **updates})

    def close(self) -> None:
        """Close Redis connection."""
        self._redis.close()
