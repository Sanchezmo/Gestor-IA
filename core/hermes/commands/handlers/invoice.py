"""
Command Layer V3 - Invoice Handlers.

Handlers for creating invoices (direct and from proposal) in Dolibarr
with deterministic calculations including retention.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from datetime import date, datetime, timedelta

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import (
    CommandType,
    CommandPreview,
    CommandResult,
    CreateInvoiceArgs,
    CreateInvoiceFromProposalArgs,
    InvoiceLineArgs,
    calculate_invoice_line,
    calculate_invoice_totals,
)
from core.integrations.dolibarr.client import DolibarrException


class CreateInvoiceHandler(CommandHandler):
    """Handler for creating invoices directly in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_INVOICE

    @property
    def required_permission(self) -> str:
        return "invoice.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        """Validate and normalize invoice payload."""
        # Convert to dict if it's a Pydantic model
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        # Payload is already validated by IntentInterpreter, just normalize
        validated = {
            "cliente_query": payload.get("cliente", "").strip(),
            "fecha": payload.get("fecha"),
            "fecha_vencimiento": payload.get("fecha_vencimiento"),
            "lineas": payload.get("lineas", []),
            "forma_pago": payload.get("forma_pago"),
            "serie": payload.get("serie"),
            "retencion_porcentaje": payload.get("retencion_porcentaje", 0.0),
            "proyecto": payload.get("proyecto"),
            "notas_privadas": payload.get("notas_privadas"),
            "notas_publicas": payload.get("notas_publicas"),
        }

        # Basic validation
        if not validated["cliente_query"]:
            raise ValueError("Cliente es obligatorio")
        if not validated["lineas"]:
            raise ValueError("Al menos una línea es obligatoria")

        return validated

    def _calculate_line(self, line: dict) -> dict[str, Decimal]:
        """Calcular base, IVA, retención, total para una línea de factura."""
        qty = Decimal(str(line["cantidad"]))
        price = Decimal(str(line["precio_unitario"]))
        discount = Decimal(str(line["descuento_porcentaje"])) / Decimal("100")
        vat_rate = Decimal(str(line["iva_porcentaje"])) / Decimal("100")
        retention_rate = Decimal(str(line["retencion_porcentaje"])) / Decimal("100")

        base = qty * price * (Decimal("1") - discount)
        base = base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        iva = base * vat_rate
        iva = iva.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        retention = base * retention_rate
        retention = retention.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        total = base + iva - retention

        return {
            "base": base,
            "iva": iva,
            "retention": retention,
            "total": total,
            "vat_rate": Decimal(str(line["iva_porcentaje"])),
            "retention_rate": Decimal(str(line["retencion_porcentaje"])),
        }

    def _calculate_totals(self, lines: list[dict], header_retention_rate: float = 0.0) -> dict[str, Decimal]:
        """Calcular totales de factura (con retención a nivel cabecera)."""
        total_base = Decimal("0")
        total_iva = Decimal("0")
        total_retention = Decimal("0")

        for line in lines:
            calc = self._calculate_line(line)
            total_base += calc["base"]
            total_iva += calc["iva"]
            total_retention += calc["retention"]

        # Retención adicional a nivel cabecera (ej. retención profesional 7%)
        if header_retention_rate > 0:
            header_retention = total_base * (Decimal(str(header_retention_rate)) / Decimal("100"))
            header_retention = header_retention.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_retention += header_retention

        total_ttc = total_base + total_iva - total_retention

        return {
            "total_base": total_base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_iva": total_iva.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_retention": total_retention.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
            "total_ttc": total_ttc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        }

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate human-readable preview with full breakdown."""
        currency = getattr(company_context, "currency", "EUR") or "EUR"

        # Parse lines and calculate
        line_calcs = []
        total_base = Decimal("0")
        total_iva = Decimal("0")
        total_retention = Decimal("0")

        for i, line in enumerate(validated_payload["lineas"], 1):
            calc = self._calculate_line(line)
            total_base += calc["base"]
            total_iva += calc["iva"]
            total_retention += calc["retention"]

            line_calcs.append({
                "num": i,
                "descripcion": line["descripcion"],
                "cantidad": line["cantidad"],
                "precio": Decimal(str(line["precio_unitario"])),
                "descuento": line["descuento_porcentaje"],
                "iva": line["iva_porcentaje"],
                "retencion": line["retencion_porcentaje"],
                "base": calc["base"],
                "iva_amt": calc["iva"],
                "retencion_amt": calc["retention"],
                "total": calc["total"],
            })

        # Retención a nivel cabecera
        header_retention = validated_payload.get("retencion_porcentaje", 0.0)
        if header_retention > 0:
            header_retention_amt = Decimal(str(total_base)) * Decimal(str(header_retention)) / Decimal("100")
            header_retention_amt = header_retention_amt.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total_retention += header_retention_amt

        total_ttc = total_base + total_iva - total_retention

        # Format preview
        lines = [
            "Voy a crear factura:",
            f"Cliente: {validated_payload['cliente_query']}",
        ]

        fecha = validated_payload.get("fecha") or date.today().isoformat()
        fecha_venc = validated_payload.get("fecha_vencimiento")
        if fecha_venc:
            lines.append(f"Fecha: {fecha}, Vencimiento: {fecha_venc}")
        else:
            lines.append(f"Fecha: {fecha}")

        if validated_payload.get("serie"):
            lines.append(f"Serie: {validated_payload['serie']}")
        if validated_payload.get("forma_pago"):
            lines.append(f"Forma pago: {validated_payload['forma_pago']}")
        if validated_payload.get("proyecto"):
            lines.append(f"Proyecto: {validated_payload['proyecto']}")

        header_retention = validated_payload.get("retencion_porcentaje", 0.0)
        if header_retention > 0:
            lines.append(f"Retención: {header_retention}%")

        lines.append("\nLíneas:")
        for lc in line_calcs:
            ret_str = f" | Ret: {lc['retencion']:.0f}%" if lc['retencion'] > 0 else ""
            lines.append(
                f"{lc['num']}. {lc['descripcion']} × {lc['cantidad']} = "
                f"{lc['base']:.2f}€ + {lc['iva']:.0f}% IVA{ret_str} = {lc['total']:.2f}€"
            )

        lines.append(f"\nBase imponible: {total_base:.2f}€")
        lines.append(f"IVA: {total_iva:.2f}€")
        if total_retention > 0:
            lines.append(f"Retención: {total_retention:.2f}€")
        lines.append(f"TOTAL: {total_base + total_iva - total_retention:.2f}€")

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
        """Execute invoice creation in Dolibarr."""
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
                    thirdparty_result = await client.create_thirdparty({
                        "name": validated_payload["cliente_query"],
                        "client": 1,
                    })
                    thirdparty_id = thirdparty_result.get("id")
                else:
                    thirdparty_id = thirdparty.get("id")

                if not thirdparty_id:
                    return CommandResult(
                        success=False,
                        error_code="CLIENT_RESOLVE_FAILED",
                        error_message="No se pudo resolver/crear el cliente",
                    )

                # 2. Prepare invoice header
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today
                fecha_venc = date.fromisoformat(validated_payload["fecha_vencimiento"]) if validated_payload.get("fecha_vencimiento") else today + timedelta(days=30)

                invoice_data = {
                    "fk_soc": thirdparty_id,
                    "date": int(datetime.combine(fecha, datetime.min.time()).timestamp()),
                    "date_lim_reglement": int(datetime.combine(today + timedelta(days=30), datetime.min.time()).timestamp()) if not validated_payload.get("fecha_vencimiento") else int(datetime.combine(fecha_venc, datetime.min.time()).timestamp()),
                    "cond_reglement_id": validated_payload.get("cond_reglement_id"),
                    "mode_reglement_id": validated_payload.get("mode_reglement_id"),
                    "note_private": validated_payload.get("notas_privadas", ""),
                    "note_public": validated_payload.get("notas_publicas", ""),
                }

                if validated_payload.get("serie"):
                    invoice_data["ref"] = validated_payload["serie"]
                if validated_payload.get("cond_reglement_id"):
                    invoice_data["cond_reglement_id"] = validated_payload["cond_reglement_id"]
                if validated_payload.get("mode_reglement_id"):
                    invoice_data["mode_reglement_id"] = validated_payload["mode_reglement_id"]

                # 3. Create invoice
                invoice = await client.create_invoice(invoice_data)
                invoice_id = invoice.get("id")
                invoice_ref = invoice.get("ref")

                if not invoice_id:
                    return CommandResult(
                        success=False,
                        error_code="INVOICE_CREATE_FAILED",
                        error_message="No se pudo crear la factura",
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

                    if line.get("retencion_porcentaje", 0) > 0:
                        line_data["retention_tx"] = line["retencion_porcentaje"]

                    await client.add_invoice_line(invoice_id, line_data)

                # 5. Validate invoice (move to status 1)
                await client.validate_invoice(invoice_id)

                # 6. Calculate totals for response
                totals = self._calculate_totals(validated_payload["lineas"], validated_payload.get("retencion_porcentaje", 0.0))

                return CommandResult(
                    success=True,
                    resource_id=invoice_id,
                    resource_type="invoice",
                    data={
                        "id": invoice_id,
                        "ref": invoice_ref,
                        "cliente": validated_payload["cliente_query"],
                        "total_base": str(totals["total_base"]),
                        "total_iva": str(totals["total_iva"]),
                        "total_retention": str(totals["total_retention"]),
                        "total_ttc": str(totals["total_ttc"]),
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear la factura",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "invoice",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
            "total_ttc": result.data.get("total_ttc") if result.data else None,
            "total_retention": result.data.get("total_retention") if result.data else None,
        }


class CreateInvoiceFromProposalHandler(CommandHandler):
    """Handler for creating invoice from validated proposal."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_INVOICE_FROM_PROPOSAL

    @property
    def required_permission(self) -> str:
        return "invoice.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        """Validate and normalize invoice from proposal payload."""
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "proposal_id": payload.get("proposal_id"),
            "fecha": payload.get("fecha"),
            "fecha_vencimiento": payload.get("fecha_vencimiento"),
            "forma_pago": payload.get("forma_pago"),
            "serie": payload.get("serie"),
            "notas_privadas": payload.get("notas_privadas"),
            "notas_publicas": payload.get("notas_publicas"),
        }

        if not validated["proposal_id"]:
            raise ValueError("proposal_id es obligatorio")

        return validated

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate preview for proposal-to-invoice conversion."""
        currency = getattr(company_context, "currency", "EUR") or "EUR"

        summary = (
            f"Voy a facturar el presupuesto {validated_payload['proposal_id']}:\n"
            f"Se copiarán todas las líneas del presupuesto.\n"
            f"Se aplicarán IVA y retención según política de empresa.\n\n"
            f"¿Confirmas la facturación?"
        )

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
        """Execute invoice creation from proposal."""
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # Use convenience method
                overrides = {}
                if validated_payload.get("fecha"):
                    overrides["date"] = int(date.fromisoformat(validated_payload["fecha"]).timestamp())
                if validated_payload.get("fecha_vencimiento"):
                    overrides["date_lim_reglement"] = int(date.fromisoformat(validated_payload["fecha_vencimiento"]).timestamp())
                if validated_payload.get("serie"):
                    overrides["ref"] = validated_payload["serie"]
                if validated_payload.get("notas_privadas"):
                    overrides["note_private"] = validated_payload["notas_privadas"]
                if validated_payload.get("notas_publicas"):
                    overrides["note_public"] = validated_payload["notas_publicas"]

                invoice = await client.create_invoice_from_proposal(
                    validated_payload["proposal_id"],
                    overrides
                )

                invoice_id = invoice.get("id")
                invoice_ref = invoice.get("ref")

                if not invoice_id:
                    return CommandResult(
                        success=False,
                        error_code="INVOICE_FROM_PROPOSAL_FAILED",
                        error_message="No se pudo crear la factura desde el presupuesto",
                    )

                return CommandResult(
                    success=True,
                    resource_id=invoice_id,
                    resource_type="invoice",
                    data={
                        "id": invoice_id,
                        "ref": invoice_ref,
                        "proposal_id": validated_payload["proposal_id"],
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear la factura desde el presupuesto",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "invoice",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
            "proposal_id": result.data.get("proposal_id") if result.data else None,
        }