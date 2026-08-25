"""
Command Layer V1 - Data Models.

Core dataclasses for command infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class CommandType(StrEnum):
    """Supported command types."""

    CREATE_THIRDPARTY = "create_thirdparty"
    CREATE_PRODUCT = "create_product"
    CREATE_SERVICE = "create_service"
    CREATE_PROPOSAL = "create_proposal"
    # V3
    CREATE_INVOICE = "create_invoice"
    CREATE_INVOICE_FROM_PROPOSAL = "create_invoice_from_proposal"
    CREATE_SUPPLIER_INVOICE = "create_supplier_invoice"
    CREATE_ORDER = "create_order"
    CREATE_SUPPLIER_ORDER = "create_supplier_order"
    CREATE_PAYMENT = "create_payment"
    CREATE_COLLECTION = "create_collection"
    CREATE_STOCK_MOVEMENT = "create_stock_movement"
    CREATE_PROJECT = "create_project"
    ADD_PROJECT_TASK = "add_project_task"
    IMPORT_BC3 = "import_bc3"
    EXPORT_BC3 = "export_bc3"


class CommandStatus(StrEnum):
    """Pending command lifecycle states."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    EXECUTED = "EXECUTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class CommandIntent:
    """Structured command from NL interpretation."""

    command_type: CommandType
    payload: dict[str, Any]
    instance_id: str
    telegram_user_id: int
    dolibarr_user_id: int
    request_id: str
    correlation_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass(frozen=True, slots=True)
class CommandPreview:
    """Human-readable preview for confirmation."""

    command_type: CommandType
    summary: str
    structured_data: dict[str, Any]
    command_id: UUID = field(default_factory=uuid4)
    warnings: list[str] = field(default_factory=list)
    expires_at: datetime = field(default_factory=lambda: datetime.now() + __import__("datetime").timedelta(hours=24))


@dataclass(frozen=True, slots=True)
class PendingCommand:
    """Stored pending command awaiting confirmation."""

    command_id: UUID
    instance_id: str
    telegram_user_id: int
    dolibarr_user_id: int
    command_type: CommandType
    validated_payload: dict[str, Any]
    status: CommandStatus
    created_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None = None
    executed_at: datetime | None = None
    idempotency_key: str = field(default="")
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            object.__setattr__(self, "idempotency_key", str(self.command_id))


@dataclass(frozen=True, slots=True)
class ValidatedCommand:
    """Command after policy validation."""

    command_type: CommandType
    payload: dict[str, Any]
    policy_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Execution result."""

    success: bool
    resource_id: int | None = None
    resource_type: str | None = None
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    idempotent: bool = False


# =========================================================================
# PROPOSAL MODELS (Command Layer V2)
# =========================================================================


@dataclass(frozen=True, slots=True)
class ProposalLineArgs:
    """Argumentos para línea de presupuesto."""

    descripcion: str
    cantidad: float
    precio_unitario: float
    iva_porcentaje: float = 21.0
    descuento_porcentaje: float = 0.0
    producto_ref: str | None = None
    es_servicio: bool = False

    def __post_init__(self) -> None:
        if self.cantidad <= 0:
            raise ValueError("Cantidad debe ser > 0")
        if self.precio_unitario < 0:
            raise ValueError("Precio no puede ser negativo")
        if not (0 <= self.iva_porcentaje <= 100):
            raise ValueError("IVA debe estar entre 0 y 100")
        if not (0 <= self.descuento_porcentaje <= 100):
            raise ValueError("Descuento debe estar entre 0 y 100")


@dataclass(frozen=True, slots=True)
class CreateProposalArgs:
    """Argumentos para crear propuesta."""

    cliente: str  # nombre o CIF/NIF
    fecha: date | None = None
    validez_dias: int | None = None
    lineas: list[ProposalLineArgs] = field(default_factory=list)
    serie: str | None = None
    forma_pago: str | None = None
    proyecto: str | None = None
    notas_privadas: str | None = None
    notas_publicas: str | None = None

    def __post_init__(self) -> None:
        if not self.cliente or not self.cliente.strip():
            raise ValueError("Cliente es obligatorio")
        if not self.lineas:
            raise ValueError("Al menos una línea es obligatoria")
        if self.validez_dias is not None and self.validez_dias < 1:
            raise ValueError("Validez debe ser >= 1 día")


def calculate_line_totals(line: ProposalLineArgs) -> dict[str, Decimal]:
    """Calcular base, IVA, total para una línea."""
    qty = Decimal(str(line.cantidad))
    price = Decimal(str(line.precio_unitario))
    discount = Decimal(str(line.descuento_porcentaje)) / Decimal("100")
    vat_rate = Decimal(str(line.iva_porcentaje)) / Decimal("100")

    base = qty * price * (Decimal("1") - discount)
    base = base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    iva = base * vat_rate
    iva = iva.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    total = base + iva

    return {
        "base": base,
        "iva": iva,
        "total": total,
        "vat_rate": Decimal(str(line.iva_porcentaje)),
    }


def calculate_proposal_totals(lines: list[ProposalLineArgs]) -> dict[str, Decimal]:
    """Calcular totales del presupuesto."""
    total_base = Decimal("0")
    total_iva = Decimal("0")

    for line in lines:
        calc = calculate_line_totals(line)
        total_base += calc["base"]
        total_iva += calc["iva"]

    total_ttc = total_base + total_iva

    return {
        "total_base": total_base.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_iva": total_iva.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "total_ttc": total_ttc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
    }


# =========================================================================
# V3 MODELS
# =========================================================================


@dataclass(frozen=True, slots=True)
class InvoiceLineArgs:
    """Argumentos para línea de factura."""

    descripcion: str
    cantidad: float
    precio_unitario: float
    iva_porcentaje: float = 21.0
    descuento_porcentaje: float = 0.0
    producto_ref: str | None = None
    retencion_porcentaje: float = 0.0

    def __post_init__(self) -> None:
        if self.cantidad <= 0:
            raise ValueError("Cantidad debe ser > 0")
        if self.precio_unitario < 0:
            raise ValueError("Precio no puede ser negativo")
        if not (0 <= self.iva_porcentaje <= 100):
            raise ValueError("IVA debe estar entre 0 y 100")
        if not (0 <= self.descuento_porcentaje <= 100):
            raise ValueError("Descuento debe estar entre 0 y 100")
        if not (0 <= self.retencion_porcentaje <= 100):
            raise ValueError("Retención debe estar entre 0 y 100")


@dataclass(frozen=True, slots=True)
class CreateInvoiceArgs:
    """Argumentos para crear factura directa."""

    cliente: str  # nombre o CIF/NIF
    fecha: date | None = None
    fecha_vencimiento: date | None = None
    lineas: list[InvoiceLineArgs] = field(default_factory=list)
    forma_pago: str | None = None
    serie: str | None = None
    retencion_porcentaje: float = 0.0
    proyecto: str | None = None
    notas_privadas: str | None = None
    notas_publicas: str | None = None

    def __post_init__(self) -> None:
        if not self.cliente or not self.cliente.strip():
            raise ValueError("Cliente es obligatorio")
        if not self.lineas:
            raise ValueError("Al menos una línea es obligatoria")


@dataclass(frozen=True, slots=True)
class CreateInvoiceFromProposalArgs:
    """Argumentos para crear factura desde propuesta."""

    proposal_id: int
    fecha: date | None = None
    fecha_vencimiento: date | None = None
    forma_pago: str | None = None
    serie: str | None = None
    notas_privadas: str | None = None
    notas_publicas: str | None = None


@dataclass(frozen=True, slots=True)
class CreateSupplierInvoiceArgs:
    """Argumentos para crear factura de proveedor."""

    proveedor: str  # nombre o CIF/NIF
    fecha: date | None = None
    fecha_vencimiento: date | None = None
    lineas: list[InvoiceLineArgs] = field(default_factory=list)
    forma_pago: str | None = None
    serie: str | None = None
    proyecto: str | None = None
    notas: str | None = None

    def __post_init__(self) -> None:
        if not self.proveedor or not self.proveedor.strip():
            raise ValueError("Proveedor es obligatorio")
        if not self.lineas:
            raise ValueError("Al menos una línea es obligatoria")


@dataclass(frozen=True, slots=True)
class OrderLineArgs:
    """Argumentos para línea de pedido."""

    descripcion: str
    cantidad: float
    precio_unitario: float
    iva_porcentaje: float = 21.0
    descuento_porcentaje: float = 0.0
    producto_ref: str | None = None

    def __post_init__(self) -> None:
        if self.cantidad <= 0:
            raise ValueError("Cantidad debe ser > 0")
        if self.precio_unitario < 0:
            raise ValueError("Precio no puede ser negativo")
        if not (0 <= self.iva_porcentaje <= 100):
            raise ValueError("IVA debe estar entre 0 y 100")
        if not (0 <= self.descuento_porcentaje <= 100):
            raise ValueError("Descuento debe estar entre 0 y 100")


@dataclass(frozen=True, slots=True)
class CreateOrderArgs:
    """Argumentos para crear pedido de cliente."""

    cliente: str
    fecha: date | None = None
    lineas: list[OrderLineArgs] = field(default_factory=list)
    forma_pago: str | None = None
    serie: str | None = None
    almacen: str | None = None
    proyecto: str | None = None
    notas: str | None = None

    def __post_init__(self) -> None:
        if not self.cliente or not self.cliente.strip():
            raise ValueError("Cliente es obligatorio")
        if not self.lineas:
            raise ValueError("Al menos una línea es obligatoria")


@dataclass(frozen=True, slots=True)
class CreateSupplierOrderArgs:
    """Argumentos para crear pedido de proveedor."""

    proveedor: str
    fecha: date | None = None
    lineas: list[OrderLineArgs] = field(default_factory=list)
    forma_pago: str | None = None
    serie: str | None = None
    almacen: str | None = None
    proyecto: str | None = None
    notas: str | None = None

    def __post_init__(self) -> None:
        if not self.proveedor or not self.proveedor.strip():
            raise ValueError("Proveedor es obligatorio")
        if not self.lineas:
            raise ValueError("Al menos una línea es obligatoria")


@dataclass(frozen=True, slots=True)
class CreatePaymentArgs:
    """Argumentos para crear cobro (pago de cliente)."""

    cliente: str
    importe: float
    fecha: date | None = None
    forma_pago: str | None = None
    cuenta_bancaria: str | None = None
    facturas: list[int] | None = None  # manual allocation
    auto_allocate: bool = True

    def __post_init__(self) -> None:
        if not self.cliente or not self.cliente.strip():
            raise ValueError("Cliente es obligatorio")
        if self.importe <= 0:
            raise ValueError("Importe debe ser > 0")


@dataclass(frozen=True, slots=True)
class CreateCollectionArgs:
    """Argumentos para crear pago a proveedor."""

    proveedor: str
    importe: float
    fecha: date | None = None
    forma_pago: str | None = None
    cuenta_bancaria: str | None = None
    facturas: list[int] | None = None
    auto_allocate: bool = True

    def __post_init__(self) -> None:
        if not self.proveedor or not self.proveedor.strip():
            raise ValueError("Proveedor es obligatorio")
        if self.importe <= 0:
            raise ValueError("Importe debe ser > 0")


@dataclass(frozen=True, slots=True)
class StockLineArgs:
    """Argumentos para línea de movimiento de stock."""

    producto_ref: str
    cantidad: float
    precio_unitario: float | None = None  # para valoración
    lote: str | None = None

    def __post_init__(self) -> None:
        if self.cantidad <= 0:
            raise ValueError("Cantidad debe ser > 0")
        if self.precio_unitario is not None and self.precio_unitario < 0:
            raise ValueError("Precio no puede ser negativo")


@dataclass(frozen=True, slots=True)
class CreateStockMovementArgs:
    """Argumentos para movimiento de stock."""

    tipo: Literal["entrada", "salida", "traslado", "inventario"]
    almacen_origen: str
    almacen_destino: str | None = None  # para traslado
    fecha: date | None = None
    lineas: list[StockLineArgs] = field(default_factory=list)
    referencia: str | None = None
    notas: str | None = None

    def __post_init__(self) -> None:
        if not self.lineas:
            raise ValueError("Al menos una línea es obligatoria")
        if self.tipo == "traslado" and not self.almacen_destino:
            raise ValueError("Traslado requiere almacén destino")


@dataclass(frozen=True, slots=True)
class CreateProjectArgs:
    """Argumentos para crear proyecto."""

    nombre: str
    descripcion: str | None = None
    cliente: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    presupuesto: float | None = None
    estado: Literal["planificacion", "en_curso", "finalizado"] = "planificacion"

    def __post_init__(self) -> None:
        if not self.nombre or not self.nombre.strip():
            raise ValueError("Nombre del proyecto es obligatorio")


@dataclass(frozen=True, slots=True)
class AddProjectTaskArgs:
    """Argumentos para añadir tarea a proyecto."""

    project_id: int
    nombre: str
    descripcion: str | None = None
    fecha_inicio: date | None = None
    fecha_fin: date | None = None
    horas_estimadas: float | None = None
    coste_estimado: float | None = None

    def __post_init__(self) -> None:
        if not self.nombre or not self.nombre.strip():
            raise ValueError("Nombre de la tarea es obligatorio")


@dataclass(frozen=True, slots=True)
class ImportBC3Args:
    """Argumentos para importar BC3."""

    file_data: bytes
    nombre_proyecto: str
    vincular_productos: bool = False


@dataclass(frozen=True, slots=True)
class ExportBC3Args:
    """Argumentos para exportar BC3."""

    project_id: int


# =========================================================================
# CALCULATION FUNCTIONS V3
# =========================================================================


def calculate_invoice_line(line: InvoiceLineArgs) -> dict[str, Decimal]:
    """Calcular base, IVA, retención, total para una línea de factura."""
    qty = Decimal(str(line.cantidad))
    price = Decimal(str(line.precio_unitario))
    discount = Decimal(str(line.descuento_porcentaje)) / Decimal("100")
    vat_rate = Decimal(str(line.iva_porcentaje)) / Decimal("100")
    retention_rate = Decimal(str(line.retencion_porcentaje)) / Decimal("100")

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
        "vat_rate": Decimal(str(line.iva_porcentaje)),
        "retention_rate": Decimal(str(line.retencion_porcentaje)),
    }


def calculate_invoice_totals(lines: list[InvoiceLineArgs], header_retention_rate: float = 0.0) -> dict[str, Decimal]:
    """Calcular totales de factura (con retención a nivel cabecera)."""
    total_base = Decimal("0")
    total_iva = Decimal("0")
    total_retention = Decimal("0")

    for line in lines:
        calc = calculate_invoice_line(line)
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


@dataclass(frozen=True, slots=True)
class PaymentAllocation:
    """Asignación de pago a factura."""
    invoice_id: int
    amount: Decimal
    invoice_remaining: Decimal


def allocate_payment_fifo(amount: Decimal, pending_invoices: list[dict]) -> list[PaymentAllocation]:
    """Asignar pago a facturas pendientes usando FIFO (más antiguas primero).
    
    Args:
        amount: Importe total a asignar
        pending_invoices: Lista de dicts con 'id', 'remaining_amount', 'date'
        
    Returns:
        Lista de PaymentAllocation con factura, importe asignado, saldo restante
    """
    # Ordenar por fecha ascendente (más antiguas primero)
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


def calculate_stock_valuation(movement_type: str, lines: list[StockLineArgs], method: str = "weighted_average") -> dict[str, Decimal]:
    """Calcular impacto de valoración de stock."""
    total_value = Decimal("0")
    total_qty = Decimal("0")
    
    for line in lines:
        if line.precio_unitario is not None:
            qty = Decimal(str(line.cantidad))
            price = Decimal(str(line.precio_unitario))
            total_value += qty * price
            total_qty += qty
    
    avg_price = (total_value / total_qty).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if total_qty > 0 else Decimal("0")
    
    return {
        "total_qty": total_qty,
        "total_value": total_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "average_price": avg_price,
    }
