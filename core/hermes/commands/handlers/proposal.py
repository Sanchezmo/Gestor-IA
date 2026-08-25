"""
Command Layer V2 - Create Proposal Handler.

Handler for creating proposals (presupuestos) in Dolibarr with deterministic calculations.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import (
    CommandPreview,
    CommandResult,
    CommandType,
    ProposalLineArgs,
    calculate_line_totals,
    calculate_proposal_totals,
)
from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.integrations.dolibarr.client import DolibarrException


class CreateProposalHandler(CommandHandler):
    """Handler for creating proposals with deterministic calculations."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_PROPOSAL

    @property
    def required_permission(self) -> str:
        return "proposal.create"

    def _calculate_line(self, line: ProposalLineArgs) -> dict[str, Decimal]:
        """Calcular base, IVA, total para una línea."""
        return calculate_line_totals(line)

    def _calculate_totals(self, lines: list[ProposalLineArgs]) -> dict[str, Decimal]:
        """Calcular totales del presupuesto."""
        return calculate_proposal_totals(lines)

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        """Validate and normalize proposal payload.

        Accepts either a dict (from Pydantic model_dump) or a Pydantic model.
        The payload is expected to already be validated by the IntentInterpreter.
        """
        # Convert to dict if it's a Pydantic model
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        # Payload is already validated by IntentInterpreter, just normalize
        validated = {
            "cliente_query": payload.get("cliente", "").strip(),
            "fecha": payload.get("fecha"),
            "validez_dias": payload.get("validez_dias"),
            "lineas": payload.get("lineas", []),
            "serie": payload.get("serie"),
            "forma_pago": payload.get("forma_pago"),
            "proyecto": payload.get("proyecto"),
            "notas_privadas": payload.get("notas_privadas"),
            "notas_publicas": payload.get("notas_publicas"),
        }

        # Basic validation
        if not validated["cliente_query"]:
            raise ValueError("Cliente es obligatorio")
        if not validated["lineas"]:
            raise ValueError("Al menos una línea es obligatoria")
        if validated["validez_dias"] is not None and validated["validez_dias"] < 1:
            raise ValueError("Validez debe ser >= 1 día")

        return validated

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate human-readable preview with full breakdown."""
        # Parse lines and calculate
        line_calcs = []
        total_base = Decimal("0")
        total_iva = Decimal("0")

        for i, line in enumerate(validated_payload["lineas"], 1):
            qty = Decimal(str(line["cantidad"]))
            price = Decimal(str(line["precio_unitario"]))
            discount = Decimal(str(line["descuento_porcentaje"])) / Decimal("100")
            vat_rate = Decimal(str(line["iva_porcentaje"])) / Decimal("100")

            base = qty * price * (Decimal("1") - discount)
            base = base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            iva = (base * vat_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total = base + iva

            total_base += base
            total_iva += iva

            line_calcs.append(
                {
                    "num": i,
                    "descripcion": line["descripcion"],
                    "cantidad": line["cantidad"],
                    "precio": f"{price:.2f}",
                    "descuento": f"{line['descuento_porcentaje']}%",
                    "iva": f"{line['iva_porcentaje']}%",
                    "base": f"{base:.2f}",
                    "iva_amt": f"{iva:.2f}",
                    "total": f"{total:.2f}",
                }
            )

        total_ttc = total_base + total_iva

        # Format preview
        lines = [
            "Voy a crear presupuesto:",
            f"Cliente: {validated_payload['cliente_query']}",
        ]

        fecha = validated_payload.get("fecha") or date.today().isoformat()
        validez = validated_payload.get("validez_dias") or 30
        lines.append(f"Fecha: {fecha}, Validez: {validez} días")

        if validated_payload.get("serie"):
            lines.append(f"Serie: {validated_payload['serie']}")
        if validated_payload.get("forma_pago"):
            lines.append(f"Forma pago: {validated_payload['forma_pago']}")
        if validated_payload.get("proyecto"):
            lines.append(f"Proyecto: {validated_payload['proyecto']}")

        lines.append("\nLíneas:")
        for lc in line_calcs:
            lines.append(
                f"{lc['num']}. {lc['descripcion']} × {lc['cantidad']} = "
                f"{lc['base']}€ + {lc['iva']}% IVA = {lc['total']}€"
            )

        lines.append(f"\nBase imponible: {total_base:.2f}€")
        lines.append(f"IVA: {total_iva:.2f}€")
        lines.append(f"TOTAL: {total_ttc:.2f}€")

        summary = "\n".join(lines)

        return CommandPreview(
            command_type=self.command_type,
            summary=summary,
            structured_data=validated_payload,
        )

    async def execute(
        self, company_context: CompanyContext, user_context: UserContext, validated_payload: dict[str, Any]
    ) -> CommandResult:
        """Execute proposal creation: create propal + lines + validate."""
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # 1. Resolve client (search by name or VAT)
                cliente_query = validated_payload["cliente_query"]
                thirdparty = await client.find_thirdparty_by_tax_id(cliente_query)
                if not thirdparty:
                    # Search by name
                    thirdparties = await client.search_thirdparties(
                        query=cliente_query,
                        filter_customer=True,
                        limit=1,
                    )
                    if thirdparties:
                        thirdparty = thirdparties[0]

                if not thirdparty:
                    # Create new client if not found (basic)
                    thirdparty_result = await client.create_thirdparty(
                        {
                            "name": validated_payload["cliente_query"],
                            "client": 1,
                        }
                    )
                    thirdparty_id = thirdparty_result.get("id")
                else:
                    thirdparty_id = thirdparty.get("id")

                if not thirdparty_id:
                    return CommandResult(
                        success=False,
                        error_code="CLIENT_RESOLVE_FAILED",
                        error_message="No se pudo resolver/crear el cliente",
                    )

                # 2. Prepare proposal header
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today
                validez_dias = validated_payload.get("validez_dias") or 30
                fecha_valid = fecha + timedelta(days=validez_dias)

                proposal_data = {
                    "fk_soc": thirdparty_id,
                    "date": int(fecha.timestamp()),
                    "date_valid": int(fecha_valid.timestamp()),
                    "note_private": validated_payload.get("notas_privadas", ""),
                    "note_public": validated_payload.get("notas_publicas", ""),
                }

                if validated_payload.get("serie"):
                    proposal_data["ref"] = validated_payload["serie"]

                # 3. Create proposal
                proposal = await client.create_proposal(proposal_data)
                proposal_id = proposal.get("id")
                proposal_ref = proposal.get("ref")

                if not proposal_id:
                    return CommandResult(
                        success=False,
                        error_code="PROPOSAL_CREATE_FAILED",
                        error_message="No se pudo crear la propuesta",
                    )

                # 4. Add lines
                for line in validated_payload["lineas"]:
                    line_data = {
                        "label": line["descripcion"],
                        "qty": line["cantidad"],
                        "price_ht": line["precio_unitario"],
                        "tva_tx": line["iva_porcentaje"],
                        "remise_percent": line["descuento_porcentaje"],
                    }

                    if line.get("producto_ref"):
                        product = await client.get_product_by_ref(line["producto_ref"])
                        if product:
                            line_data["fk_product"] = product.get("id")

                    await client.add_proposal_line(proposal_id, line_data)

                # 5. Validate proposal (move to status 1 = sent)
                await client.validate_proposal(proposal_id)

                # 6. Calculate totals for response
                totals = self._calculate_totals([ProposalLineArgs(**line) for line in validated_payload["lineas"]])

                return CommandResult(
                    success=True,
                    resource_id=proposal_id,
                    resource_type="proposal",
                    data={
                        "id": proposal_id,
                        "ref": proposal_ref,
                        "cliente": validated_payload["cliente_query"],
                        "total_base": str(totals["total_base"]),
                        "total_iva": str(totals["total_iva"]),
                        "total_ttc": str(totals["total_ttc"]),
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear el presupuesto",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def _calculate_totals(self, lines: list) -> dict[str, Decimal]:
        """Calcular totales del presupuesto."""
        total_base = Decimal("0")
        total_iva = Decimal("0")

        for line in lines:
            calc = self._calculate_line(line)
            total_base += calc["base"]
            total_iva += calc["iva"]

        total_ttc = total_base + total_iva

        return {
            "total_base": total_base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_iva": total_iva.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_ttc": total_ttc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        }

    def _calculate_line(self, line: dict) -> dict[str, Decimal]:
        """Calcular base, IVA, total para una línea."""
        qty = Decimal(str(line["cantidad"]))
        price = Decimal(str(line["precio_unitario"]))
        discount = Decimal(str(line["descuento_porcentaje"])) / Decimal("100")
        vat_rate = Decimal(str(line["iva_porcentaje"])) / Decimal("100")

        base = qty * price * (Decimal("1") - discount)
        base = base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        iva = base * vat_rate
        iva = iva.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total = base + iva

        return {
            "base": base,
            "iva": iva,
            "total": total,
            "vat_rate": Decimal(str(line["iva_porcentaje"])),
        }

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "proposal",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
            "total_ttc": result.data.get("total_ttc") if result.data else None,
        }
