"""
Command Layer V1 - Create Thirdparty Handler.

Handler for creating clients/proveedores in Dolibarr.
"""

from __future__ import annotations

from typing import Any

from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import CommandPreview, CommandResult, CommandType
from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.integrations.dolibarr.client import DolibarrException


class CreateThirdpartyHandler(CommandHandler):
    """Handler for creating thirdparties (clients/suppliers)."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_THIRDPARTY

    @property
    def required_permission(self) -> str:
        return "thirdparty.create"

    def validate_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and normalize thirdparty payload."""
        # Required fields
        name = payload.get("name")
        if not name or not str(name).strip():
            raise ValueError("El nombre es obligatorio")

        name = str(name).strip()
        if len(name) > 200:
            raise ValueError("El nombre es demasiado largo (máx 200 caracteres)")

        # Optional fields with validation
        vat_number = payload.get("vat_number")
        if vat_number:
            vat_number = str(vat_number).strip().upper()
            if len(vat_number) > 50:
                raise ValueError("El CIF/NIF es demasiado largo")

        email = payload.get("email")
        if email:
            email = str(email).strip().lower()
            if "@" not in email or len(email) > 255:
                raise ValueError("Email inválido")

        phone = payload.get("phone")
        if phone:
            phone = str(phone).strip()
            if len(phone) > 50:
                raise ValueError("Teléfono demasiado largo")

        address = payload.get("address")
        if address:
            address = str(address).strip()
            if len(address) > 255:
                raise ValueError("Dirección demasiado larga")

        town = payload.get("town")
        if town:
            town = str(town).strip()
            if len(town) > 100:
                raise ValueError("Ciudad demasiado larga")

        zip_code = payload.get("zip")
        if zip_code:
            zip_code = str(zip_code).strip()
            if len(zip_code) > 20:
                raise ValueError("Código postal demasiado largo")

        # Type flags: at least one must be true
        is_customer = bool(payload.get("is_customer", False))
        is_supplier = bool(payload.get("is_supplier", False))

        if not is_customer and not is_supplier:
            raise ValueError("Debe ser al menos cliente o proveedor")

        return {
            "name": name,
            "vat_number": vat_number,
            "email": email,
            "phone": phone,
            "address": address,
            "town": town,
            "zip": zip_code,
            "is_customer": is_customer,
            "is_supplier": is_supplier,
        }

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate human-readable preview for confirmation."""
        tipo_parts = []
        if validated_payload["is_customer"]:
            tipo_parts.append("Cliente")
        if validated_payload["is_supplier"]:
            tipo_parts.append("Proveedor")
        tipo = "/".join(tipo_parts)

        lines = [
            "Voy a crear:",
            f"Tipo: {tipo}",
            f"Nombre: {validated_payload['name']}",
        ]

        if validated_payload.get("vat_number"):
            lines.append(f"CIF/NIF: {validated_payload['vat_number']}")
        if validated_payload.get("email"):
            lines.append(f"Email: {validated_payload['email']}")
        if validated_payload.get("phone"):
            lines.append(f"Teléfono: {validated_payload['phone']}")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self, company_context: CompanyContext, user_context: UserContext, validated_payload: dict[str, Any]
    ) -> CommandResult:
        """Execute thirdparty creation in Dolibarr."""
        dolibarr = company_context.create_dolibarr_client()

        # Build Dolibarr payload
        dolibarr_payload = {
            "name": validated_payload["name"],
            "client": 1 if validated_payload["is_customer"] else 0,
            "fournisseur": 1 if validated_payload["is_supplier"] else 0,
        }

        if validated_payload.get("vat_number"):
            dolibarr_payload["vat_number"] = validated_payload["vat_number"]
        if validated_payload.get("email"):
            dolibarr_payload["email"] = validated_payload["email"]
        if validated_payload.get("phone"):
            dolibarr_payload["phone"] = validated_payload["phone"]
        if validated_payload.get("address"):
            dolibarr_payload["address"] = validated_payload["address"]
        if validated_payload.get("town"):
            dolibarr_payload["town"] = validated_payload["town"]
        if validated_payload.get("zip"):
            dolibarr_payload["zip"] = validated_payload["zip"]

        try:
            async with dolibarr as client:
                result = await client.create_thirdparty(dolibarr_payload)

                thirdparty_id = result.get("id")
                if not thirdparty_id:
                    return CommandResult(
                        success=False,
                        error_code="DOLIBARR_NO_ID",
                        error_message="Dolibarr no devolvió ID del tercero creado",
                    )

                return CommandResult(
                    success=True,
                    resource_id=thirdparty_id,
                    resource_type="thirdparty",
                    data={
                        "id": thirdparty_id,
                        "name": validated_payload["name"],
                        "is_customer": validated_payload["is_customer"],
                        "is_supplier": validated_payload["is_supplier"],
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                # Duplicate - will be handled by executor as idempotent
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear el tercero",
            )
        except Exception:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message="Error interno creando el tercero",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "thirdparty",
            "resource_id": result.resource_id,
            "name": result.data.get("name") if result.data else None,
        }
