"""
Servicio de insights financieros para facturas de proveedor (Supplier).

Este servicio implementa la lógica de agregación y análisis financiero
sobre facturas de proveedor, usando exclusivamente las tools read-only existentes.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.insights.models import (
    SupplierInvoiceSummaryArgs,
    SupplierInvoiceSummaryByThirdpartyArgs,
    SupplierInvoiceSummaryByThirdpartyResult,
    SupplierInvoiceSummaryResult,
    SupplierOutstandingByThirdpartyArgs,
    SupplierOutstandingByThirdpartyResult,
    SupplierOutstandingSummaryArgs,
    SupplierOutstandingSummaryResult,
)
from core.hermes.tools import tool_registry


class SupplierFinanceInsightService:
    """
    Servicio de insights financieros para facturas de proveedor.

    Implementa la lógica de agregación y análisis financiero usando
    exclusivamente las tools read-only existentes (list_supplier_invoices, etc.).

    Principios:
    - NO accede directamente a Dolibarr ni a la base de datos
    - Usa exclusivamente las tools registradas en tool_registry
    - Agrega resultados mediante Decimal para precisión monetaria
    - Respeta paginación y límites de las tools subyacentes
    """

    def __init__(self) -> None:
        pass

    async def _execute_tool(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        tool_name: str,
        **params: Any,
    ) -> Any:
        """Ejecutar una tool y devolver su resultado (ToolResult)."""
        result = await tool_registry.execute_tool(
            instance_id=company_context.instance_id,
            name=tool_name,
            company_context=company_context,
            user_context=user_context,
            **params,
        )
        # Return the full ToolResult so caller can check success/error
        return result

    def _resolve_period(
        self,
        period: str,
        date_from: date | None,
        date_to: date | None,
        company_today: date,
    ) -> tuple[date, date]:
        """Resolver un período financiero a fechas concretas (date_from, date_to)."""
        today = company_today

        if period == "today":
            return today, today
        elif period == "current_week":
            start = today - timedelta(days=today.weekday())
            return start, today
        elif period == "current_month":
            start = today.replace(day=1)
            return start, today
        elif period == "previous_month":
            first_this_month = today.replace(day=1)
            last_day_prev_month = first_this_month - timedelta(days=1)
            start = last_day_prev_month.replace(day=1)
            return start, last_day_prev_month
        elif period == "current_quarter":
            quarter = (today.month - 1) // 3 + 1
            start = today.replace(month=(quarter - 1) * 3 + 1, day=1)
            return start, today
        elif period == "current_year":
            start = today.replace(month=1, day=1)
            return start, today
        elif period == "previous_year":
            start = today.replace(year=today.year - 1, month=1, day=1)
            end = today.replace(year=today.year - 1, month=12, day=31)
            return start, end
        elif period == "custom":
            if not date_from or not date_to:
                raise ValueError("CUSTOM period requiere date_from y date_to")
            if date_from > date_to:
                raise ValueError("date_from debe ser anterior o igual a date_to")
            return date_from, date_to
        else:
            raise ValueError(f"Período financiero desconocido: {period}")

    async def supplier_invoice_summary(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        args: dict[str, Any],
    ) -> SupplierInvoiceSummaryResult:
        """
        Resumen de facturas de proveedor en un período.

        Agrega: count, subtotal, tax, total, paid, outstanding.
        """
        args = SupplierInvoiceSummaryArgs(**args)
        company_today = company_context.get_company_today()
        date_from, date_to = self._resolve_period(args.period, args.date_from, args.date_to, company_today)

        list_params = {
            "limit": 100,
            "page": 1,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "pagination_data": True,
        }
        if args.status:
            list_params["status"] = args.status
        if args.thirdparty_id:
            list_params["thirdparty_id"] = args.thirdparty_id

        result = await self._execute_tool(
            company_context=company_context,
            user_context=user_context,
            tool_name="list_supplier_invoices",
            **list_params,
        )
        if not result.success:
            raise RuntimeError(f"Error ejecutando list_supplier_invoices: {result.error_message}")

        invoices = result.data.get("invoices", [])
        total_count = result.data.get("pagination", {}).get("total", len(invoices))

        if total_count > len(invoices):
            all_invoices = list(invoices)
            page = 2
            page_size = 100
            while len(all_invoices) < total_count:
                page_params = {
                    "limit": 100,
                    "page": page,
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                }
                if args.status:
                    page_params["status"] = args.status
                if args.thirdparty_id:
                    page_params["thirdparty_id"] = args.thirdparty_id
                page_result = await self._execute_tool(
                    company_context=company_context,
                    user_context=user_context,
                    tool_name="list_supplier_invoices",
                    **page_params,
                )
                if not page_result.success:
                    raise RuntimeError(f"Error ejecutando list_supplier_invoices: {page_result.error_message}")
                page_invoices = page_result.data if isinstance(page_result.data, list) else page_result.data.get("invoices", [])
                if not page_invoices:
                    break
                all_invoices.extend(page_invoices)
                # Use pagination metadata from result to determine if more pages exist
                page_pagination = page_result.data.get("pagination", {}) if isinstance(page_result.data, dict) else {}
                if not page_pagination.get("has_more", len(page_invoices) >= page_size):
                    break
                page += 1
            invoices = all_invoices

        # Agregar usando Decimal
        subtotal = Decimal("0")
        tax = Decimal("0")
        total = Decimal("0")
        paid = Decimal("0")
        outstanding = Decimal("0")

        for inv in invoices:
            subtotal += inv.get("total_ht", Decimal("0"))
            tax += (
                inv.get("total_tva", Decimal("0"))
                if "total_tva" in inv
                else (inv.get("total_ttc", Decimal("0")) - inv.get("total_ht", Decimal("0")))
            )
            total += inv.get("total_ttc", Decimal("0"))
            paid += inv.get("paid_amount", Decimal("0"))
            outstanding += inv.get("remaining_amount", Decimal("0"))

        if tax == Decimal("0") and total > Decimal("0") and subtotal > Decimal("0"):
            tax = total - subtotal

        period_info = {"from": date_from, "to": date_to, "period": "custom"}

        # Obtener moneda de la instancia
        currency = company_context.dolibarr_config.currency

        return SupplierInvoiceSummaryResult(
            period=period_info,
            invoice_count=len(invoices),
            subtotal=subtotal,
            tax=tax,
            total=total,
            paid=paid,
            outstanding=outstanding,
            currency=currency,
        )

    async def supplier_invoice_summary_by_thirdparty(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        args: dict[str, Any],
    ) -> SupplierInvoiceSummaryByThirdpartyResult:
        """
        Resumen de facturas de un proveedor específico en un período.
        """
        args = SupplierInvoiceSummaryByThirdpartyArgs(**args)
        company_today = company_context.get_company_today()
        date_from, date_to = self._resolve_period(args.period, args.date_from, args.date_to, company_today)

        list_params = {
            "limit": 100,
            "page": 1,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "thirdparty_id": args.thirdparty_id,
            "pagination_data": True,
        }
        if args.status:
            list_params["status"] = args.status

        result = await self._execute_tool(
            company_context=company_context,
            user_context=user_context,
            tool_name="list_supplier_invoices",
            **list_params,
        )
        if not result.success:
            raise RuntimeError(f"Error ejecutando list_supplier_invoices: {result.error_message}")

        invoices = result.data.get("invoices", [])
        total_count = result.data.get("pagination", {}).get("total", len(invoices))

        if total_count > len(invoices):
            all_invoices = list(invoices)
            page = 2
            page_size = 100
            while len(all_invoices) < total_count:
                page_params = {
                    "limit": 100,
                    "page": page,
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                    "thirdparty_id": args.thirdparty_id,
                }
                if args.status:
                    page_params["status"] = args.status
                page_result = await self._execute_tool(
                    company_context=company_context,
                    user_context=user_context,
                    tool_name="list_supplier_invoices",
                    **page_params,
                )
                if not page_result.success:
                    raise RuntimeError(f"Error ejecutando list_supplier_invoices: {page_result.error_message}")
                page_invoices = page_result.data if isinstance(page_result.data, list) else page_result.data.get("invoices", [])
                if not page_invoices:
                    break
                all_invoices.extend(page_invoices)
                # Use pagination metadata from result to determine if more pages exist
                page_pagination = page_result.data.get("pagination", {}) if isinstance(page_result.data, dict) else {}
                if not page_pagination.get("has_more", len(page_invoices) >= page_size):
                    break
                page += 1
            invoices = all_invoices


        # Agregar
        subtotal = Decimal("0")
        tax = Decimal("0")
        total = Decimal("0")
        paid = Decimal("0")
        outstanding = Decimal("0")

        for inv in invoices:
            subtotal += inv.get("total_ht", Decimal("0"))
            total += inv.get("total_ttc", Decimal("0"))
            paid += inv.get("paid_amount", Decimal("0"))
            outstanding += inv.get("remaining_amount", Decimal("0"))

        if tax == Decimal("0") and total > Decimal("0") and subtotal > Decimal("0"):
            tax = total - subtotal

        # Obtener moneda de la instancia
        currency = company_context.dolibarr_config.currency

        return SupplierInvoiceSummaryByThirdpartyResult(
            thirdparty_id=args.thirdparty_id,
            thirdparty_name=invoices[0].get("thirdparty_name", "Sin nombre") if invoices else "Sin nombre",
            invoice_count=len(invoices),
            subtotal=subtotal,
            tax=tax,
            total=total,
            paid=paid,
            outstanding=outstanding,
            currency=currency,
        )

    async def supplier_outstanding_summary(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        args: dict[str, Any],
    ) -> SupplierOutstandingSummaryResult:
        """
        Resumen de lo que debemos a proveedores (pendiente de pago).
        """
        args = SupplierOutstandingSummaryArgs(**args)
        company_today = company_context.get_company_today()
        date_from, date_to = self._resolve_period(args.period, args.date_from, args.date_to, company_today)

        list_params = {
            "limit": 100,
            "page": 1,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "pagination_data": True,
        }
        if args.status:
            list_params["status"] = args.status

        result = await self._execute_tool(
            company_context=company_context,
            user_context=user_context,
            tool_name="list_supplier_invoices",
            **list_params,
        )
        if not result.success:
            raise RuntimeError(f"Error ejecutando list_supplier_invoices: {result.error_message}")

        invoices = result.data.get("invoices", [])
        total_count = result.data.get("pagination", {}).get("total", len(invoices))

        if total_count > len(invoices):
            all_invoices = list(invoices)
            page = 2
            page_size = 100
            while len(all_invoices) < total_count:
                page_params = {
                    "limit": 100,
                    "page": page,
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                }
                if args.status:
                    page_params["status"] = args.status
                page_result = await self._execute_tool(
                    company_context=company_context,
                    user_context=user_context,
                    tool_name="list_supplier_invoices",
                    **page_params,
                )
                if not page_result.success:
                    raise RuntimeError(f"Error ejecutando list_supplier_invoices: {page_result.error_message}")
                page_invoices = page_result.data if isinstance(page_result.data, list) else page_result.data.get("invoices", [])
                if not page_invoices:
                    break
                all_invoices.extend(page_invoices)
                # Use pagination metadata from result to determine if more pages exist
                page_pagination = page_result.data.get("pagination", {}) if isinstance(page_result.data, dict) else {}
                if not page_pagination.get("has_more", len(page_invoices) >= page_size):
                    break
                page += 1
            invoices = all_invoices

        # Solo facturas con remaining > 0
        pending_invoices = [inv for inv in invoices if inv.get("remaining_amount", Decimal("0")) > Decimal("0")]

        outstanding = sum((inv.get("remaining_amount", Decimal("0")) for inv in pending_invoices), Decimal("0"))
        total_ttc = sum((inv.get("total_ttc", Decimal("0")) for inv in invoices), Decimal("0"))
        paid = sum((inv.get("paid_amount", Decimal("0")) for inv in invoices), Decimal("0"))
        subtotal = sum((inv.get("total_ht", Decimal("0")) for inv in invoices), Decimal("0"))
        tax = sum(
            (
                (
                    inv.get("total_tva", Decimal("0"))
                    if "total_tva" in inv
                    else (inv.get("total_ttc", Decimal("0")) - inv.get("total_ht", Decimal("0")))
                )
                for inv in invoices
            ),
            Decimal("0"),
        )

        if tax == Decimal("0") and total_ttc > Decimal("0") and subtotal > Decimal("0"):
            tax = total_ttc - subtotal

        period_info = {"from": date_from, "to": date_to, "period": "custom"}

        # Obtener moneda de la instancia
        currency = company_context.dolibarr_config.currency

        return SupplierOutstandingSummaryResult(
            period=period_info,
            invoice_count=len(invoices),
            subtotal=subtotal,
            tax=tax,
            total=total_ttc,
            paid=paid,
            outstanding=outstanding,
            currency=currency,
        )

    async def supplier_outstanding_by_thirdparty(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        args: dict[str, Any],
    ) -> SupplierOutstandingByThirdpartyResult:
        """
        Ranking de proveedores a los que más debemos (pendiente de pago).
        """
        args = SupplierOutstandingByThirdpartyArgs(**args)
        company_today = company_context.get_company_today()
        date_from, date_to = self._resolve_period(args.period, args.date_from, args.date_to, company_today)

        # Obtener todas las facturas pendientes en el período
        list_params = {
            "limit": 100,
            "page": 1,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "pagination_data": True,
        }
        if args.status:
            list_params["status"] = args.status

        result = await self._execute_tool(
            company_context=company_context,
            user_context=user_context,
            tool_name="list_supplier_invoices",
            **list_params,
        )
        if not result.success:
            raise RuntimeError(f"Error ejecutando list_supplier_invoices: {result.error_message}")

        invoices = result.data.get("invoices", [])
        total_count = result.data.get("pagination", {}).get("total", len(invoices))

        if total_count > len(invoices):
            all_invoices = list(invoices)
            page = 2
            page_size = 100
            while len(all_invoices) < total_count:
                page_params = {
                    "limit": page_size,
                    "page": page,
                    "date_from": date_from.isoformat(),
                    "date_to": date_to.isoformat(),
                }
                if args.status:
                    page_params["status"] = args.status
                page_result = await self._execute_tool(
                    company_context=company_context,
                    user_context=user_context,
                    tool_name="list_supplier_invoices",
                    **page_params,
                )
                if not page_result.success:
                    raise RuntimeError(f"Error ejecutando list_supplier_invoices: {page_result.error_message}")
                page_invoices = page_result.data if isinstance(page_result.data, list) else page_result.data.get("invoices", [])
                if not page_invoices:
                    break
                all_invoices.extend(page_invoices)
                # Use pagination metadata from result to determine if more pages exist
                page_pagination = page_result.data.get("pagination", {}) if isinstance(page_result.data, dict) else {}
                if not page_pagination.get("has_more", len(page_invoices) >= page_size):
                    break

                page += 1
            invoices = all_invoices

        # Agrupar por tercero
        by_thirdparty: dict[int, dict] = {}
        for inv in invoices:
            remaining = inv.get("remaining_amount", Decimal("0"))
            if remaining <= Decimal("0"):
                continue
            tp_id = inv.get("thirdparty_id", 0)
            tp_name = inv.get("thirdparty_name", "Sin nombre")
            if tp_id not in by_thirdparty:
                by_thirdparty[tp_id] = {
                    "thirdparty_id": tp_id,
                    "name": tp_name,
                    "invoice_count": 0,
                    "outstanding": Decimal("0"),
                }
            by_thirdparty[tp_id]["invoice_count"] += 1
            by_thirdparty[tp_id]["outstanding"] += inv.get("remaining_amount", Decimal("0"))

        # Ordenar por outstanding descendente y limitar
        from core.hermes.insights.models import ThirdPartyOutstandingItem

        items = [
            ThirdPartyOutstandingItem(
                thirdparty_id=v["thirdparty_id"],
                name=v["name"],
                invoice_count=v["invoice_count"],
                outstanding=v["outstanding"],
            )
            for v in by_thirdparty.values()
        ]
        items.sort(key=lambda x: x.outstanding, reverse=True)
        items = items[:args.limit]

        total_outstanding = sum((item.outstanding for item in items), Decimal("0"))

        # Obtener moneda de la instancia
        currency = company_context.dolibarr_config.currency

        return SupplierOutstandingByThirdpartyResult(
            items=items,
            total_outstanding=total_outstanding,
            currency=currency,
        )


# Importar timedelta al final para evitar import circular
