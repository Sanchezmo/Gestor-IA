"""Base classes and utilities for secret rotation operations."""

from __future__ import annotations

import abc
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from getpass import getpass


PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class RotationResult:
    """Result of a rotation operation."""
    success: bool
    message: str
    changed_files: list[Path] = field(default_factory=list)
    changed_services: list[str] = field(default_factory=list)
    rollback_info: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class BackupInfo:
    """Information about a backup for rollback."""
    timestamp: str
    files: dict[Path, str] = field(default_factory=dict)  # path -> content
    db_state: dict[str, Any] = field(default_factory=dict)
    service_state: dict[str, bool] = field(default_factory=dict)


class SecretRotationError(Exception):
    """Base exception for rotation errors."""
    def __init__(self, message: str, rollback_info: dict | None = None):
        super().__init__(message)
        self.rollback_info = rollback_info or {}


class PreflightError(SecretRotationError):
    """Preflight check failed."""
    pass


class ValidationError(SecretRotationError):
    """Validation after change failed."""
    pass


class RotationBase(abc.ABC):
    """
    Base class for secret rotation operations.

    Each rotation follows: preflight → backup → change → validate → (rollback on failure) → post-check
    """

    def __init__(self, instance_id: str | None = None, dry_run: bool = False, verbose: bool = False):
        self.instance_id = instance_id
        self.dry_run = dry_run
        self.verbose = verbose
        self.backup_info: BackupInfo | None = None

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Human-readable name of this rotation."""
        pass

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Description of what this rotation does."""
        pass

    @abc.abstractmethod
    def preflight(self) -> None:
        """
        Check prerequisites before rotation.

        Raises:
            PreflightError: If prerequisites not met.
        """
        pass

    @abc.abstractmethod
    def backup(self) -> BackupInfo:
        """
        Create backup of current state for rollback.

        Returns:
            BackupInfo with all data needed for rollback.
        """
        pass

    @abc.abstractmethod
    def change(self, backup: BackupInfo) -> RotationResult:
        """
        Perform the actual rotation.

        Args:
            backup: Backup info from backup() phase.

        Returns:
            RotationResult with changes made.
        """
        pass

    @abc.abstractmethod
    def validate(self, result: RotationResult) -> None:
        """
        Validate the rotation worked correctly.

        Raises:
            ValidationError: If validation fails.
        """
        pass

    def rollback(self, backup: BackupInfo, result: RotationResult) -> None:
        """
        Rollback changes using backup info.

        Default implementation restores files. Override for DB/service rollback.
        """
        if self.dry_run:
            print("  [DRY RUN] Would rollback files")
            return

        print(f"  🔄 Rolling back {self.name}...")
        for path, content in backup.files.items():
            if path.exists():
                path.write_text(content)
                print(f"    Restored: {path}")
            else:
                print(f"    ⚠️  File not found (was new): {path}")

        # Restore service state if needed
        for svc, was_active in backup.service_state.items():
            try:
                if was_active:
                    subprocess.run(["sudo", "systemctl", "start", svc], check=False, capture_output=True)
                else:
                    subprocess.run(["sudo", "systemctl", "stop", svc], check=False, capture_output=True)
            except Exception:
                pass

    def post_check(self, result: RotationResult) -> None:
        """
        Final verification after successful rotation.

        Override for additional checks.
        """
        pass

    def run(self) -> RotationResult:
        """Execute full rotation pipeline."""
        print(f"\n{'='*60}")
        print(f"  ROTATION: {self.name}")
        print(f"{'='*60}")
        print(f"  {self.description}")
        if self.dry_run:
            print("  🔍 DRY RUN MODE - No changes will be made")
        print()

        # 1. PREFLIGHT
        print("  📋 PREFLIGHT CHECKS...")
        try:
            self.preflight()
            print("    ✅ Preflight passed")
        except PreflightError as e:
            print(f"    ❌ Preflight failed: {e}")
            raise

        # 2. BACKUP
        print("\n  💾 CREATING BACKUP...")
        try:
            self.backup_info = self.backup()
            print(f"    ✅ Backup created ({len(self.backup_info.files)} files)")
        except Exception as e:
            print(f"    ❌ Backup failed: {e}")
            raise SecretRotationError(f"Backup failed: {e}")

        # 3. CHANGE
        print("\n  🔧 APPLYING CHANGES...")
        try:
            result = self.change(self.backup_info)
            print(f"    ✅ Changes applied ({len(result.changed_files)} files, {len(result.changed_services)} services)")
        except Exception as e:
            print(f"    ❌ Change failed: {e}")
            raise SecretRotationError(f"Change failed: {e}")

        # 4. VALIDATE
        print("\n  ✅ VALIDATING...")
        try:
            self.validate(result)
            print("    ✅ Validation passed")
        except ValidationError as e:
            print(f"    ❌ Validation failed: {e}")
            print("    🔄 Initiating rollback...")
            self.rollback(self.backup_info, result)
            raise

        # 5. POST-CHECK
        print("\n  🔍 POST-CHECK...")
        try:
            self.post_check(result)
            print("    ✅ Post-check passed")
        except Exception as e:
            print(f"    ⚠️  Post-check warning: {e}")
            result.warnings.append(f"Post-check: {e}")

        print(f"\n  🎉 {self.name} COMPLETED SUCCESSFULLY")
        return result


def run_cmd(cmd: list[str], check: bool = True, capture: bool = True, sudo: bool = False) -> subprocess.CompletedProcess:
    """Run command with proper sudo handling."""
    if sudo:
        cmd = ["sudo", "-n"] + cmd
    return subprocess.run(cmd, capture_output=capture, text=True, check=check)


def confirm(prompt: str, default: bool = False) -> bool:
    """Ask for confirmation."""
    suffix = " [S/n]: " if default else " [s/N]: "
    resp = input(prompt + suffix).strip().lower()
    if not resp:
        return default
    return resp in ("s", "si", "sí", "y", "yes")


def prompt_secret(prompt: str, hidden: bool = True, validator: Callable[[str], bool] | None = None) -> str:
    """Prompt for a secret with optional validation."""
    while True:
        if hidden:
            value = getpass(f"{prompt}: ").strip()
        else:
            value = input(f"{prompt}: ").strip()

        if validator and not validator(value):
            print("  ❌ Valor inválido, intenta de nuevo")
            continue
        return value


def generate_password(length: int = 32, alphabet: str | None = None) -> str:
    """Generate secure password."""
    import secrets
    if alphabet is None:
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def generate_fernet_key() -> str:
    """Generate Fernet key."""
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def update_env_file(path: Path, updates: dict[str, str], backup: BackupInfo | None = None) -> list[Path]:
    """Update .env file preserving format, return changed files."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")

    # Backup original
    if backup is not None:
        backup.files[path] = path.read_text()

    lines = path.read_text().splitlines(keepends=True)
    existing = {}

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, _ = stripped.split("=", 1)
            existing[key] = i

    changed = False
    for key, value in updates.items():
        new_line = f"{key}={value}\n"
        if key in existing:
            if lines[existing[key]] != new_line:
                lines[existing[key]] = new_line
                changed = True
        else:
            lines.append(new_line)
            changed = True

    if changed or not path.exists():
        path.write_text("".join(lines))

    return [path] if changed else []


def get_instance_env_path(instance_id: str) -> Path:
    """Get path to instance.env file."""
    return PROJECT_ROOT / "instances" / instance_id / "instance.env"


def get_global_env_path() -> Path:
    """Get path to global .env file."""
    return PROJECT_ROOT / ".env"


def load_env_file(path: Path) -> dict[str, str]:
    """Load environment variables from file."""
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                env[key] = value
    return env