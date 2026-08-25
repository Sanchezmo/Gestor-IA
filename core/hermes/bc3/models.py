"""
BC3 Advanced - Modelos de dominio para catálogo técnico BC3.

Estructura 4-niveles:
1. Capítulo (ej: 01 - Movimiento de tierras)
2. Subcapítulo (ej: 01.01 - Excavaciones)
3. Item/Recurso (ej: 01.01.01 - Excavación en zanja)
4. Descomposición (ej: Mano de obra + Maquinaria + Materiales)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class BC3ResourceType(StrEnum):
    """Tipos de recursos según norma BC3."""

    MATERIAL = "1"          # Materiales
    LABOR = "2"             # Mano de obra
    MACHINERY = "3"         # Maquinaria
    AUXILIARY = "4"         # Auxiliares
    SUBCONTRACTING = "5"    # Subcontratación
    OTHER = "6"             # Otros

    @property
    def label(self) -> str:
        labels = {
            "1": "Materiales",
            "2": "Mano de obra",
            "3": "Maquinaria",
            "4": "Auxiliares",
            "5": "Subcontratación",
            "6": "Otros",
        }
        return labels.get(self.value, "Desconocido")


class BC3Unit(StrEnum):
    """Unidades de medida normalizadas BC3."""

    M2 = "m2"       # Metro cuadrado
    M3 = "m3"       # Metro cúbico
    ML = "ml"       # Metro lineal
    KG = "kg"       # Kilogramo
    TON = "t"       # Tonelada
    UN = "ud"       # Unidad
    H = "h"         # Hora
    CJ = "cj"       # Conjunto
    GL = "gl"       # Globo (para pintura)
    PR = "pr"       # Par
    PZ = "pz"       # Pieza
    M = "m"         # Metro


@dataclass(frozen=True, slots=True)
class BC3Breakdown:
    """
    Descomposición de un item en sus componentes elementales.
    
    Ejemplo: Item "Muro de ladrillo" se descompone en:
    - Ladrillo (Material) - 50 ud/m2
    - Cemento (Material) - 0.05 m3/m2
    - Mano de obra albañil (Mano de obra) - 0.5 h/m2
    - Andamio (Maquinaria) - 0.1 h/m2
    """
    
    resource_type: BC3ResourceType
    resource_code: str
    resource_description: str
    unit: BC3Unit
    quantity_per_unit: Decimal  # Cantidad por unidad del item padre
    price_per_unit: Decimal     # Precio unitario del componente
    waste_percentage: Decimal = Decimal("0")  # Porcentaje de merma/desperdicio
    
    @property
    def cost_per_unit(self) -> Decimal:
        """Coste total del componente por unidad del item padre."""
        qty_with_waste = self.quantity_per_unit * (Decimal("1") + self.waste_percentage / Decimal("100"))
        return (qty_with_waste * self.price_per_unit).quantize(Decimal("0.0001"))
    
    @property
    def total_cost(self) -> Decimal:
        """Alias para cost_per_unit."""
        return self.cost_per_unit


@dataclass(frozen=True, slots=True)
class BC3CostComponent:
    """Componente de coste para análisis de presupuesto."""
    
    resource_type: BC3ResourceType
    description: str
    unit: BC3Unit
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    percentage: Decimal = Decimal("0")  # % sobre total presupuesto
    
    def __post_init__(self):
        if self.total == Decimal("0"):
            object.__setattr__(self, "total", self.quantity * self.unit_price)


@dataclass(frozen=True, slots=True)
class BC3Measurement:
    """Medición de un item en un capítulo/subcapítulo."""
    
    item_code: str
    item_description: str
    chapter_code: str
    subchapter_code: str | None
    unit: BC3Unit
    quantity: Decimal
    unit_price: Decimal
    total_price: Decimal
    chapter: str
    subchapter: str | None
    resource_type: BC3ResourceType
    breakdown: list[BC3Breakdown] = field(default_factory=list)
    
    @property
    def total_cost(self) -> Decimal:
        return self.quantity * self.unit_price
    
    @property
    def breakdown_cost(self) -> Decimal:
        return sum(b.cost_per_unit for b in self.breakdown)


@dataclass(frozen=True, slots=True)
class BC3Item:
    """Item/Recurso individual en un subcapítulo."""
    
    code: str
    description: str
    unit: BC3Unit
    quantity: Decimal  # Cantidad en la medición
    unit_price: Decimal
    resource_type: BC3ResourceType
    chapter_code: str
    subchapter_code: str | None = None
    breakdown: list[BC3Breakdown] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    @property
    def total_price(self) -> Decimal:
        return (self.quantity * self.unit_price).quantize(Decimal("0.01"))
    
    @property
    def breakdown_cost(self) -> Decimal:
        return sum(b.cost_per_unit for b in self.breakdown)
    
    @property
    def has_breakdown(self) -> bool:
        return len(self.breakdown) > 0


@dataclass(frozen=True, slots=True)
class BC3Subchapter:
    """Subcapítulo dentro de un capítulo."""
    
    code: str
    description: str
    chapter_code: str
    items: list[BC3Item] = field(default_factory=list)
    measurements: list[BC3Measurement] = field(default_factory=list)
    
    @property
    def total_items(self) -> int:
        return len(self.items)
    
    @property
    def total_measurements(self) -> int:
        return len(self.measurements)
    
    @property
    def total_amount(self) -> Decimal:
        return sum(m.total_price for m in self.measurements)


@dataclass(frozen=True, slots=True)
class BC3Chapter:
    """Capítulo principal del catálogo BC3."""
    
    code: str
    description: str
    subchapters: list[BC3Subchapter] = field(default_factory=list)
    items: list[BC3Item] = field(default_factory=list)  # Items directos sin subcapítulo
    measurements: list[BC3Measurement] = field(default_factory=list)
    
    @property
    def total_subchapters(self) -> int:
        return len(self.subchapters)
    
    @property
    def total_items(self) -> int:
        return len(self.items) + sum(sc.total_items for sc in self.subchapters)
    
    @property
    def total_measurements(self) -> int:
        return len(self.measurements) + sum(sc.total_measurements for sc in self.subchapters)
    
    @property
    def total_amount(self) -> Decimal:
        return sum(m.total_price for m in self.measurements) + \
               sum(sc.total_amount for sc in self.subchapters)
    
    def get_all_items(self) -> list[BC3Item]:
        """Obtener todos los items (directos + subcapítulos)."""
        items = list(self.items)
        for sc in self.subchapters:
            items.extend(sc.items)
        return items
    
    def get_all_measurements(self) -> list:
        """Obtener todas las mediciones."""
        measurements = list(self.measurements)
        for sc in self.subchapters:
            measurements.extend(sc.measurements)
        return measurements


@dataclass(frozen=True, slots=True)
class BC3Project:
    """Proyecto BC3 con información general."""
    
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    code: str = ""
    description: str = ""
    location: str = ""
    start_date: date | None = None
    end_date: date | None = None
    budget: Decimal | None = None
    currency: str = "EUR"
    chapters: list[BC3Chapter] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: date = field(default_factory=date.today)
    updated_at: date = field(default_factory=date.today)
    
    @property
    def total_chapters(self) -> int:
        return len(self.chapters)
    
    @property
    def total_items(self) -> int:
        return sum(c.total_items for c in self.chapters)
    
    @property
    def total_measurements(self) -> int:
        return sum(c.total_measurements for c in self.chapters)
    
    @property
    def total_amount(self) -> Decimal:
        return sum(c.total_amount for c in self.chapters)
    
    def get_all_measurements(self) -> list:
        measurements = []
        for chapter in self.chapters:
            measurements.extend(chapter.get_all_measurements())
        return measurements


@dataclass(frozen=True, slots=True)
class BC3Catalog:
    """
    Catálogo técnico BC3 completo.
    
    Contiene la estructura completa de capítulos, subcapítulos, items y descomposiciones.
    Puede ser importado desde BC3, generado manualmente, o vinculado a Dolibarr.
    """
    
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    version: str = "1.0"
    source: str = "import"  # import | manual | dolibarr | price_db
    source_file: str | None = None
    projects: list[BC3Project] = field(default_factory=list)
    global_items: dict[str, BC3Item] = field(default_factory=dict)  # code -> item (para reutilización)
    price_database: str | None = None  # Referencia a base de precios usada
    created_at: date = field(default_factory=date.today)
    updated_at: date = field(default_factory=date.today)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def get_project(self, project_id: UUID) -> BC3Project | None:
        for p in self.projects:
            if p.id == project_id:
                return p
        return None
    
    def get_item_by_code(self, code: str) -> BC3Item | None:
        return self.global_items.get(code)
    
    def add_item_to_global(self, item: BC3Item) -> None:
        self.global_items[item.code] = item
    
    def get_all_measurements(self) -> list:
        all_m = []
        for project in self.projects:
            all_m.extend(project.get_all_measurements())
        return all_m
    
    @property
    def total_projects(self) -> int:
        return len(self.projects)
    
    @property
    def total_chapters(self) -> int:
        return sum(p.total_chapters for p in self.projects)
    
    @property
    def total_items(self) -> int:
        return sum(p.total_items for p in self.projects)
    
    @property
    def total_amount(self) -> Decimal:
        return sum(p.total_amount for p in self.projects)


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================

def create_chapter(code: str, description: str) -> BC3Chapter:
    """Crear un capítulo nuevo."""
    return BC3Chapter(code=code, description=description)


def create_subchapter(code: str, description: str, chapter_code: str) -> BC3Subchapter:
    """Crear un subcapítulo nuevo."""
    return BC3Subchapter(code=code, description=description, chapter_code=chapter_code)


def create_item(
    code: str,
    description: str,
    unit: BC3Unit,
    quantity: Decimal,
    unit_price: Decimal,
    resource_type: BC3ResourceType,
    chapter_code: str,
    subchapter_code: str | None = None,
    breakdown: list | None = None,
) -> BC3Item:
    """Crear un item/recurso nuevo."""
    return BC3Item(
        code=code,
        description=description,
        unit=unit,
        quantity=quantity,
        unit_price=unit_price,
        resource_type=resource_type,
        chapter_code=chapter_code,
        subchapter_code=subchapter_code,
        breakdown=breakdown or [],
    )


def create_breakdown(
    resource_type: BC3ResourceType,
    resource_code: str,
    resource_description: str,
    unit: BC3Unit,
    quantity_per_unit: Decimal,
    price_per_unit: Decimal,
    waste_percentage: Decimal = Decimal("0"),
) -> BC3Breakdown:
    """Crear una descomposición de coste."""
    return BC3Breakdown(
        resource_type=resource_type,
        resource_code=resource_code,
        resource_description=resource_description,
        unit=unit,
        quantity_per_unit=quantity_per_unit,
        price_per_unit=price_per_unit,
        waste_percentage=waste_percentage,
    )


def create_measurement(
    item_code: str,
    item_description: str,
    chapter_code: str,
    subchapter_code: str | None,
    unit: BC3Unit,
    quantity: Decimal,
    unit_price: Decimal,
    chapter: str,
    subchapter: str | None,
    resource_type: BC3ResourceType,
    breakdown: list | None = None,
) -> BC3Measurement:
    """Crear una medición."""
    return BC3Measurement(
        item_code=item_code,
        item_description=item_description,
        chapter_code=chapter_code,
        subchapter_code=subchapter_code,
        unit=unit,
        quantity=quantity,
        unit_price=unit_price,
        total_price=(quantity * unit_price).quantize(Decimal("0.01")),
        chapter=chapter_code,
        subchapter=subchapter_code,
        resource_type=resource_type,
        breakdown=breakdown or [],
    )