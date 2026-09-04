"""
Command Layer V1 - Create Product/Service Handlers.

Handlers for creating products (type=0) and services (type=1) in Dolibarr.

Money handling: Input as str → Decimal internally → str to Dolibarr API.
No float at any boundary.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import CommandPreview, CommandResult, CommandType
from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.integrations.dolibarr.client import DolibarrException


def _parse_decimal(value: Any, field_name: str, min_value: Decimal | None = None, max_value: Decimal | None = None) -> Decimal:
    """Parse input to Decimal safely. Input must be str/int/Decimal, not float."""
    if value is None:
        return None
    if isinstance(value, float):
        raise ValueError(f"{field_name}: float no permitido, use string (ej: '38.50')")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field_name}: valor inválido '{value}'")
    if min_value is not None and result < min_value:
        raise ValueError(f"{field_name}: debe ser >= {min_value}")
    if max_value is not None and result > max_value:
        raise ValueError(f"{field_name}: debe ser <= {max_value}")
    return result


class CreateProductHandler(CommandHandler):
    """Handler for creating products (type=0) in Dolibarr.

    ERP permission checked by Dolibarr via user's API key.
    Hermes only validates identity, cross-instance isolation, and workflow security.
    """

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_PRODUCT

    @property
    def required_permission(self) -> str:
        # No Hermes-specific permission required for this write operation.
        # Dolibarr enforces ERP permissions (produit.creer) via the user's API key.
        return ""

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize product payload."""
        # Required fields
        ref = payload.get("ref")
        if not ref or not str(ref).strip():
            raise ValueError("La referencia es obligatoria")
        ref = str(ref).strip()
        if len(ref) > 50:
            raise ValueError("La referencia es demasiado larga (máx 50 caracteres)")

        label = payload.get("label")
        if not label or not str(label).strip():
            raise ValueError("El nombre es obligatorio")
        label = str(label).strip()
        if len(label) > 255:
            raise ValueError("El nombre es demasiado largo (máx 255 caracteres)")

        # Optional fields with validation
        description = payload.get("description")
        if description:
            description = str(description).strip()
            if len(description) > 5000:
                raise ValueError("La descripción es demasiado larga")

        # Money fields: str input → Decimal internal
        price = payload.get("price")
        if price is not None:
            price = _parse_decimal(price, "price", min_value=Decimal("0"))

        vat_rate = payload.get("vat_rate")
        if vat_rate is not None:
            vat_rate = _parse_decimal(vat_rate, "vat_rate", min_value=Decimal("0"), max_value=Decimal("100"))

        return {
            "ref": ref,
            "label": label,
            "description": description,
            "price": price,
            "vat_rate": vat_rate,
            "type": 0,  # PRODUCT
        }

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate human-readable preview for confirmation."""
        currency = getattr(company_context, "currency", "EUR") or "EUR"

        lines = [
            "Voy a crear:",
            "Tipo: Producto",
            f"Referencia: {validated_payload['ref']}",
            f"Nombre: {validated_payload['label']}",
        ]

        if validated_payload.get("price") is not None:
            price = validated_payload["price"]
            lines.append(f"Precio: {price:.2f} {currency}")
        if validated_payload.get("vat_rate") is not None:
            vat = validated_payload["vat_rate"]
            lines.append(f"IVA: {vat:.2f}%")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        validated_payload: dict[str, Any],
        document_hash: str | None = None,
    ) -> CommandResult:
        """Execute product creation in Dolibarr using user's API key."""
        # Obtener TelegramIdentity del user_context para usar su API key
        from core.hermes.identity_store import IdentityStore
        identity_store = IdentityStore(company_context.instance_id)
        identity = identity_store.get(user_context.telegram_user_id)

        # Crear cliente Dolibarr usando la API key DEL USUARIO
        dolibarr = company_context.create_dolibarr_client_for_user(identity)

        # Build Dolibarr payload - pass Decimal as string for API
        dolibarr_payload = {
            "ref": validated_payload["ref"],
            "label": validated_payload["label"],
            "type": 0,  # PRODUCT
        }

        if validated_payload.get("description"):
            dolibarr_payload["description"] = validated_payload["description"]
        if validated_payload.get("price") is not None:
            dolibarr_payload["price"] = str(validated_payload["price"])
        if validated_payload.get("vat_rate") is not None:
            dolibarr_payload["tva_tx"] = str(validated_payload["vat_rate"])

        try:
            async with dolibarr as client:
                result = await client.create_product(dolibarr_payload)

                product_id = result.get("id")
                if not product_id:
                    return CommandResult(
                        success=False,
                        error_code="DOLIBARR_NO_ID",
                        error_message="Dolibarr no devolvió ID del producto creado",
                    )

                return CommandResult(
                    success=True,
                    resource_id=product_id,
                    resource_type="product",
                    data={
                        "id": product_id,
                        "ref": validated_payload["ref"],
                        "label": validated_payload["label"],
                        "type": "PRODUCT",
                    },
                )

        except DolibarrException as e:
            if e.status_code == 401:
                return CommandResult(
                    success=False,
                    error_code="DOLIBARR_AUTH_FAILED",
                    error_message="No he podido autenticar tu usuario en Dolibarr",
                )
            elif e.status_code == 403:
                return CommandResult(
                    success=False,
                    error_code="DOLIBARR_PERMISSION_DENIED",
                    error_message="No tienes permisos en Dolibarr para crear productos",
                )
            elif e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear el producto",
            )
        except Exception:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message="Error interno creando el producto",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "product",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
        }


class CreateServiceHandler(CommandHandler):
    """Handler for creating services (type=1) in Dolibarr.

    ERP permission checked by Dolibarr via user's API key.
    Hermes only validates identity, cross-instance isolation, and workflow security.
    """

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_SERVICE

    @property
    def required_permission(self) -> str:
        # No Hermes-specific permission required for this write operation.
        # Dolibarr enforces ERP permissions (produit.creer) via the user's API key.
        return ""

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize service payload (same as product)."""
        # Reuse product validation logic
        product_handler = CreateProductHandler()
        validated = product_handler.validate_payload(payload)
        validated["type"] = 1  # SERVICE
        return validated

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate human-readable preview for confirmation."""
        currency = getattr(company_context, "currency", "EUR") or "EUR"

        lines = [
            "Voy a crear:",
            "Tipo: Servicio",
            f"Referencia: {validated_payload['ref']}",
            f"Nombre: {validated_payload['label']}",
        ]

        if validated_payload.get("price") is not None:
            price = validated_payload["price"]
            lines.append(f"Precio: {price:.2f} {currency}")
        if validated_payload.get("vat_rate") is not None:
            vat = validated_payload["vat_rate"]
            lines.append(f"IVA: {vat:.2f}%")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        validated_payload: dict[str, Any],
        document_hash: str | None = None,
    ) -> CommandResult:
        """Execute service creation in Dolibarr using user's API key."""
        # Obtener TelegramIdentity del user_context para usar su API key
        from core.hermes.identity_store import IdentityStore
        identity_store = IdentityStore(company_context.instance_id)
        identity = identity_store.get(user_context.telegram_user_id)

        # Crear cliente Dolibarr usando la API key DEL USUARIO
        dolibarr = company_context.create_dolibarr_client_for_user(identity)

        dolibarr_payload = {
            "ref": validated_payload["ref"],
            "label": validated_payload["label"],
            "type": 1,  # SERVICE
        }

        if validated_payload.get("description"):
            dolibarr_payload["description"] = validated_payload["description"]
        if validated_payload.get("price") is not None:
            dolibarr_payload["price"] = str(validated_payload["price"])
        if validated_payload.get("vat_rate") is not None:
            dolibarr_payload["tva_tx"] = str(validated_payload["vat_rate"])

        try:
            async with dolibarr as client:
                result = await client.create_product(dolibarr_payload)

                service_id = result.get("id")
                if not service_id:
                    return CommandResult(
                        success=False,
                        error_code="DOLIBARR_NO_ID",
                        error_message="Dolibarr no devolvió ID del servicio creado",
                    )

                return CommandResult(
                    success=True,
                    resource_id=service_id,
                    resource_type="service",
                    data={
                        "id": service_id,
                        "ref": validated_payload["ref"],
                        "label": validated_payload["label"],
                        "type": "SERVICE",
                    },
                )

        except DolibarrException as e:
            if e.status_code == 401:
                return CommandResult(
                    success=False,
                    error_code="DOLIBARR_AUTH_FAILED",
                    error_message="No he podido autenticar tu usuario en Dolibarr",
                )
            elif e.status_code == 403:
                return CommandResult(
                    success=False,
                    error_code="DOLIBARR_PERMISSION_DENIED",
                    error_message="No tienes permisos en Dolibarr para crear servicios",
                )
            elif e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear el servicio",
            )
        except Exception:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message="Error interno creando el servicio",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "service",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
        }
