"""
Command Layer V3 - Supplier Invoice Handler.

Handler for creating supplier invoices in Dolibarr with deterministic calculations.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from datetime import date, timedelta

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import (
    CommandType,
    CommandPreview,
    CommandResult,
    CreateSupplierInvoiceArgs,
    InvoiceLineArgs,
    calculate_invoice_line,
    calculate_invoice_totals,
)
from core.integrations.dolibarr.client import DolibarrException


class CreateSupplierInvoiceHandler(CommandHandler):
    """Handler for creating supplier invoices in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_SUPPLIER_INVOICE

    @property
    def required_permission(self) -> str:
        return "supplier_invoice.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        """Validate and normalize supplier invoice payload."""
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "proveedor_query": payload.get("proveedor", "").strip(),
            "fecha": payload.get("fecha"),
            "fecha_vencimiento": payload.get("fecha_vencimiento"),
            "lineas": payload.get("lineas", []),
            "forma_pago": payload.get("forma_pago"),
            "serie": payload.get("serie"),
            "proyecto": payload.get("proyecto"),
            "notas": payload.get("notas"),
        }

        if not validated["proveedor_query"]:
            raise ValueError("Proveedor es obligatorio")
        if not validated["lineas"]:
            raise ValueError("Al menos una línea es obligatoria")

        return validated

    def _calculate_line(self, line: dict) -> dict[str, Decimal]:
        """Calcular base, IVA, total para una línea de factura proveedor."""
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

    def _calculate_totals(self, lines: list[dict]) -> dict[str, Decimal]:
        """Calcular totales de factura de proveedor (sin retención)."""
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

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        """Generate human-readable preview with full breakdown."""
        line_calcs = []
        total_base = Decimal("0")
        total_iva = Decimal("0")

        for i, line in enumerate(validated_payload["lineas"], 1):
            calc = self._calculate_line(line)
            total_base += calc["base"]
            total_iva += calc["iva"]

            line_calcs.append({
                "num": i,
                "descripcion": line["descripcion"],
                "cantidad": line["cantidad"],
                "precio": Decimal(str(line["precio_unitario"])),
                "descuento": line["descuento_porcentaje"],
                "iva": line["iva_porcentaje"],
                "base": calc["base"],
                "iva_amt": calc["iva"],
                "total": calc["total"],
            })

        total_ttc = total_base + total_iva

        lines = [
            "Voy a crear factura de proveedor:",
            f"Proveedor: {validated_payload['proveedor_query']}",
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

        lines.append("\nLíneas:")
        for lc in line_calcs:
            lines.append(
                f"{lc['num']}. {lc['descripcion']} × {lc['cantidad']} = "
                f"{lc['base']:.2f}€ + {lc['iva']:.0f}% IVA = {lc['total']:.2f}€"
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
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        validated_payload: dict[str, Any]
    ) -> CommandResult:
        """Execute supplier invoice creation in Dolibarr."""
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # 1. Resolve supplier (search by name or VAT)
                proveedor_query = validated_payload["proveedor_query"]
                thirdparty = await client.find_thirdparty_by_tax_id(proveedor_query)
                if not thirdparty:
                    thirdparties = await client.search_thirdparties(
                        query=proveedor_query,
                        filter_supplier=True,
                        limit=1,
                    )
                    if thirdparties:
                        thirdparty = thirdparties[0]

                if not thirdparty:
                    thirdparty_result = await client.create_thirdparty({
                        "name": validated_payload["proveedor_query"],
                        "fournisseur": 1,
                    })
                    thirdparty_id = thirdparty_result.get("id")
                else:
                    thirdparty_id = thirdparty.get("id")

                if not thirdparty_id:
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_RESOLVE_FAILED",
                        error_message="No se pudo resolver/crear el proveedor",
                    )

                # 2. Prepare invoice header
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today
                fecha_venc = date.fromisoformat(validated_payload["fecha_vencimiento"]) if validated_payload.get("fecha_vencimiento") else today + timedelta(days=30)

                invoice_data = {
                    "socid": thirdparty_id,
                    "date": int(fecha.timestamp()),
                    "date_lim_reglement": int(fecha_venc.timestamp()) if validated_payload.get("fecha_vencimiento") else int((today + timedelta(days=30)).timestamp()),
                    "cond_reglement_id": validated_payload.get("cond_reglement_id"),
                    "mode_reglement_id": validated_payload.get("mode_reglement_id"),
                    "note_private": validated_payload.get("notas", ""),
                }

                if validated_payload.get("serie"):
                    invoice_data["ref"] = validated_payload["serie"]

                # 3. Create supplier invoice
                invoice = await client.create_supplier_invoice(invoice_data)
                invoice_id = invoice.get("id")
                invoice_ref = invoice.get("ref")

                if not invoice_id:
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_INVOICE_CREATE_FAILED",
                        error_message="No se pudo crear la factura de proveedor",
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

                    await client.add_supplier_invoice_line(invoice_id, line_data)

                # 5. Validate invoice
                await client.validate_supplier_invoice(invoice_id)

                # 6. Calculate totals for response
                totals = self._calculate_totals(validated_payload["lineas"])

                return CommandResult(
                    success=True,
                    resource_id=invoice_id,
                    resource_type="supplier_invoice",
                    data={
                        "id": invoice_id,
                        "ref": invoice_ref,
                        "proveedor": validated_payload["proveedor_query"],
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
                error_message="No he podido crear la factura de proveedor",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "supplier_invoice",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
            "total_ttc": result.data.get("total_ttc") if result.data else None,
        }