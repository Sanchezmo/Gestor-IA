"""
Base classes for Hermes Tools.

This module contains the core abstractions (Tool, ToolResult, ToolDefinition, ToolRegistry)
to avoid circular imports between tools modules.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Resultado estandarizado de una Tool."""

    success: bool
    data: Any = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def ok(cls, data: Any = None, metadata: dict[str, Any] | None = None) -> ToolResult:
        return cls(success=True, data=data, metadata=metadata)

    @classmethod
    def error(
        cls,
        error_code: str,
        error_message: str,
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(success=False, error_code=error_code, error_message=error_message, metadata=metadata)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Definición declarativa de una Tool."""

    name: str
    description: str
    parameters_schema: dict[str, Any]  # JSON Schema
    required_permissions: frozenset[str] = frozenset()
    # Si una empresa quiere override, puede registrar su propia versión
    is_core: bool = True


class Tool(ABC):
    """
    Base abstracta para todas las Tools de Hermes.

    Una Tool:
    - NO conoce el canal de entrada (Telegram, API, CLI, LLM)
    - Recibe CompanyContext + UserContext + parámetros tipados
    - Devuelve ToolResult
    - Se registra en ToolRegistry
    """

    def __init__(self, definition: ToolDefinition) -> None:
        self.definition = definition

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def required_permissions(self) -> frozenset[str]:
        return self.definition.required_permissions

    @abstractmethod
    async def execute(
        self,
        company_context: CompanyContext,
        user_context: UserContext,
        **params: Any,
    ) -> ToolResult:
        """
        Ejecutar la herramienta.

        Args:
            company_context: Contexto de empresa (instancia, configs)
            user_context: Usuario autenticado (permisos, identidad Dolibarr)
            **params: Parámetros validados contra parameters_schema

        Returns:
            ToolResult con success/data o error_code/error_message
        """
        pass

    def check_permissions(self, user_context: UserContext) -> bool:
        """Verificar si el usuario tiene los permisos requeridos."""
        return all(user_context.has_permission(p) for p in self.required_permissions)


class ToolRegistry:
    """
    Registro de Tools por instancia.

    - Tools CORE: disponibles para todas las instancias (enabled_tools no aplica)
    - Tools INSTANCIA: registradas específicamente para una empresa
    - Prioridad: instancia > core
    """

    def __init__(self) -> None:
        self._core_tools: dict[str, Tool] = {}
        self._instance_tools: dict[str, dict[str, Tool]] = {}

    # =========================================================================
    # CORE TOOLS (disponibles globalmente)
    # =========================================================================

    def register_core_tool(self, tool: Tool) -> None:
        """Registrar tool core (disponible para todas las instancias)."""
        self._core_tools[tool.name] = tool

    def get_core_tool(self, name: str) -> Tool | None:
        return self._core_tools.get(name)

    def list_core_tools(self) -> list[Tool]:
        return list(self._core_tools.values())

    # =========================================================================
    # INSTANCE TOOLS (por empresa)
    # =========================================================================

    def register_instance_tool(self, instance_id: str, tool: Tool) -> None:
        """Registrar tool para una instancia específica."""
        if instance_id not in self._instance_tools:
            self._instance_tools[instance_id] = {}
        self._instance_tools[instance_id][tool.name] = tool

    def get_instance_tool(self, instance_id: str, name: str) -> Tool | None:
        return self._instance_tools.get(instance_id, {}).get(name)

    def list_instance_tools(self, instance_id: str) -> list[Tool]:
        return list(self._instance_tools.get(instance_id, {}).values())

    # =========================================================================
    # RESOLUCIÓN (instance > core)
    # =========================================================================

    def get_tool(self, instance_id: str, name: str) -> Tool | None:
        """Obtener tool: primero instancia, luego core."""
        # 1. Tool específica de la instancia
        tool = self.get_instance_tool(instance_id, name)
        if tool:
            return tool
        # 2. Tool core
        return self.get_core_tool(name)

    def list_available_tools(self, instance_id: str) -> list[Tool]:
        """Listar todas las tools disponibles para una instancia."""
        tools = self.list_core_tools()
        tools.extend(self.list_instance_tools(instance_id))
        return tools

    # =========================================================================
    # EJECUCIÓN CON PERMISOS
    # =========================================================================

    async def execute_tool(
        self,
        instance_id: str,
        name: str,
        company_context: CompanyContext,
        user_context: UserContext,
        **params: Any,
    ) -> ToolResult:
        """
        Ejecutar tool con verificación de permisos y validación cross-instance.

        Args:
            instance_id: ID de la instancia
            name: Nombre de la tool
            company_context: Contexto de empresa
            user_context: Usuario autenticado
            **params: Parámetros de la tool

        Returns:
            ToolResult (success/error)
        """
        # Cross-instance validation: instance_id must match both contexts
        if company_context.instance_id != instance_id:
            msg = (
                f"Instance ID mismatch: tool called with instance_id='{instance_id}' "
                f"but company_context has '{company_context.instance_id}'"
            )
            return ToolResult.error(
                error_code="CROSS_INSTANCE_ERROR",
                error_message=msg,
                metadata={
                    "provided_instance_id": instance_id,
                    "company_context_instance_id": company_context.instance_id,
                },
            )
        if user_context.instance_id != instance_id:
            msg = (
                f"Instance ID mismatch: tool called with instance_id='{instance_id}' "
                f"but user_context has '{user_context.instance_id}'"
            )
            return ToolResult.error(
                error_code="CROSS_INSTANCE_ERROR",
                error_message=msg,
                metadata={
                    "provided_instance_id": instance_id,
                    "user_context_instance_id": user_context.instance_id,
                },
            )

        tool = self.get_tool(instance_id, name)
        if not tool:
            return ToolResult.error(
                error_code="TOOL_NOT_FOUND",
                error_message=f"Tool '{name}' no disponible para esta instancia",
            )

        # Verificar permisos ANTES de ejecutar (DEFAULT DENY)
        if not tool.check_permissions(user_context):
            return ToolResult.error(
                error_code="PERMISSION_DENIED",
                error_message=f"Permiso requerido: {', '.join(tool.required_permissions)}",
                metadata={
                    "instance_id": instance_id,
                    "tool_name": name,
                    "required_permissions": list(tool.required_permissions),
                },
            )

        # Ejecutar
        try:
            return await tool.execute(company_context, user_context, **params)
        except Exception as e:
            return ToolResult.error(
                error_code="TOOL_EXECUTION_ERROR",
                error_message=f"Error ejecutando tool '{name}': {e}",
                metadata={
                    "instance_id": instance_id,
                    "tool_name": name,
                },
            )

    # =========================================================================
    # UTILIDADES
    # =========================================================================

    def clear_instance(self, instance_id: str) -> None:
        self._instance_tools.pop(instance_id, None)

    def clear_all(self) -> None:
        self._core_tools.clear()
        self._instance_tools.clear()


# Instancia global
tool_registry = ToolRegistry()