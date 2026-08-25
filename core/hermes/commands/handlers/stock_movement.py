"""
Command Layer V3 - Stock Movement Handler.

Handler for stock movements (entrada, salida, traslado, inventario) in Dolibarr.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from datetime import date

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.commands.base import CommandHandler
from core.hermes.commands.models import (
    CommandType,
    CommandPreview,
    CommandResult,
    CreateStockMovementArgs,
    StockLineArgs,
    calculate_stock_valuation,
)
from core.integrations.dolibarr.client import DolibarrException


class CreateStockMovementHandler(CommandHandler):
    """Handler for creating stock movements in Dolibarr."""

    @property
    def command_type(self) -> CommandType:
        return CommandType.CREATE_STOCK_MOVEMENT

    @property
    def required_permission(self) -> str:
        return "stock_movement.create"

    def validate_payload(self, payload: dict[str, Any] | Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        elif hasattr(payload, "dict"):
            payload = payload.dict()

        validated = {
            "tipo": payload.get("tipo"),
            "almacen_origen": payload.get("almacen_origen"),
            "almacen_destino": payload.get("almacen_destino"),
            "fecha": payload.get("fecha"),
            "lineas": payload.get("lineas", []),
            "referencia": payload.get("referencia"),
            "notas": payload.get("notas"),
        }

        if not validated["tipo"]:
            raise ValueError("Tipo de movimiento es obligatorio (entrada/salida/traslado/inventario)")
        if not validated["almacen_origen"]:
            raise ValueError("Almacén origen es obligatorio")
        if not validated["lineas"]:
            raise ValueError("Al menos una línea es obligatoria")
        if validated["tipo"] == "traslado" and not validated["almacen_destino"]:
            raise ValueError("Traslado requiere almacén destino")

        return validated

    async def _resolve_product(self, client, ref: str) -> dict | None:
        """Buscar producto por referencia."""
        return await client.get_product_by_ref(ref)

    async def _check_stock_availability(self, client, product_id: int, warehouse_id: int, qty: Decimal) -> bool:
        """Verificar disponibilidad de stock para salidas."""
        stock = await client.get_stock(product_id, warehouse_id)
        stock_reel = Decimal(str(stock.get("stock_reel", 0)))
        return stock_reel >= qty

    def _get_warehouse_id(self, client, warehouse_ref: str) -> int | None:
        """Resolver ID de almacén por referencia/nombre."""
        # Simplificado: asumimos que warehouse_ref es el ID o código
        try:
            return int(warehouse_ref)
        except ValueError:
            # Buscar por código/nombre - simplificado
            return None

    def generate_preview(self, validated_payload: dict[str, Any], company_context: CompanyContext) -> CommandPreview:
        tipo_labels = {
            "entrada": "ENTRADA (receipt)",
            "salida": "SALIDA (delivery)",
            "traslado": "TRASLADO (transfer)",
            "inventario": "INVENTARIO (adjustment)",
        }

        lines = [
            f"Voy a registrar {tipo_labels.get(validated_payload['tipo'], validated_payload['tipo'])} de stock:",
        ]

        if validated_payload.get("referencia"):
            lines.append(f"Referencia: {validated_payload['referencia']}")

        fecha = validated_payload.get("fecha") or date.today().isoformat()
        lines.append(f"Fecha: {fecha}")

        tipo = validated_payload["tipo"]
        if tipo == "traslado":
            lines.append(f"Origen: {validated_payload['almacen_origen']}")
            lines.append(f"Destino: {validated_payload['almacen_destino']}")
        else:
            lines.append(f"Almacén: {validated_payload['almacen_origen']}")

        lines.append("\nLíneas:")
        for i, line in enumerate(validated_payload["lineas"], 1):
            precio_str = f" a {line['precio_unitario']:.2f}€" if line.get("precio_unitario") else ""
            lote_str = f" (lote: {line['lote']})" if line.get("lote") else ""
            lines.append(
                f"{i}. {line['producto_ref']} × {line['cantidad']}{precio_str}{lote_str}"
            )

        if validated_payload.get("notas"):
            lines.append(f"\nNotas: {validated_payload['notas']}")

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
                tipo = validated_payload["tipo"]
                almacen_origen = validated_payload["almacen_origen"]
                almacen_destino = validated_payload.get("almacen_destino")

                # Resolve warehouse IDs
                warehouse_origin_id = self._get_warehouse_id(client, almacen_origen)
                if not warehouse_origin_id:
                    return CommandResult(
                        success=False,
                        error_code="WAREHOUSE_NOT_FOUND",
                        error_message=f"Almacén origen '{almacen_origen}' no encontrado",
                    )

                warehouse_dest_id = None
                if tipo == "traslado" and almacen_destino:
                    warehouse_dest_id = self._get_warehouse_id(client, almacen_destino)
                    if not warehouse_dest_id:
                        return CommandResult(
                            success=False,
                            error_code="WAREHOUSE_NOT_FOUND",
                            error_message=f"Almacén destino '{almacen_destino}' no encontrado",
                        )

                # Validate stock for salida/traslado
                if tipo in ("salida", "traslado"):
                    for line in validated_payload["lineas"]:
                        product = await self._resolve_product(client, line["producto_ref"])
                        if not product:
                            return CommandResult(
                                success=False,
                                error_code="PRODUCT_NOT_FOUND",
                                error_message=f"Producto '{line['producto_ref']}' no encontrado",
                            )

                        product_id = product.get("id")
                        qty = Decimal(str(line["cantidad"]))

                        if not await self._check_stock_availability(client, product_id, warehouse_origin_id, qty):
                            stock = await client.get_stock(product_id, warehouse_origin_id)
                            stock_reel = Decimal(str(stock.get("stock_reel", 0)))
                            return CommandResult(
                                success=False,
                                error_code="INSUFFICIENT_STOCK",
                                error_message=f"Stock insuficiente para '{line['producto_ref']}': disponible {stock_reel}, solicitado {qty}",
                            )

                # Build movement data
                today = date.today()
                fecha = date.fromisoformat(validated_payload["fecha"]) if validated_payload.get("fecha") else today

                tipo_map = {"entrada": 0, "salida": 1, "traslado": 2, "inventario": 3}
                movement_type = tipo_map.get(tipo, 0)

                movement_data = {
                    "type": movement_type,
                    "date": int(fecha.timestamp()),
                    "warehouse_id": warehouse_origin_id,
                    "warehouse_dest_id": warehouse_dest_id,
                    "ref": validated_payload.get("referencia", ""),
                    "note": validated_payload.get("notas", ""),
                    "lines": [],
                }

                for line in validated_payload["lineas"]:
                    product = await self._resolve_product(client, line["producto_ref"])
                    if not product:
                        return CommandResult(
                            success=False,
                            error_code="PRODUCT_NOT_FOUND",
                            error_message=f"Producto '{line['producto_ref']}' no encontrado",
                        )

                    line_data = {
                        "fk_product": product.get("id"),
                        "qty": line["cantidad"],
                    }

                    if line.get("precio_unitario"):
                        line_data["price_ht"] = line["precio_unitario"]

                    if line.get("lote"):
                        line_data["lot"] = line["lote"]

                    movement_data["lines"].append(line_data)

                # Create stock movement
                movement = await client.create_stock_movement(movement_data)
                movement_id = movement.get("id")

                if not movement_id:
                    return CommandResult(
                        success=False,
                        error_code="STOCK_MOVEMENT_CREATE_FAILED",
                        error_message="No se pudo crear el movimiento de stock",
                    )

                return CommandResult(
                    success=True,
                    resource_id=movement_id,
                    resource_type="stock_movement",
                    data={
                        "id": movement_id,
                        "tipo": tipo,
                        "almacen_origen": almacen_origen,
                        "almacen_destino": almacen_destino,
                        "lineas": len(validated_payload["lineas"]),
                    },
                )

        except DolibarrException as e:
            if e.status_code == 409:
                raise
            return CommandResult(
                success=False,
                error_code=f"DOLIBARR_{e.status_code}",
                error_message="No he podido crear el movimiento de stock",
            )
        except Exception as e:
            return CommandResult(
                success=False,
                error_code="INTERNAL_ERROR",
                error_message=f"Error interno: {e}",
            )

    def audit_data(self, result: CommandResult) -> dict[str, Any]:
        return {
            "resource_type": "stock_movement",
            "resource_id": result.resource_id,
            "tipo": result.data.get("tipo") if result.data else None,
        }