"""
BC3 Advanced - Módulo principal para gestión avanzada de archivos BC3.

⚠️ EXPERIMENTAL / NOT PRODUCTION READY ⚠️

Este módulo está en desarrollo activo y NO debe usarse en producción.
Funcionalidad planeada:
- Parser robusto con validación XSD
- Catálogo técnico 4-niveles (Capítulos → Subcapítulos → Items → Descomposición)
- Tipos de recursos (Materiales, Mano de obra, Maquinaria, Auxiliares, Subcontratación)
- Base de precios
- Descomposición de costes
- Mediciones
- Presupuestos
- Vinculación Dolibarr

Estado actual:
- Parser: funcional básico
- Catálogo: estructura definida
- SQLite price_db: almacenamiento temporal/catálogo técnico (NO source of truth)
- Cost breakdown: cálculos básicos
- Measurements: estructura definida
- Budget generator: estructura definida
- Dolibarr link: sincronización básica

ADVERTENCIA: Dolibarr debe seguir siendo el source of truth empresarial.
Este módulo NO intenta convertirse en ERP paralelo.
"""

from __future__ import annotations

from .models import (
    BC3Project,
    BC3Chapter,
    BC3Subchapter,
    BC3Item,
    BC3Breakdown,
    BC3Measurement,
    BC3ResourceType,
    BC3Unit,
    BC3Catalog,
    BC3CostComponent,
    BC3CostBreakdown,
    BC3CostComponent,
    BC3CostBreakdown,
    BC3Measurement,
    BC3PriceEntry,
    BC3PriceDatabase,
    BC3CostComponent,
    BC3CostBreakdown,
    BC3Measurement,
    BC3PriceEntry,
    BC3PriceDatabase,
    BC3ResourceType,
    BC3Unit,
    BC3Catalog,
    create_chapter,
    create_subchapter,
    create_item,
    create_breakdown,
    create_measurement,
    calculate_line_totals,
    calculate_proposal_totals,
    calculate_invoice_totals,
    calculate_payment_fifo,
    allocate_payment_fifo,
    calculate_stock_valuation,
)

from .parser import (
    BC3Parser,
    BC3ParseError,
    BC3ValidationError,
    parse_bc3_file,
    parse_bc3_bytes,
    validate_bc3_file,
)

from .price_db import (
    BC3PriceDatabase,
    BC3PriceEntry,
    create_price_database,
    load_official_bc3_prices,
    load_bedec_prices,
    load_preoc_prices,
)

from .cost_breakdown import (
    BC3CostComponent,
    BC3CostBreakdown,
    BC3CostBreakdownCalculator,
    create_cost_breakdown,
    analyze_item_price,
    compare_item_prices,
    suggest_price_adjustments,
)

from .measurements import (
    BC3MeasurementCalculator,
    create_measurement_calculator,
    generate_measurement_state,
    export_measurements_to_csv,
)

from .budget import (
    BC3BudgetGenerator,
    BC3BudgetLine,
    BC3BudgetSummary,
    BC3Budget,
    BC3ProfitabilityAnalysis,
    BC3ProfitabilityAnalyzer,
    create_budget_generator,
    analyze_budget_profitability,
    generate_budget_document,
)

from .dolibarr_link import (
    BC3DolibarrLink,
    BC3DolibarrLinker,
    create_dolibarr_linker,
)

from .models import (
    BC3Project,
    BC3Chapter,
    BC3Subchapter,
    BC3Item,
    BC3Breakdown,
    BC3Measurement,
    BC3ResourceType,
    BC3Unit,
    BC3Catalog,
    BC3CostComponent,
    BC3CostBreakdown,
    BC3ResourceType,
    BC3Unit,
    BC3Catalog,
    create_chapter,
    create_subchapter,
    create_item,
    create_breakdown,
    create_measurement,
    calculate_line_totals,
    calculate_proposal_totals,
    calculate_invoice_totals,
    calculate_payment_fifo,
    allocate_payment_fifo,
    calculate_stock_valuation,
)

__all__ = [
    # Models
    "BC3Project",
    "BC3Chapter",
    "BC3Subchapter",
    "BC3Item",
    "BC3Breakdown",
    "BC3Measurement",
    "BC3Budget",
    "BC3ResourceType",
    "BC3Unit",
    "BC3Catalog",
    "BC3CostComponent",
    "BC3CostBreakdown",
    "BC3BudgetLine",
    "BC3BudgetSummary",
    "BC3Budget",
    "BC3ProfitabilityAnalysis",
    "BC3ProfitabilityAnalyzer",
    # Parser
    "BC3Parser",
    "BC3ParseError",
    "BC3ValidationError",
    "parse_bc3_file",
    "parse_bc3_bytes",
    "validate_bc3_file",
    # Price DB (EXPERIMENTAL - SQLite temporal para catálogo técnico)
    "BC3PriceDatabase",
    "BC3PriceEntry",
    "create_price_database",
    "load_official_bc3_prices",
    "load_bedec_prices",
    "load_preoc_prices",
    # Cost Breakdown
    "BC3CostComponent",
    "BC3CostBreakdown",
    "BC3CostBreakdownCalculator",
    "create_cost_breakdown",
    "analyze_item_price",
    "compare_item_prices",
    "suggest_price_adjustments",
    # Measurements
    "BC3MeasurementCalculator",
    "create_measurement_calculator",
    "generate_measurement_state",
    "export_measurements_to_csv",
    # Budget
    "BC3BudgetGenerator",
    "BC3BudgetLine",
    "BC3BudgetSummary",
    "BC3Budget",
    "BC3ProfitabilityAnalysis",
    "BC3ProfitabilityAnalyzer",
    "create_budget_generator",
    "analyze_budget_profitability",
    "generate_budget_document",
    # Dolibarr Link
    "BC3DolibarrLink",
    "BC3DolibarrLinker",
    "create_dolibarr_linker",
    # Factory functions
    "create_chapter",
    "create_subchapter",
    "create_item",
    "create_breakdown",
    "create_measurement",
    "calculate_line_totals",
    "calculate_proposal_totals",
    "calculate_invoice_totals",
    "calculate_payment_fifo",
    "allocate_payment_fifo",
    "calculate_stock_valuation",
]

__version__ = "1.0.0-experimental"