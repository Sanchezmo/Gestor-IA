"""
BC3 Cost Breakdown - Análisis de descomposición de costes.

Proporciona:
- Descomposición de items en componentes elementales
- Análisis de coste por tipo de recurso
- Cálculo de precios unitarios compuestos
- Análisis de rentabilidad
- Comparativa de precios
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any
from uuid import UUID, uuid4

from .models import BC3Breakdown, BC3Item, BC3ResourceType, BC3Unit


# =============================================================================
# COST COMPONENT
# =============================================================================

@dataclass(frozen=True, slots=True)
class BC3CostComponent:
    """Componente de coste individual."""
    
    resource_type: BC3ResourceType
    code: str
    description: str
    unit: BC3Unit
    quantity: Decimal
    unit_price: Decimal
    total: Decimal
    percentage: Decimal = Decimal("0")
    
    @property
    def cost_per_unit(self) -> Decimal:
        return self.total / self.quantity if self.quantity > 0 else Decimal("0")


@dataclass(frozen=True, slots=True)
class BC3CostBreakdown:
    """
    Descomposición completa de costes para un item/recurso.
    
    Permite analizar la composición del precio unitario de un item
    en sus componentes elementales (materiales, mano de obra, maquinaria, etc.)
    """
    
    item_code: str
    item_description: str
    unit: BC3Unit
    base_price: Decimal  # Precio unitario del item (sin descomposición)
    components: list[BC3CostComponent] = field(default_factory=list)
    overhead_percentage: Decimal = Decimal("0")  # % gastos generales
    profit_percentage: Decimal = Decimal("0")    # % beneficio industrial
    contingency_percentage: Decimal = Decimal("0")  # % contingencias
    
    @property
    def direct_cost(self) -> Decimal:
        """Coste directo = suma de componentes."""
        return sum(c.total for c in self.components)
    
    @property
    def overhead_cost(self) -> Decimal:
        """Coste de gastos generales."""
        return (self.direct_cost * self.overhead_percentage / Decimal("100")).quantize(Decimal("0.01"))
    
    @property
    def profit_cost(self) -> Decimal:
        """Coste de beneficio industrial."""
        base = self.direct_cost + self.overhead_cost
        return (base * self.profit_percentage / Decimal("100")).quantize(Decimal("0.01"))
    
    @property
    def contingency_cost(self) -> Decimal:
        """Coste de contingencias."""
        base = self.direct_cost + self.overhead_cost + self.profit_cost
        return (base * self.contingency_percentage / Decimal("100")).quantize(Decimal("0.01"))
    
    @property
    def total_price(self) -> Decimal:
        """Precio total = coste directo + GG + BI + Contingencias."""
        total = (self.direct_cost + self.overhead_cost + 
                self.profit_cost + self.contingency_cost)
        return total.quantize(Decimal("0.01"))
    
    @property
    def unit_price(self) -> Decimal:
        """Precio unitario final."""
        return self.total_price
    
    @property
    def resource_breakdown(self) -> dict[BC3ResourceType, Decimal]:
        """Desglose de coste por tipo de recurso."""
        breakdown = {}
        for component in self.components:
            rt = component.resource_type
            if rt not in breakdown:
                breakdown[rt] = Decimal("0")
            breakdown[rt] += component.total
        return breakdown
    
    @property
    def resource_percentage(self) -> dict[BC3ResourceType, Decimal]:
        """Porcentaje de coste por tipo de recurso."""
        total = self.direct_cost
        if total == 0:
            return {}
        percentages = {}
        for rt, cost in self.resource_breakdown.items():
            percentages[rt] = (cost / total * Decimal("100")).quantize(Decimal("0.01"))
        return percentages


# =============================================================================
# BREAKDOWN CALCULATOR
# =============================================================================

class BC3CostBreakdownCalculator:
    """
    Calculadora de descomposición de costes.
    
    Permite:
    - Calcular descomposición a partir de items BC3
    - Analizar desviación de precios
    - Optimizar composición de costes
    - Generar informes de análisis de costes
    """
    
    def __init__(self):
        self.overhead_default = Decimal("15")      # 15% gastos generales
        self.profit_default = Decimal("6")         # 6% beneficio industrial
        self.contingency_default = Decimal("3")    # 3% contingencias
    
    def calculate_breakdown(
        self,
        item: Any,  # BC3Item
        breakdown_data: list[dict[str, Any]] | None = None,
        overhead: Decimal | None = None,
        profit: Decimal | None = None,
        contingency: Decimal | None = None,
    ) -> Any:  # BC3CostBreakdown
        """
        Calcular descomposición de costes para un item.
        
        Args:
            item: Item BC3 a analizar
            breakdown_data: Datos de descomposición (opcional, si el item no los tiene)
            overhead: % gastos generales (default 15%)
            profit: % beneficio industrial (default 6%)
            contingency: % contingencias (default 3%)
            
        Returns:
            BC3CostBreakdown con análisis completo
        """
        # Usar descomposición del item o datos proporcionados
        if breakdown_data is None:
            breakdown_data = getattr(item, "breakdown", [])
        
        # Convertir a componentes de coste
        components = []
        for bd in breakdown_data:
            if hasattr(bd, "resource_type"):  # Es BC3Breakdown
                resource_type = bd.resource_type
                code = bd.resource_code
                description = bd.resource_description
                unit = bd.unit
                quantity = bd.quantity_per_unit
                price = bd.price_per_unit
                waste = bd.waste_percentage
            else:  # dict
                resource_type = BC3ResourceType(bd.get("tipo", "1"))
                code = bd.get("codigo", "")
                description = bd.get("descripcion", "")
                unit = BC3Unit(bd.get("unidad", "ud"))
                quantity = Decimal(str(bd.get("cantidad", 1)))
                price = Decimal(str(bd.get("precio", 0)))
                waste = Decimal(str(bd.get("merma", 0)))
            
            # Calcular cantidad con merma
            qty_with_waste = quantity * (Decimal("1") + waste / Decimal("100"))
            total = (qty_with_waste * price).quantize(Decimal("0.01"))
            
            component = BC3CostComponent(
                resource_type=resource_type,
                code=code,
                description=description,
                unit=unit,
                quantity=quantity,
                unit_price=price,
                total=total,
            )
            components.append(component)
        
        # Calcular costes indirectos
        overhead_pct = overhead or Decimal("15")
        profit_pct = profit or Decimal("6")
        contingency_pct = contingency or Decimal("3")
        
        return BC3CostBreakdown(
            item_code=item.code,
            item_description=item.description,
            unit=item.unit,
            base_price=item.unit_price,
            components=components,
            overhead_percentage=overhead_pct,
            profit_percentage=profit_pct,
            contingency_percentage=contingency_pct,
        )
    
    def analyze_price_deviation(
        self,
        item: Any,
        market_price: Decimal,
        tolerance: Decimal = Decimal("5"),  # 5% tolerancia
    ) -> dict[str, Any]:
        """
        Analizar desviación de precio respecto al mercado.
        
        Returns:
            Dict con análisis de desviación
        """
        breakdown = self.calculate_breakdown(item)
        
        unit_price = breakdown.unit_price
        deviation = ((market_price - unit_price) / unit_price * Decimal("100")).quantize(Decimal("0.01"))
        is_within_tolerance = abs(deviation) <= tolerance
        
        return {
            "item_code": item.code,
            "item_description": item.description,
            "calculated_price": unit_price,
            "market_price": market_price,
            "deviation_percentage": deviation,
            "within_tolerance": is_within_tolerance,
            "recommendation": self._get_recommendation(deviation, tolerance),
            "cost_breakdown": {
                "direct_cost": breakdown.direct_cost,
                "overhead": breakdown.overhead_cost,
                "profit": breakdown.profit_cost,
                "contingency": breakdown.contingency_cost,
                "total": breakdown.total_price,
            },
        }
    
    def _get_recommendation(self, deviation: Decimal, tolerance: Decimal) -> str:
        """Generar recomendación basada en desviación."""
        if deviation > tolerance:
            return f"PRECIO ALTO: Revisar costes directos o reducir márgenes (desviación +{deviation}%)"
        elif deviation < -tolerance:
            return f"PRECIO BAJO: Riesgo de pérdidas, revisar márgenes (desviación {deviation}%)"
        else:
            return f"PRECIO CORRECTO: Dentro de tolerancia ±{tolerance}% (desviación {deviation}%)"
    
    def compare_items(self, items: list) -> dict[str, Any]:
        """
        Comparar múltiples items del mismo tipo.
        
        Returns:
            Análisis comparativo
        """
        if not items:
            return {"error": "Lista vacía"}
        
        breakdowns = [self.calculate_breakdown(item) for item in items]
        
        # Agrupar por tipo de recurso
        resource_comparison = {}
        for bd in breakdowns:
            for rt, cost in bd.resource_breakdown.items():
                if rt not in resource_comparison:
                    resource_comparison[rt] = {"total": Decimal("0"), "count": 0, "items": []}
                resource_comparison[rt]["total"] += cost
                resource_comparison[rt]["count"] += 1
                resource_comparison[rt]["items"].append({
                    "code": items[breakdowns.index(bd)].code,
                    "cost": cost,
                })
        
        # Estadísticas
        prices = [bd.unit_price for bd in breakdowns]
        avg_price = sum(prices) / len(prices) if prices else Decimal("0")
        
        return {
            "total_items": len(items),
            "price_range": {
                "min": min(prices),
                "max": max(prices),
                "avg": avg_price,
            },
            "by_resource_type": {
                rt: {
                    "total_cost": data["total"],
                    "count": data["count"],
                    "avg_cost": data["total"] / data["count"] if data["count"] > 0 else Decimal("0"),
                }
                for rt, data in resource_comparison.items()
            },
            "cost_structure": {
                "direct_cost_pct": Decimal("100"),
                "overhead_pct": breakdowns[0].overhead_percentage if breakdowns else Decimal("0"),
                "profit_pct": breakdowns[0].profit_percentage if breakdowns else Decimal("0"),
                "contingency_pct": breakdowns[0].contingency_percentage if breakdowns else Decimal("0"),
            },
        }
    
    def optimize_breakdown(
        self,
        breakdown: Any,  # BC3CostBreakdown
        target_price: Decimal,
        fixed_resource_types: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Sugerir ajustes en descomposición para alcanzar precio objetivo.
        
        Returns:
            Lista de sugerencias de ajuste
        """
        suggestions = []
        current_price = breakdown.unit_price
        difference = target_price - current_price
        
        if abs(difference) < Decimal("0.01"):
            return [{"message": "Precio ya coincide con objetivo"}]
        
        # Analizar qué componentes ajustar
        for component in breakdown.components:
            if fixed_resource_types and component.resource_type in fixed_resource_types:
                continue
            
            # Calcular impacto de ajustar este componente
            impact_per_pct = component.total / Decimal("100")
            pct_change_needed = (difference / impact_per_pct).quantize(Decimal("0.01"))
            
            if abs(pct_change_needed) > Decimal("50"):  # Más de 50% cambio
                suggestions.append({
                    "component": component.description,
                    "resource_type": component.resource_type.label,
                    "current_cost": component.total,
                    "suggested_change_pct": pct_change_needed,
                    "feasible": abs(pct_change_needed) <= Decimal("30"),
                    "action": "reducir" if pct_change_needed < 0 else "aumentar",
                })
        
        # Ordenar por factibilidad
        suggestions.sort(key=lambda x: (not x["feasible"], abs(x["suggested_change_pct"])))
        
        return suggestions


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================

def create_cost_breakdown(
    item: Any,
    breakdown_data: list[dict[str, Any]] | None = None,
    overhead: Decimal = Decimal("15"),
    profit: Decimal = Decimal("6"),
    contingency: Decimal = Decimal("3"),
) -> Any:  # BC3CostBreakdown
    """Función de conveniencia para crear descomposición de coste."""
    calculator = BC3CostBreakdownCalculator()
    return calculator.calculate_breakdown(item, breakdown_data, overhead, profit, contingency)


def analyze_item_price(
    item: Any,
    market_price: Decimal,
    tolerance: Decimal = Decimal("5"),
) -> dict[str, Any]:
    """Analizar desviación de precio de un item."""
    calculator = BC3CostBreakdownCalculator()
    return calculator.analyze_price_deviation(item, market_price)


def compare_item_prices(items: list) -> dict[str, Any]:
    """Comparar precios de múltiples items."""
    calculator = BC3CostBreakdownCalculator()
    return calculator.compare_items(items)


def suggest_price_adjustments(
    item: Any,
    target_price: Decimal,
    fixed_resources: list | None = None,
) -> list[dict[str, Any]]:
    """Sugerir ajustes para alcanzar precio objetivo."""
    calculator = BC3CostBreakdownCalculator()
    breakdown = calculator.calculate_breakdown(item)
    return calculator.optimize_breakdown(breakdown, target_price, fixed_resource_types=fixed_resources)