from __future__ import annotations

"""
Query Layer V2 - Paquete para interpretación de lenguaje natural a Tools.

Estructura:
- models: Pydantic models para StructuredIntent, argumentos, catálogo
- interpreter: IntentInterpreter interface + implementaciones
- factory: Funciones para crear intérpretes configurados
"""

from core.hermes.query.factory import create_interpreter_for_company_context
from core.hermes.query.interpreter import (
    CompositeIntentInterpreter,
    DeterministicIntentInterpreter,
    IntentInterpreter,
    OllamaIntentInterpreter,
)
from core.hermes.query.models import (
    THIRDPARTY_TOOLS_CATALOG,
    CountThirdpartiesArgs,
    GetThirdpartyArgs,
    IntentInterpretation,
    InterpretationStatus,
    # Argument models
    ListThirdpartiesArgs,
    SearchThirdpartiesArgs,
    SortField,
    SortOrder,
    # Core models
    StructuredIntent,
    # Enums
    ThirdpartyAction,
    ThirdpartyPartyType,
    ToolSchema,
    format_count_for_telegram,
    format_thirdparties_for_telegram,
    format_thirdparty_detail_for_telegram,
    get_tools_catalog_for_prompt,
    # Helpers
    structured_intent_to_tool_call,
)

__all__ = [
    # Enums
    "ThirdpartyAction",
    "ThirdpartyPartyType",
    "SortField",
    "SortOrder",
    "InterpretationStatus",
    # Argument models
    "ListThirdpartiesArgs",
    "SearchThirdpartiesArgs",
    "GetThirdpartyArgs",
    "CountThirdpartiesArgs",
    # Core models
    "StructuredIntent",
    "IntentInterpretation",
    "ToolSchema",
    # Helpers
    "structured_intent_to_tool_call",
    "get_tools_catalog_for_prompt",
    "THIRDPARTY_TOOLS_CATALOG",
    "format_count_for_telegram",
    "format_thirdparties_for_telegram",
    "format_thirdparty_detail_for_telegram",
    # Interpreters
    "IntentInterpreter",
    "DeterministicIntentInterpreter",
    "OllamaIntentInterpreter",
    "CompositeIntentInterpreter",
    # Factory
    "create_interpreter_for_company_context",
]
