"""
Command Layer V1 - Create Product/Service Handlers.

Handlers for creating products (type=0) and services (type=1) in Dolibarr.
"""

from __future__ import annotations

from typing import Any

from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import CommandPreview, CommandResult, CommandType
from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.integrations.dolibarr.client import DolibarrException


class CreateProductHandler(CommandHandler):
    """Handler for creating products (type=0) in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_PRODUCT

    @property
    def required_permission(self) -> str:
        return "product.create"

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

        price = payload.get("price")
        if price is not None:
            try:
                price = float(price)
                if price < 0:
                    raise ValueError("El precio no puede ser negativo")
            except (ValueError, TypeError):
                raise ValueError("Precio inválido")

        vat_rate = payload.get("vat_rate")
        if vat_rate is not None:
            try:
                vat_rate = float(vat_rate)
                if vat_rate < 0 or vat_rate > 100:
                    raise ValueError("IVA debe estar entre 0 y 100")
            except (ValueError, TypeError):
                raise ValueError("IVA inválido")

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
            lines.append(f"Precio: {validated_payload['price']} {currency}")
        if validated_payload.get("vat_rate") is not None:
            lines.append(f"IVA: {validated_payload['vat_rate']}%")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self, company_context: CompanyContext, user_context: UserContext, validated_payload: dict[str, Any]
    ) -> CommandResult:
        """Execute product creation in Dolibarr."""
        dolibarr = company_context.create_dolibarr_client()

        # Build Dolibarr payload
        dolibarr_payload = {
            "ref": validated_payload["ref"],
            "label": validated_payload["label"],
            "type": 0,  # PRODUCT
        }

        if validated_payload.get("description"):
            dolibarr_payload["description"] = validated_payload["description"]
        if validated_payload.get("price") is not None:
            dolibarr_payload["price"] = validated_payload["price"]
        if validated_payload.get("vat_rate") is not None:
            dolibarr_payload["tva_tx"] = validated_payload["vat_rate"]

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
            if e.status_code == 409:
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
    """Handler for creating services (type=1) in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_SERVICE

    @property
    def required_permission(self) -> str:
        return "service.create"

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
            lines.append(f"Precio: {validated_payload['price']} {currency}")
        if validated_payload.get("vat_rate") is not None:
            lines.append(f"IVA: {validated_payload['vat_rate']}%")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self, company_context: CompanyContext, user_context: UserContext, validated_payload: dict[str, Any]
    ) -> CommandResult:
        """Execute service creation in Dolibarr."""
        dolibarr = company_context.create_dolibarr_client()

        dolibarr_payload = {
            "ref": validated_payload["ref"],
            "label": validated_payload["label"],
            "type": 1,  # SERVICE
        }

        if validated_payload.get("description"):
            dolibarr_payload["description"] = validated_payload["description"]
        if validated_payload.get("price") is not None:
            dolibarr_payload["price"] = validated_payload["price"]
        if validated_payload.get("vat_rate") is not None:
            dolibarr_payload["tva_tx"] = validated_payload["vat_rate"]

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
            if e.status_code == 409:
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
