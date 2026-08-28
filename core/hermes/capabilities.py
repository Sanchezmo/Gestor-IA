"""
Capability Resolver - Maps Gestor-IA capabilities to ERP permissions.

Centralizes the mapping between abstract Gestor-IA capabilities
and concrete Dolibarr permission strings.

This is the SINGLE SOURCE OF TRUTH for capability -> ERP permission mapping.

ARCHITECTURE CHANGE:
====================
Dolibarr is now the SOLE AUTHORITY for ERP permissions.

Hermes ONLY manages:
- Hermes-specific capabilities (ai.use, admin, bc3.import, etc.)
- Identity resolution and cross-instance isolation
- Workflow security (preview, confirm, idempotency, audit)

Dolibarr manages ALL ERP permissions:
- thirdparty.read/create/delete
- product.read/create/delete
- customer_invoice.read/create/validate
- supplier_invoice.read/create/validate
- order.read/create
- proposal.read/create
- payment.read/create
- stock.read/create
- project.read/create
- etc.

NO capability in Hermes should mirror a Dolibarr ERP permission.
If a capability only exists to replicate a Dolibarr permission, REMOVE IT.
Dolibarr will return 403 if the user lacks the ERP permission.
"""

from __future__ import annotations

from typing import Any


# =========================================================================
# HERMES-SPECIFIC CAPABILITIES ONLY
# =========================================================================
#
# These are capabilities that Hermes itself controls and enforces.
# They are NOT mirrors of Dolibarr permissions.
# Dolibarr is the authority for ERP permissions.

HERMES_CAPABILITIES: frozenset[str] = frozenset(
    [
        # AI capabilities
        "ai.use",                    # Can use AI features
        "ai.external_provider",      # Can use external AI providers (NVIDIA, OpenAI, Anthropic)
        
        # Admin capabilities
        "admin",                     # Full admin access to Gestor-IA
        "telegram.manage",           # Manage Telegram bot configuration
        "instance.manage",           # Manage instance configuration
        "audit.read",                # Read audit logs
        
        # Content capabilities
        "content.generate",          # Generate content (marketing, etc.)
        
        # Advanced/Experimental capabilities
        "bc3.import",                # Import BC3 construction budgets (experimental)
        "mass_operations",           # Bulk operations
        "media.publish",             # Publish media
        "system.manage",             # System administration
    ]
)


# =========================================================================
# ERP PERMISSION MIRRORS - DEPRECATED / REMOVED
# =========================================================================
#
# The following capabilities were previously used to mirror Dolibarr ERP permissions.
# They are NOW REMOVED because Dolibarr is the sole authority.
#
# DEPRECATED_CAPABILITIES = {
#     "thirdparty.read", "thirdparty.create", "thirdparty.delete", "thirdparty.export", "thirdparty.import",
#     "product.read", "product.create", "product.delete", "product.export", "product.import",
#     "customer_invoice.read", "customer_invoice.create", "customer_invoice.delete", 
#     "customer_invoice.validate", "customer_invoice.export",
#     "supplier_invoice.read", "supplier_invoice.create", "supplier_invoice.delete",
#     "supplier_invoice.validate", "supplier_invoice.export",
#     "order.read", "order.create", "order.delete",
#     "supplier_order.read", "supplier_order.create",
#     "proposal.read", "proposal.create", "proposal.delete",
#     "payment.read", "payment.create",
#     "stock_movement.read", "stock_movement.create",
#     "project.read", "project.create",
#     "contact.read", "contact.create",
#     "societe.read", "societe.create", "societe.delete", "societe.export", "societe.import",
#     "user.manage",
# }
#
# DO NOT ADD NEW CAPABILITIES HERE. Use Dolibarr permissions directly.
# If you need to check an ERP permission, let Dolibarr return 403.


# =========================================================================
# CAPABILITY RESOLVER
# =========================================================================


class CapabilityResolver:
    """
    Resolves Gestor-IA capabilities.
    
    Hermes capabilities are checked locally.
    ERP permissions are delegated to Dolibarr (not checked here).
    """

    def __init__(self, hermes_capabilities: frozenset[str] | None = None):
        self._hermes_capabilities = hermes_capabilities or HERMES_CAPABILITIES

    def is_hermes_capability(self, capability: str) -> bool:
        """Check if a capability is a Hermes-specific capability (not an ERP mirror)."""
        return capability in self._hermes_capabilities

    def is_erp_mirror(self, capability: str) -> bool:
        """Check if a capability is an ERP permission mirror (deprecated)."""
        # This is for detection/validation - ERP mirrors should not be used
        return not self.is_hermes_capability(capability)

    def resolve(self, capability: str, user_permissions: frozenset[str]) -> bool:
        """
        Resolve if a user has a capability.
        
        For Hermes capabilities: check local permissions.
        For ERP mirrors: return False (let Dolibarr decide).
        
        Args:
            capability: Capability string (e.g., "ai.use", "thirdparty.read")
            user_permissions: User's effective permissions (Gestor-IA roles)
            
        Returns:
            True if granted, False if denied (default deny)
        """
        # Admin bypass for Hermes capabilities
        if "admin" in user_permissions:
            return True

        # Hermes capabilities: check locally
        if self.is_hermes_capability(capability):
            return capability in user_permissions

        # ERP mirror capabilities: NOT checked here
        # Dolibarr will enforce these via 403 responses
        return False

    def get_all_hermes_capabilities(self) -> list[str]:
        """Get all Hermes-specific capabilities."""
        return list(self._hermes_capabilities)

    def validate_capability(self, capability: str) -> tuple[bool, str]:
        """
        Validate a capability string.
        
        Returns:
            (is_valid, message)
            - is_valid: True if capability is a known Hermes capability
            - message: Explanation if invalid
        """
        if self.is_hermes_capability(capability):
            return True, "Valid Hermes capability"
        else:
            return False, f"'{capability}' is not a Hermes capability. ERP permissions are enforced by Dolibarr directly."


# Global instance (single source of truth)
_capability_resolver: CapabilityResolver | None = None


def get_capability_resolver() -> CapabilityResolver:
    """Get the global CapabilityResolver instance."""
    global _capability_resolver
    if _capability_resolver is None:
        _capability_resolver = CapabilityResolver()
    return _capability_resolver


def set_capability_resolver(resolver: CapabilityResolver) -> None:
    """Set the global CapabilityResolver (for testing)."""
    global _capability_resolver
    _capability_resolver = resolver