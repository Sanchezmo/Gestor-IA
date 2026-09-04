"""
Correction Applicator - Aplica correcciones validadas al borrador de factura.

Flujo:
1. Recibir estructura de cambios validada
2. Aplicar cambios SOLO a campos solicitados
3. Recalcular totales, IVA, desgloses
4. Revalidar con validador determinista
5. Retornar nuevo borrador listo para preview
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field, replace
from decimal import Decimal, ROUND_HALF_UP
from datetime import date
from typing import Any

from core.hermes.invoices.models import (
    SupplierInvoiceDraft,
    InvoiceLine,
    TaxBreakdownItem,
    WithholdingBreakdownItem,
    InvoiceFieldSource,
)
from core.hermes.invoices.validator import validate_invoice, normalize_tax_data, infer_missing_totals

logger = structlog.get_logger()


# =========================================================================
# APPLICATION RESULT
# =========================================================================

@dataclass(frozen=True, slots=True)
class ApplicationResult:
    """Resultado de aplicar correcciones."""
    success: bool
    draft: SupplierInvoiceDraft | None = None
    error: str | None = None
    error_code: str | None = None
    validation_status: str | None = None
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    validation_warnings: list[dict[str, Any]] = field(default_factory=list)


# =========================================================================
# CORRECTION APPLICATOR
# =========================================================================

class CorrectionApplicator:
    """
    Aplica correcciones a un SupplierInvoiceDraft de forma segura y determinista.
    
    Principios:
    - Solo modifica campos explícitamente solicitados
    - Preserva fuente original de campos no modificados (KNOWN > INFERRED > UNKNOWN)
    - Recalcula matemáticamente todos los totales
    - Revalida con validador determinista
    - NO toca ERP
    """

    def __init__(self):
        pass

    def apply(self, draft: SupplierInvoiceDraft, changes: dict[str, Any]) -> ApplicationResult:
        """
        Aplica cambios al borrador y retorna nuevo borrador validado.
        
        Args:
            draft: Borrador original
            changes: Estructura de cambios validada por CorrectionParser
            
        Returns:
            ApplicationResult con nuevo draft o error
        """
        logger.info(
            "correction_applying",
            draft_hash=draft.document_hash[:16],
            changes_keys=list(changes.keys()),
        )

        try:
            # Clonar líneas para mutación
            new_lines = list(draft.lines)
            new_tax_breakdown = list(draft.tax_breakdown)
            new_withholding_breakdown = list(draft.withholding_breakdown)

            # 1. Aplicar cambios de cabecera
            new_draft = self._apply_header_changes(draft, changes)

            # 2. Aplicar cambios de líneas
            new_lines = self._apply_line_changes(new_draft.lines, changes)

            # 3. Reconstruir draft con nuevas líneas
            working_draft = replace(
                new_draft,
                lines=new_lines,
            )

            # 4. Recalcular totales y desgloses (determinista)
            recalculated = self._recalculate_all(working_draft)

            # 5. Normalizar datos fiscales
            normalized = normalize_tax_data(recalculated)

            # 6. Inferir totales faltantes
            with_inferred = infer_missing_totals(normalized)

            # 7. Validar
            validation_result = validate_invoice(with_inferred)

            # 8. Actualizar estado de validación en el draft
            final_draft = replace(
                with_inferred,
                validation_status=validation_result.status,
                validation_errors=validation_result.errors,
                validation_warnings=validation_result.warnings,
            )

            logger.info(
                "correction_applied",
                draft_hash=draft.document_hash[:16],
                validation_status=validation_result.status.value,
                line_count=len(final_draft.lines),
                total=str(final_draft.total) if final_draft.total else "—",
            )

            return ApplicationResult(
                success=True,
                draft=final_draft,
                validation_status=validation_result.status.value,
                validation_errors=validation_result.errors,
                validation_warnings=validation_result.warnings,
            )

        except Exception as e:
            logger.error(
                "correction_apply_failed",
                draft_hash=draft.document_hash[:16],
                error=str(e),
            )
            return ApplicationResult(
                success=False,
                error=f"Error aplicando corrección: {type(e).__name__}",
                error_code="APPLICATION_ERROR",
            )

    def _apply_header_changes(self, draft: SupplierInvoiceDraft, changes: dict[str, Any]) -> SupplierInvoiceDraft:
        """Aplica cambios a campos de cabecera de la factura."""
        updates = {}

        # invoice_number
        if "invoice_number" in changes and changes["invoice_number"] is not None:
            updates["invoice_number"] = changes["invoice_number"]
            updates["invoice_number_source"] = InvoiceFieldSource.KNOWN  # Corrección manual = KNOWN

        # invoice_date
        if "invoice_date" in changes and changes["invoice_date"] is not None:
            updates["invoice_date"] = self._parse_date(changes["invoice_date"])
            updates["invoice_date_source"] = InvoiceFieldSource.KNOWN

        # due_date
        if "due_date" in changes and changes["due_date"] is not None:
            updates["due_date"] = self._parse_date(changes["due_date"])
            updates["due_date_source"] = InvoiceFieldSource.KNOWN

        # supplier
        if "supplier" in changes and changes["supplier"] is not None:
            supplier_changes = changes["supplier"]
            if draft.supplier:
                # Actualizar proveedor existente
                new_supplier = replace(
                    draft.supplier,
                    name=supplier_changes.get("name", draft.supplier.name) if supplier_changes.get("name") else draft.supplier.name,
                    tax_id=supplier_changes.get("tax_id", draft.supplier.tax_id) if supplier_changes.get("tax_id") else draft.supplier.tax_id,
                    address=supplier_changes.get("address") if supplier_changes.get("address") is not None else draft.supplier.address,
                    email=supplier_changes.get("email") if supplier_changes.get("email") is not None else draft.supplier.email,
                    phone=supplier_changes.get("phone") if supplier_changes.get("phone") is not None else draft.supplier.phone,
                )
            else:
                # Crear nuevo proveedor (raro en corrección, pero posible)
                new_supplier = None  # Will be handled by validation
                if supplier_changes.get("name") and supplier_changes.get("tax_id"):
                    from core.hermes.invoices.models import SupplierInfo
                    new_supplier = SupplierInfo(
                        name=supplier_changes["name"],
                        tax_id=supplier_changes["tax_id"],
                        address=supplier_changes.get("address"),
                        email=supplier_changes.get("email"),
                        phone=supplier_changes.get("phone"),
                    )
            updates["supplier"] = new_supplier

        # notes
        if "notes" in changes and changes["notes"] is not None:
            updates["notes"] = changes["notes"]

        if updates:
            return replace(draft, **updates)
        return draft

    def _apply_line_changes(self, lines: list[InvoiceLine], changes: dict[str, Any]) -> list[InvoiceLine]:
        """Aplica cambios a líneas: update, add, remove."""
        new_lines = list(lines)
        line_changes = changes.get("lines", {})

        # 1. REMOVE - eliminar líneas (procesar primero, índices descendentes)
        if "remove" in line_changes:
            # Ordenar índices descendente para no desplazar
            remove_indices = sorted(
                [r["index"] for r in line_changes["remove"] if "index" in r],
                reverse=True
            )
            for idx in remove_indices:
                if 0 <= idx < len(new_lines):
                    removed = new_lines.pop(idx)
                    logger.info("correction_line_removed", index=idx, description=removed.description[:50])

        # 2. UPDATE - modificar líneas existentes
        if "update" in line_changes:
            for update in line_changes["update"]:
                idx = update.get("index")
                if idx is None or idx < 0 or idx >= len(new_lines):
                    logger.warning("correction_update_invalid_index", index=idx, line_count=len(new_lines))
                    continue

                old_line = new_lines[idx]
                # Crear nueva línea con campos actualizados
                new_line = replace(
                    old_line,
                    description=update.get("description", old_line.description) if update.get("description") is not None else old_line.description,
                    quantity=Decimal(str(update["quantity"])) if update.get("quantity") is not None else old_line.quantity,
                    unit_price=Decimal(str(update["unit_price"])) if update.get("unit_price") is not None else old_line.unit_price,
                    vat_rate=Decimal(str(update["vat_rate"])) if update.get("vat_rate") is not None else old_line.vat_rate,
                    discount_percent=Decimal(str(update["discount_percent"])) if update.get("discount_percent") is not None else old_line.discount_percent,
                    product_ref=update.get("product_ref") if update.get("product_ref") is not None else old_line.product_ref,
                )
                new_lines[idx] = new_line
                logger.info("correction_line_updated", index=idx, description=new_line.description[:50])

        # 3. ADD - añadir nuevas líneas
        if "add" in line_changes:
            for add in line_changes["add"]:
                new_line = InvoiceLine(
                    description=add["description"],
                    quantity=Decimal(str(add["quantity"])),
                    unit_price=Decimal(str(add["unit_price"])),
                    vat_rate=Decimal(str(add.get("vat_rate", 21))),
                    discount_percent=Decimal(str(add.get("discount_percent", 0))),
                    product_ref=add.get("product_ref"),
                )
                new_lines.append(new_line)
                logger.info("correction_line_added", description=new_line.description[:50])

        return new_lines

    def _recalculate_all(self, draft: SupplierInvoiceDraft) -> SupplierInvoiceDraft:
        """Recalcula todos los campos derivados de forma determinista."""
        # Recalcular líneas (base, IVA, total por línea)
        recalculated_lines = []
        for line in draft.lines:
            qty = line.quantity
            price = line.unit_price
            discount = line.discount_percent / Decimal("100")
            vat_rate = line.vat_rate / Decimal("100")

            base = qty * price * (Decimal("1") - discount)
            base = base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            vat = base * vat_rate
            vat = vat.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

            total = base + vat

            recalculated_lines.append(replace(
                line,
                line_total_excl_tax=base,
                vat_amount=vat,
                line_total_incl_tax=total,
            ))

        # Recalcular subtotal
        subtotal = sum(l.line_total_excl_tax or Decimal("0") for l in recalculated_lines)
        subtotal = subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Recalcular tax_breakdown agrupando por vat_rate
        tax_by_rate: dict[Decimal, Decimal] = {}
        base_by_rate: dict[Decimal, Decimal] = {}

        for line in recalculated_lines:
            if line.vat_rate > 0 and line.line_total_excl_tax is not None and line.vat_amount is not None:
                tax_by_rate[line.vat_rate] = tax_by_rate.get(line.vat_rate, Decimal("0")) + line.vat_amount
                base_by_rate[line.vat_rate] = base_by_rate.get(line.vat_rate, Decimal("0")) + line.line_total_excl_tax

        new_tax_breakdown = []
        for rate, amount in sorted(tax_by_rate.items()):
            new_tax_breakdown.append(TaxBreakdownItem(
                rate=rate,
                base=base_by_rate.get(rate, Decimal("0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                amount=amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                source=InvoiceFieldSource.INFERRED,  # Recalculado = INFERRED
            ))

        tax_total = sum(t.amount for t in new_tax_breakdown)
        tax_total = tax_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Recalcular withholding_breakdown (preservar existentes, recalcular amounts si cambió base)
        new_withholding_breakdown = []
        for wh in draft.withholding_breakdown:
            # Base de retención = subtotal (típicamente)
            wh_base = subtotal
            wh_amount = (wh_base * wh.rate / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            new_withholding_breakdown.append(replace(
                wh,
                base=wh_base,
                amount=wh_amount,
                source=InvoiceFieldSource.INFERRED,
            ))

        withholding_total = sum(w.amount for w in new_withholding_breakdown)
        withholding_total = withholding_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Total final
        total = subtotal + tax_total - withholding_total
        total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return replace(
            draft,
            lines=recalculated_lines,
            tax_breakdown=new_tax_breakdown,
            withholding_breakdown=new_withholding_breakdown,
            subtotal=subtotal,
            subtotal_source=InvoiceFieldSource.INFERRED,
            tax_total=tax_total,
            tax_total_source=InvoiceFieldSource.INFERRED,
            withholding_total=withholding_total,
            withholding_total_source=InvoiceFieldSource.INFERRED if withholding_total > 0 else InvoiceFieldSource.UNKNOWN,
            total=total,
            total_source=InvoiceFieldSource.INFERRED,
        )

    def _parse_date(self, date_str: str) -> date | None:
        """Parsea fecha en formato ISO YYYY-MM-DD."""
        if not date_str:
            return None
        try:
            return date.fromisoformat(date_str)
        except Exception:
            logger.warning("correction_invalid_date_format", date_str=date_str)
            return None


# =========================================================================
# FACTORY
# =========================================================================

def create_correction_applicator() -> CorrectionApplicator:
    """Factory para crear CorrectionApplicator."""
    return CorrectionApplicator()