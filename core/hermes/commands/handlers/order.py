"""
Command Layer V3 - Order Handlers.

Handlers for creating orders (customer and supplier) in Dolibarr.
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
    CreateOrderArgs,
    CreateSupplierOrderArgs,
    OrderLineArgs,
)
from core.integrations.dolibarr.client import DolibarrException


class CreateOrderHandler(CommandHandler):
    """Handler for creating customer orders in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_ORDER

    @property
    def required_permission(self) -> str:
        return "order.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "cliente_query": payload.get("cliente", "").strip(),
            "fecha": payload.get("fecha"),
            "lineas": payload.get("lineas", []),
            "forma_pago": payload.get("forma_pago"),
            "serie": payload.get("serie"),
            "almacen": payload.get("almacen"),
            "proyecto": payload.get("proyecto"),
            "notas": payload.get("notas"),
        }

        if not validated["cliente_query"]:
            raise ValueError("Cliente es obligatorio")
        if not validated["lineas"]:
            raise ValueError("Al menos una línea es obligatoria")

        return validated

    def _calculate_line(self, line: dict) -> dict[str, Decimal]:
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
        }

    def _calculate_totals(self, lines: list[dict]) -> dict[str, Decimal]:
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
            "Voy a crear pedido:",
            f"Cliente: {validated_payload['cliente_query']}",
        ]

        fecha = validated_payload.get("fecha") or date.today().isoformat()
        lines.append(f"Fecha: {fecha}")

        if validated_payload.get("serie"):
            lines.append(f"Serie: {validated_payload['serie']}")
        if validated_payload.get("forma_pago"):
            lines.append(f"Forma pago: {validated_payload['forma_pago']}")
        if validated_payload.get("almacen"):
            lines.append(f"Almacén: {validated_payload['almacen']}")
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

                # 2. Prepare order header
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today

                order_data = {
                    "fk_soc": thirdparty_id,
                    "date": int(fecha.timestamp()),
                    "cond_reglement_id": validated_payload.get("cond_reglement_id"),
                    "mode_reglement_id": validated_payload.get("mode_reglement_id"),
                    "note_private": validated_payload.get("notas", ""),
                }

                if validated_payload.get("serie"):
                    order_data["ref"] = validated_payload["serie"]
                if validated_payload.get("almacen"):
                    # Buscar almacén por nombre/ref
                    pass  # Se resolvería en execute real

                # 3. Create order
                order = await client.create_order(order_data)
                order_id = order.get("id")
                order_ref = order.get("ref")

                if not order_id:
                    return CommandResult(
                        success=False,
                        error_code="ORDER_CREATE_FAILED",
                        error_message="No se pudo crear el pedido",
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

                    await client.add_order_line(order_id, line_data)

                # 5. Validate order
                await client.validate_order(order_id)

                totals = self._calculate_totals(validated_payload["lineas"])

                return CommandResult(
                    success=True,
                    resource_id=order_id,
                    resource_type="order",
                    data={
                        "id": order_id,
                        "ref": order_ref,
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
                error_message="No he podido crear el pedido",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "order",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
            "total_ttc": result.data.get("total_ttc") if result.data else None,
        }


class CreateSupplierOrderHandler(CommandHandler):
    """Handler for creating supplier orders in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_SUPPLIER_ORDER

    @property
    def required_permission(self) -> str:
        return "supplier_order.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "proveedor_query": payload.get("proveedor", "").strip(),
            "fecha": payload.get("fecha"),
            "lineas": payload.get("lineas", []),
            "forma_pago": payload.get("forma_pago"),
            "serie": payload.get("serie"),
            "almacen": payload.get("almacen"),
            "proyecto": payload.get("proyecto"),
            "notas": payload.get("notas"),
        }

        if not validated["proveedor_query"]:
            raise ValueError("Proveedor es obligatorio")
        if not validated["lineas"]:
            raise ValueError("Al menos una línea es obligatoria")

        return validated

    def _calculate_line(self, line: dict) -> dict[str, Decimal]:
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
        }

    def _calculate_totals(self, lines: list[dict]) -> dict[str, Decimal]:
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
            "Voy a crear pedido de proveedor:",
            f"Proveedor: {validated_payload['proveedor_query']}",
        ]

        fecha = validated_payload.get("fecha") or date.today().isoformat()
        lines.append(f"Fecha: {fecha}")

        if validated_payload.get("serie"):
            lines.append(f"Serie: {validated_payload['serie']}")
        if validated_payload.get("forma_pago"):
            lines.append(f"Forma pago: {validated_payload['forma_pago']}")
        if validated_payload.get("almacen"):
            lines.append(f"Almacén: {validated_payload['almacen']}")
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

                # 2. Prepare order header
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today

                order_data = {
                    "fk_soc": thirdparty_id,
                    "date": int(fecha.timestamp()),
                    "cond_reglement_id": validated_payload.get("cond_reglement_id"),
                    "mode_reglement_id": validated_payload.get("mode_reglement_id"),
                    "note_private": validated_payload.get("notas", ""),
                }

                if validated_payload.get("serie"):
                    order_data["ref"] = validated_payload["serie"]

                # 3. Create supplier order
                # Nota: Dolibarr usa endpoint 'orders' para pedidos cliente y 'supplier_orders' para proveedores
                order = await client.create_order(order_data)  # Asumiendo endpoint similar
                order_id = order.get("id")
                order_ref = order.get("ref")

                if not order_id:
                    return CommandResult(
                        success=False,
                        error_code="SUPPLIER_ORDER_CREATE_FAILED",
                        error_message="No se pudo crear el pedido de proveedor",
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

                    await client.add_order_line(order_id, line_data)

                # 5. Validate order
                await client.validate_order(order_id)

                totals = self._calculate_totals(validated_payload["lineas"])

                return CommandResult(
                    success=True,
                    resource_id=order_id,
                    resource_type="supplier_order",
                    data={
                        "id": order_id,
                        "ref": order_ref,
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
                error_message="No he podido crear el pedido de proveedor",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "supplier_order",
            "resource_id": result.resource_id,
            "ref": result.data.get("ref") if result.data else None,
            "total_ttc": result.data.get("total_ttc") if result.data else None,
        }