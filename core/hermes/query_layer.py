from __future__ import annotations

# ruff: noqa: I001
"""
Query Layer - Capa de interpretación de lenguaje natural a Tools estructuradas.

Esta capa traduce consultas en lenguaje natural (español) a Intents estructurados
que mapean a Tools del ToolRegistry.

ARQUITECTURA:
- NO genera SQL
- NO accede a base de datos directamente
- Solo mapea lenguaje natural -> Tool + parámetros validados
- Respeta CompanyContext, UserContext, AuthorizationService
- AI Policy: LOCAL_ONLY por defecto para consultas empresariales
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


# =========================================================================
# INTENT TYPES
# =========================================================================


class ThirdpartyIntentType(StrEnum):
    """Tipos de intent soportados para terceros."""

    LIST = "list"  # "lista terceros", "lista clientes", "lista proveedores"
    SEARCH = "search"  # "busca cliente ACME", "busca proveedor Pinturas"
    GET = "get"  # "detalle cliente 123" (menos común en lenguaje natural)
    COUNT = "count"  # "cuántos clientes hay", "cuántos proveedores"


class ThirdpartyFilterType(StrEnum):
    """Tipos de filtro para terceros."""

    ALL = "all"
    CUSTOMERS = "customers"  # clientes (client=1)
    SUPPLIERS = "suppliers"  # proveedores (fournisseur=1)


# =========================================================================
# INTENT MODELS
# =========================================================================


@dataclass(frozen=True, slots=True)
class ThirdpartyIntent:
    """Intent estructurado para operaciones de terceros."""

    intent_type: ThirdpartyIntentType
    filter_type: ThirdpartyFilterType = ThirdpartyFilterType.ALL
    query: str | None = None  # Para búsqueda
    thirdparty_id: int | None = None  # Para get por ID
    limit: int = 20
    offset: int = 0
    sort_field: str = "nom"
    sort_order: str = "ASC"

    def to_tool_call(self) -> tuple[str, dict[str, Any]]:
        """Convertir intent a (tool_name, parameters) para ToolRegistry."""
        if self.intent_type == ThirdpartyIntentType.LIST:
            return "list_thirdparties", {
                "limit": self.limit,
                "offset": self.offset,
                "filter_customer": self._customer_filter(),
                "sort_field": self.sort_field,
                "sort_order": self.sort_order,
            }
        elif self.intent_type == ThirdpartyIntentType.SEARCH:
            return "search_thirdparties", {
                "query": self.query or "",
                "filter_customer": self._customer_filter(),
                "filter_supplier": self._supplier_filter(),
                "limit": self.limit,
                "offset": self.offset,
                "sort_field": self.sort_field,
                "sort_order": self.sort_order,
            }
        elif self.intent_type == ThirdpartyIntentType.GET:
            return "get_thirdparty", {
                "thirdparty_id": self.thirdparty_id,
            }
        elif self.intent_type == ThirdpartyIntentType.COUNT:
            return "count_thirdparties", {
                "filter_customer": self._customer_filter(),
                "filter_supplier": self._supplier_filter(),
                "filter_status": None,
            }
        else:
            raise ValueError(f"Tipo de intent no soportado: {self.intent_type}")

    def _customer_filter(self) -> bool | None:
        if self.filter_type == ThirdpartyFilterType.CUSTOMERS:
            return True
        elif self.filter_type == ThirdpartyFilterType.SUPPLIERS:
            return False
        return None

    def _supplier_filter(self) -> bool | None:
        if self.filter_type == ThirdpartyFilterType.SUPPLIERS:
            return True
        elif self.filter_type == ThirdpartyFilterType.CUSTOMERS:
            return False
        return None


# =========================================================================
# NATURAL LANGUAGE PARSER
# =========================================================================


class QueryParser:
    """
    Parser simple y extensible para consultas en lenguaje natural (español).

    Soporta patrones como:
    - "lista terceros" / "lista clientes" / "lista proveedores"
    - "busca cliente ACME" / "busca proveedor Pinturas" / "busca ACME"
    - "cuántos clientes hay" / "cuántos proveedores hay" / "cuántos terceros"
    - "dame el cliente 123" (opcional)

    Diseñado para ser reemplazado/ampliado por LLM (Ollama) en el futuro.
    """

    # Patrones para LIST - regex simple, el filtro se determina por _match_filter_type
    LIST_PATTERNS = [
        (r"^lista\s+(clientes?|proveedores?|terceros?)\s*$", None),
        (r"^muestra\s+(clientes?|proveedores?|terceros?)\s*$", None),
        (r"^ver\s+(clientes?|proveedores?|terceros?)\s*$", None),
    ]

    # Patrones para SEARCH
    SEARCH_PATTERNS = [
        # "busca cliente ACME", "busca proveedor Pinturas"
        (r"^busca\s+(cliente|proveedor)\s+(.+)$", None),
        # "busca ACME" (genérico)
        (r"^busca\s+(.+)$", None),
        # "encuentra cliente ACME"
        (r"^encuentra\s+(cliente|proveedor)\s+(.+)$", None),
        (r"^encuentra\s+(.+)$", None),
    ]

    # Patrones para COUNT
    COUNT_PATTERNS = [
        (r"^cuántos\s+(clientes?|proveedores?|terceros?)\s+(hay|tienes?|tiene?)\s*$", None),
        (r"^cuántos\s+(clientes?|proveedores?|terceros?)\s*$", None),
        (r"^cuenta\s+(clientes?|proveedores?|terceros?)\s*$", None),
        (r"^número\s+de\s+(clientes?|proveedores?|terceros?)\s*$", None),
    ]

    # Patrones para GET
    GET_PATTERNS = [
        (r"^(detalle|ver|muestra)\s+(?:del\s+)?(?:cliente|proveedor|tercero)\s+(\d+)\s*$", None),
        (r"^cliente\s+(\d+)\s*$", None),
        (r"^proveedor\s+(\d+)\s*$", None),
    ]

    def __init__(self) -> None:
        self._compiled_patterns = self._compile_patterns()

    def _compile_patterns(self) -> dict[str, list[tuple[re.Pattern[str], ThirdpartyFilterType | None]]]:
        """Compilar todos los patrones regex."""
        return {
            "list": [(re.compile(p, re.IGNORECASE), f) for p, f in self.LIST_PATTERNS],
            "search": [(re.compile(p, re.IGNORECASE), None) for p, _ in self.SEARCH_PATTERNS],
            "count": [(re.compile(p, re.IGNORECASE), None) for p, _ in self.COUNT_PATTERNS],
            "get": [(re.compile(p, re.IGNORECASE), None) for p, _ in self.GET_PATTERNS],
        }

    def _match_filter_type(self, text: str) -> ThirdpartyFilterType:
        """Determinar tipo de filtro basado en palabras clave."""
        text_lower = text.lower()
        if any(w in text_lower for w in ["cliente", "clientes"]):
            return ThirdpartyFilterType.CUSTOMERS
        if any(w in text_lower for w in ["proveedor", "proveedores"]):
            return ThirdpartyFilterType.SUPPLIERS
        return ThirdpartyFilterType.ALL

    def parse(self, text: str) -> ThirdpartyIntent | None:
        """
        Parsear texto en lenguaje natural a ThirdpartyIntent.

        Returns:
            ThirdpartyIntent si coincide, None si no se reconoce.
        """
        text = text.strip()
        if not text:
            return None

        # 1. Intentar COUNT
        for pattern, _ in self._compiled_patterns["count"]:
            match = pattern.match(text)
            if match:
                filter_type = self._match_filter_type(text)
                return ThirdpartyIntent(
                    intent_type=ThirdpartyIntentType.COUNT,
                    filter_type=filter_type,
                )

        # 2. Intentar LIST
        for pattern, explicit_filter in self._compiled_patterns["list"]:
            match = pattern.match(text)
            if match:
                if explicit_filter:
                    filter_type = explicit_filter
                else:
                    filter_type = self._match_filter_type(text)
                return ThirdpartyIntent(
                    intent_type=ThirdpartyIntentType.LIST,
                    filter_type=filter_type,
                )

        # 3. Intentar SEARCH
        for pattern, _ in self._compiled_patterns["search"]:
            match = pattern.match(text)
            if match:
                groups = match.groups()
                if len(groups) == 2:
                    # "busca cliente ACME" o "busca proveedor Pinturas"
                    filter_word, query = groups
                    filter_type = (
                        ThirdpartyFilterType.CUSTOMERS
                        if "cliente" in filter_word.lower()
                        else ThirdpartyFilterType.SUPPLIERS
                    )
                else:
                    # "busca ACME"
                    query = groups[0] if groups else text.replace("busca", "").strip()
                    filter_type = ThirdpartyFilterType.ALL

                return ThirdpartyIntent(
                    intent_type=ThirdpartyIntentType.SEARCH,
                    filter_type=filter_type,
                    query=query.strip(),
                )

        # 4. Intentar GET
        for pattern, _ in self._compiled_patterns["get"]:
            match = pattern.match(text)
            if match:
                groups = match.groups()
                thirdparty_id = int(groups[-1]) if groups else None
                if thirdparty_id:
                    return ThirdpartyIntent(
                        intent_type=ThirdpartyIntentType.GET,
                        thirdparty_id=thirdparty_id,
                    )

        # No reconocido
        return None


# Instancia global del parser
query_parser = QueryParser()


# =========================================================================
# HELPER FUNCTIONS
# =========================================================================


def parse_natural_query(text: str) -> ThirdpartyIntent | None:
    """
    Función de conveniencia para parsear una consulta natural.

    Args:
        text: Texto en lenguaje natural (español)

    Returns:
        ThirdpartyIntent o None si no se reconoce
    """
    return query_parser.parse(text)


def format_thirdparties_for_telegram(parties: list[dict[str, Any]], limit: int, offset: int) -> str:
    """Formatear lista de terceros para respuesta Telegram."""
    if not parties:
        return "No se han encontrado terceros."

    lines = ["Terceros encontrados:"]
    for i, p in enumerate(parties, 1):
        tipo = []
        if p.get("is_customer"):
            tipo.append("Cliente")
        if p.get("is_supplier"):
            tipo.append("Proveedor")
        tipo_str = f" ({', '.join(tipo)})" if tipo else ""
        email_str = f" - {p['email']}" if p.get("email") else ""
        phone_str = f" - {p['phone']}" if p.get("phone") else ""
        lines.append(f"{i}. {p['name']}{tipo_str}{email_str}{phone_str}")

    if len(parties) >= limit:
        lines.append(f"\nMostrando los primeros {limit} resultados (offset {offset}).")

    return "\n".join(lines)


def format_thirdparty_detail_for_telegram(detail: dict[str, Any]) -> str:
    """Formatear detalle de tercero para respuesta Telegram."""
    lines = [f"📋 *{detail['name']}*"]
    if detail.get("ref"):
        lines.append(f"Ref: {detail['ref']}")
    if detail.get("vat_number"):
        lines.append(f"NIF/CIF: {detail['vat_number']}")
    if detail.get("email"):
        lines.append(f"Email: {detail['email']}")
    if detail.get("phone"):
        lines.append(f"Teléfono: {detail['phone']}")
    if detail.get("address"):
        addr_parts = [detail["address"]]
        if detail.get("zip"):
            addr_parts.append(detail["zip"])
        if detail.get("town"):
            addr_parts.append(detail["town"])
        lines.append(f"Dirección: {', '.join(addr_parts)}")

    tipo = []
    if detail.get("is_customer"):
        tipo.append("Cliente")
    if detail.get("is_supplier"):
        tipo.append("Proveedor")
    if tipo:
        lines.append(f"Tipo: {', '.join(tipo)}")

    status_map = {0: "Borrador", 1: "Validado", 2: "Enviado"}
    status = status_map.get(detail.get("status", 0), f"Status {detail.get('status', 0)}")
    lines.append(f"Estado: {status}")

    return "\n".join(lines)


def format_count_for_telegram(count: int, filter_type: ThirdpartyFilterType) -> str:
    """Formatear respuesta de conteo para Telegram."""
    if filter_type == ThirdpartyFilterType.CUSTOMERS:
        return f"Hay {count} clientes registrados."
    elif filter_type == ThirdpartyFilterType.SUPPLIERS:
        return f"Hay {count} proveedores registrados."
    else:
        return f"Hay {count} terceros registrados (clientes + proveedores)."
