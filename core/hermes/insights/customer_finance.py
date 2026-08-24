"""
Servicio de insights financieros para facturas de cliente (Customer).

Este servicio implementa la lógica de agregación y análisis financiero
sobre facturas de cliente, usando exclusivamente las tools read-only existentes.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext
from core.hermes.insights.models import (
    CustomerInvoiceSummaryArgs,
    CustomerInvoiceSummaryByThirdpartyArgs,
    CustomerInvoiceSummaryByThirdpartyResult,
    CustomerInvoiceSummaryResult,
    CustomerOutstandingByThirdpartyArgs,
    CustomerOutstandingByThirdpartyResult,
    CustomerOutstandingSummaryArgs,
    CustomerOutstandingSummaryResult,
    ThirdPartyOutstandingItem,
)
from core.hermes.tools import tool_registry


class CustomerFinanceInsightService:
    """
    Servicio de insights financieros para facturas de cliente.

    Implementa la lógica de agregación y análisis financiero usando
    exclusivamente las tools read-only existentes (list_customer_invoices, etc.).

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
        """Ejecutar una tool y devolver su resultado."""
        result = await tool_registry.execute_tool(
            instance_id=company_context.instance_id,
            name=tool_name,
            company_context=company_context,
            user_context=user_context,
            **params,
        )
        if not result.success:
            raise RuntimeError(f"Error ejecutando {tool_name}: {result.error_message}")
        return result.data

    def _resolve_period(
        self,
        period: str,
        date_from: date | None,
        date_to: date | None,
    ) -> tuple[date, date]:
        """Resolver un período financiero a fechas concretas (date_from, date_to)."""
        today = date.today()

        if period == "today":
            return today, today
        elif period == "current_week":
            # Monday of current week
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
            return date_from, date_to
        else:
            # Default a mes actual
            start = today.replace(day=1)
            return start, today

    async def customer_invoice_summary(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        args: dict[str, Any],
    ) -> CustomerInvoiceSummaryResult:
        """
        Resumen de facturas de cliente en un período.

        Agrega: count, subtotal, tax, total, paid, outstanding.
        """
        args = CustomerInvoiceSummaryArgs(**args)
        date_from, date_to = self._resolve_period(args.period, args.date_from, args.date_to)

        # Ejecutar tool con pagination_data para obtener total
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
            tool_name="list_customer_invoices",
            **list_params,
        )

        invoices = result.get("invoices", [])
        total_count = result.get("pagination", {}).get("total", len(invoices))

        # Si hay más páginas, necesitamos iterar para obtener todos los datos
        if total_count > len(invoices):
            all_invoices = list(invoices)
            page = 2
            page_size = 100
            while len(all_invoices) < total_count:
                page_params = dict(list_params)
                page_params["page"] = page
                page_params["limit"] = page_size
                page_params.pop("pagination_data", None)
                page_result = await self._execute_tool(
                    company_context=company_context,
                    user_context=user_context,
                    tool_name="list_customer_invoices",
                    **page_params,
                )
                page_invoices = page_result if isinstance(page_result, list) else page_result.get("invoices", [])
                if not page_invoices:
                    break
                all_invoices.extend(page_invoices)
                if len(page_invoices) < page_size:
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

        # Calcular tax si no viene explícito
        if tax == Decimal("0") and total > Decimal("0") and subtotal > Decimal("0"):
            tax = total - subtotal

        period_info = {"from": date_from, "to": date_to, "period": "custom"}

        return CustomerInvoiceSummaryResult(
            period=period_info,
            invoice_count=len(invoices),
            subtotal=subtotal,
            tax=tax,
            total=total,
            paid=paid,
            outstanding=outstanding,
            currency="EUR",
        )

    async def customer_invoice_summary_by_thirdparty(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        args: dict[str, Any],
    ) -> CustomerInvoiceSummaryByThirdpartyResult:
        """
        Resumen de facturas de un cliente específico en un período.
        """
        args = CustomerInvoiceSummaryByThirdpartyArgs(**args)
        date_from, date_to = self._resolve_period(args.period, args.date_from, args.date_to)

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
            tool_name="list_customer_invoices",
            **list_params,
        )

        invoices = result.get("invoices", [])
        total_count = result.get("pagination", {}).get("total", len(invoices))

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
                    "thirdparty_id": args.thirdparty_id,
                }
                if args.status:
                    page_params["status"] = args.status
                page_result = await self._execute_tool(
                    company_context=company_context,
                    user_context=user_context,
                    tool_name="list_customer_invoices",
                    **page_params,
                )
                page_invoices = page_result if isinstance(page_result, list) else page_result.get("invoices", [])
                if not page_invoices:
                    break
                all_invoices.extend(page_invoices)
                if len(page_invoices) < page_size:
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

        return CustomerInvoiceSummaryByThirdpartyResult(
            thirdparty_id=args.thirdparty_id,
            thirdparty_name=invoices[0].get("thirdparty_name", "Sin nombre") if invoices else "Sin nombre",
            invoice_count=len(invoices),
            subtotal=subtotal,
            tax=tax,
            total=total,
            paid=paid,
            outstanding=outstanding,
            currency="EUR",
        )

    async def customer_outstanding_summary(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        args: dict[str, Any],
    ) -> CustomerOutstandingSummaryResult:
        """
        Resumen de lo que nos deben los clientes (pendiente de cobro).
        """
        args = CustomerOutstandingSummaryArgs(**args)
        date_from, date_to = self._resolve_period(args.period, args.date_from, args.date_to)

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
            tool_name="list_customer_invoices",
            **list_params,
        )

        invoices = result.get("invoices", [])
        total_count = result.get("pagination", {}).get("total", len(invoices))

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
                    tool_name="list_customer_invoices",
                    **page_params,
                )
                page_invoices = page_result if isinstance(page_result, list) else page_result.get("invoices", [])
                if not page_invoices:
                    break
                all_invoices.extend(page_invoices)
                if len(page_invoices) < page_size:
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
                inv.get("total_tva", Decimal("0"))
                if "total_tva" in inv
                else (inv.get("total_ttc", Decimal("0")) - inv.get("total_ht", Decimal("0")))
            )
            for inv in invoices
        ),
        Decimal("0"),

        if tax == Decimal("0") and total_ttc > Decimal("0") and subtotal > Decimal("0"):
            tax = total_ttc - subtotal

        period_info = {"from": date_from, "to": date_to, "period": "custom"}

        return CustomerOutstandingSummaryResult(
            period=period_info,
            invoice_count=len(invoices),
            subtotal=subtotal,
            tax=tax,
            total=total_ttc,
            paid=paid,
            outstanding=outstanding,
            currency="EUR",
        )

    async def customer_outstanding_by_thirdparty(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        args: dict[str, Any],
    ) -> CustomerOutstandingByThirdpartyResult:
        """
        Ranking de clientes que más nos deben (pendiente de cobro).
        """
        args = CustomerOutstandingByThirdpartyArgs(**args)
        date_from, date_to = self._resolve_period(args.period, args.date_from, args.date_to)

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
            tool_name="list_customer_invoices",
            **list_params,
        )

        invoices = result.get("invoices", [])
        total_count = result.get("pagination", {}).get("total", len(invoices))

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
                    tool_name="list_customer_invoices",
                    **page_params,
                )
                page_invoices = page_result if isinstance(page_result, list) else page_result.get("invoices", [])
                if not page_invoices:
                    break
                all_invoices.extend(page_invoices)
                if len(page_invoices) < page_size:
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

        return CustomerOutstandingByThirdpartyResult(
            items=items,
            total_outstanding=total_outstanding,
            currency="EUR",
        )


# Importar timedelta al final para evitar import circular
