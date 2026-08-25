"""
Command Layer V3 - Payment Handlers.

Handlers for creating payments (customer collections) and supplier payments.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from datetime import date, timedelta
from dataclasses import dataclass

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import (
    CommandType,
    CommandPreview,
    CommandResult,
    CreatePaymentArgs,
    CreateCollectionArgs,
)
from core.integrations.dolibarr.client import DolibarrException


@dataclass
class PaymentAllocation:
    """Asignación de pago a factura."""
    invoice_id: int
    amount: Decimal
    invoice_remaining: Decimal


def allocate_payment_fifo(amount: Decimal, pending_invoices: list[dict]) -> list[PaymentAllocation]:
    """Asignar pago a facturas pendientes usando FIFO (más antiguas primero)."""
    sorted_invoices = sorted(pending_invoices, key=lambda x: x.get("date", ""))

    remaining = amount
    allocations = []

    for inv in sorted_invoices:
        if remaining <= 0:
            break

        inv_remaining = Decimal(str(inv.get("remaining_amount", 0)))
        if inv_remaining <= 0:
            continue

        allocated = min(remaining, inv_remaining)
        allocated = allocated.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        allocations.append(PaymentAllocation(
            invoice_id=inv["id"],
            amount=allocated,
            invoice_remaining=(inv_remaining - allocated).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        ))

        remaining -= allocated

    return allocations


class CreatePaymentHandler(CommandHandler):
    """Handler for creating customer payments (cobros)."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_PAYMENT

    @property
    def required_permission(self) -> str:
        return "payment.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "cliente_query": payload.get("cliente", "").strip(),
            "importe": payload.get("importe"),
            "fecha": payload.get("fecha"),
            "forma_pago": payload.get("forma_pago"),
            "cuenta_bancaria": payload.get("cuenta_bancaria"),
            "facturas": payload.get("facturas"),
            "auto_allocate": payload.get("auto_allocate", True),
        }

        if not validated["cliente_query"]:
            raise ValueError("Cliente es obligatorio")
        if not validated["importe"] or validated["importe"] <= 0:
            raise ValueError("Importe debe ser > 0")

        return validated

    async def _get_pending_invoices(self, client, thirdparty_id: int) -> list[dict]:
        """Obtener facturas pendientes de cobro para un cliente (FIFO por fecha)."""
        invoices = await client.list_invoices(
            thirdparty_id=thirdparty_id,
            status=1,  # validada
            limit=100,
        )
        pending = []
        for inv in invoices:
            remaining = Decimal(str(inv.get("total_ttc", 0))) - Decimal(str(inv.get("paid_amount", 0)))
            if remaining > 0:
                pending.append({
                    "id": inv.get("id"),
                    "ref": inv.get("ref"),
                    "date": inv.get("date"),
                    "remaining_amount": float(remaining),
                })
        return pending

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        lines = [
            "Voy a registrar cobro:",
            f"Cliente: {validated_payload['cliente_query']}",
            f"Importe: {validated_payload['importe']:.2f}€",
        ]

        fecha = validated_payload.get("fecha") or date.today().isoformat()
        lines.append(f"Fecha: {fecha}")

        if validated_payload.get("forma_pago"):
            lines.append(f"Forma pago: {validated_payload['forma_pago']}")
        if validated_payload.get("cuenta_bancaria"):
            lines.append(f"Cuenta: {validated_payload['cuenta_bancaria']}")

        if validated_payload.get("auto_allocate", True):
            lines.append("\nReparto FIFO (automático):")
            lines.append("  (Se mostrará al ejecutar tras consultar facturas pendientes)")
        elif validated_payload.get("facturas"):
            lines.append("\nReparto manual:")
            for fid in validated_payload["facturas"]:
                lines.append(f"  Factura ID: {fid}")

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
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # 1. Resolve client
                cliente_query = validated_payload["cliente_query"]
                thirdparty = await client.find_thirdparty_by_tax_id(cliente_query)
                if not thirdparty:
                    thirdparties = await client.search_thirdparties(
                        query=cliente_query,
                        filter_customer=True,
                        limit=1,
                    )
                    if thirdparties:
                        thirdparty = thirdparties[0]

                if not thirdparty:
                    return CommandResult(
                        success=False,
                        error_code="CLIENT_NOT_FOUND",
                        error_message=f"Cliente '{cliente_query}' no encontrado",
                    )

                thirdparty_id = thirdparty.get("id")

                # 2. Get pending invoices for FIFO allocation
                pending_invoices = await self._get_pending_invoices(client, thirdparty_id)

                if not pending_invoices:
                    return CommandResult(
                        success=False,
                        error_code="NO_PENDING_INVOICES",
                        error_message="El cliente no tiene facturas pendientes",
                    )

                # 3. Calculate allocation
                amount = Decimal(str(validated_payload["importe"]))
                allocations = []

                if validated_payload.get("facturas"):
                    # Manual allocation - would need amounts per invoice
                    # For simplicity, allocate FIFO to specified invoices
                    pass
                else:
                    # Auto FIFO allocation
                    allocations = allocate_payment_fifo(amount, pending_invoices)

                if not allocations:
                    return CommandResult(
                        success=False,
                        error_code="ALLOCATION_FAILED",
                        error_message="No se pudo asignar el importe a facturas",
                    )

                # 4. Create payment
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today

                payment_data = {
                    "fk_soc": thirdparty_id,
                    "date": int(fecha.timestamp()),
                    "amount": float(amount),
                    "mode_reglement_id": validated_payload.get("mode_reglement_id"),
                    "fk_bank": validated_payload.get("cuenta_bancaria"),
                    "note": validated_payload.get("notas", ""),
                }

                payment = await client.create_payment(payment_data)
                payment_id = payment.get("id")

                if not payment_id:
                    return CommandResult(
                        success=False,
                        error_code="PAYMENT_CREATE_FAILED",
                        error_message="No se pudo crear el cobro",
                    )

                # 5. Allocate to invoices
                for alloc in allocations:
                    await client._request("POST", f"invoices/{alloc.invoice_id}/payments", json={
                        "fk_paiement": payment_id,
                        "amount": float(alloc.amount),
                    })

                return CommandResult(
                    success=True,
                    resource_id=payment_id,
                    resource_type="payment",
                    data={
                        "id": payment_id,
                        "cliente": validated_payload["cliente_query"],
                        "importe": str(amount),
                        "allocations": [
                            {"invoice_id": a.invoice_id, "amount": str(a.amount), "remaining": str(a.invoice_remaining)}
                            for a in allocations
                        ],
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear el cobro",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "payment",
            "resource_id": result.resource_id,
            "amount": result.data.get("importe") if result.data else None,
        }


class CreateCollectionHandler(CommandHandler):
    """Handler for creating supplier payments (pagos a proveedores)."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_COLLECTION

    @property
    def required_permission(self) -> str:
        return "collection.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "proveedor_query": payload.get("proveedor", "").strip(),
            "importe": payload.get("importe"),
            "fecha": payload.get("fecha"),
            "forma_pago": payload.get("forma_pago"),
            "cuenta_bancaria": payload.get("cuenta_bancaria"),
            "facturas": payload.get("facturas"),
            "auto_allocate": payload.get("auto_allocate", True),
        }

        if not validated["proveedor_query"]:
            raise ValueError("Proveedor es obligatorio")
        if not validated["importe"] or validated["importe"] <= 0:
            raise ValueError("Importe debe ser > 0")

        return validated

    async def _get_pending_supplier_invoices(self, client, thirdparty_id: int) -> list[dict]:
        """Obtener facturas de proveedor pendientes de pago."""
        invoices = await client.list_supplier_invoices(
            thirdparty_id=thirdparty_id,
            status=1,
            limit=100,
        )
        pending = []
        for inv in invoices:
            remaining = Decimal(str(inv.get("total_ttc", 0))) - Decimal(str(inv.get("paid_amount", 0)))
            if remaining > 0:
                pending.append({
                    "id": inv.get("id"),
                    "ref": inv.get("ref"),
                    "date": inv.get("date"),
                    "remaining_amount": float(remaining),
                })
        return pending

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        lines = [
            "Voy a registrar pago a proveedor:",
            f"Proveedor: {validated_payload['proveedor_query']}",
            f"Importe: {validated_payload['importe']:.2f}€",
        ]

        fecha = validated_payload.get("fecha") or date.today().isoformat()
        lines.append(f"Fecha: {fecha}")

        if validated_payload.get("forma_pago"):
            lines.append(f"Forma pago: {validated_payload['forma_pago']}")
        if validated_payload.get("cuenta_bancaria"):
            lines.append(f"Cuenta: {validated_payload['cuenta_bancaria']}")

        if validated_payload.get("auto_allocate", True):
            lines.append("\nReparto FIFO (automático a facturas pendientes más antiguas)")

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
        dolibarr = company_context.create_dolibarr_client()

        try:
            async with dolibarr as client:
                # 1. Resolve supplier
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
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_NOT_FOUND",
                        error_message=f"Proveedor '{proveedor_query}' no encontrado",
                    )

                thirdparty_id = thirdparty.get("id")

                # 2. Get pending supplier invoices
                pending_invoices = await self._get_pending_supplier_invoices(client, thirdparty_id)

                if not pending_invoices:
                    return CommandResult(
                        success=False,
                        error_code="NO_PENDING_INVOICES",
                        error_message="El proveedor no tiene facturas pendientes",
                    )

                # 3. Calculate allocation
                amount = Decimal(str(validated_payload["importe"]))
                allocations = allocate_payment_fifo(amount, pending_invoices)

                if not allocations:
                    return CommandResult(
                        success=False,
                        error_code="ALLOCATION_FAILED",
                        error_message="No se pudo asignar el importe",
                    )

                # 4. Create supplier payment
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today

                payment_data = {
                    "socid": thirdparty_id,
                    "date": int(fecha.timestamp()),
                    "amount": float(amount),
                    "mode_reglement_id": validated_payload.get("mode_reglement_id"),
                    "fk_bank": validated_payload.get("cuenta_bancaria"),
                }

                payment = await client.create_supplier_payment(payment_data)
                payment_id = payment.get("id")

                if not payment_id:
                    return CommandResult(
                        success=False,
                        error_code="PAYMENT_CREATE_FAILED",
                        error_message="No se pudo crear el pago",
                    )

                # 5. Allocate to supplier invoices
                for alloc in allocations:
                    await client._request("POST", f"supplier_invoices/{alloc.invoice_id}/payments", json={
                        "fk_paiementfourn": payment_id,
                        "amount": float(alloc.amount),
                    })

                return CommandResult(
                    success=True,
                    resource_id=payment_id,
                    resource_type="collection",
                    data={
                        "id": payment_id,
                        "proveedor": validated_payload["proveedor_query"],
                        "importe": str(amount),
                        "allocations": [
                            {"invoice_id": a.invoice_id, "amount": str(a.amount), "remaining": str(a.invoice_remaining)}
                            for a in allocations
                        ],
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear el pago",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "collection",
            "resource_id": result.resource_id,
            "amount": result.data.get("importe") if result.data else None,
        }