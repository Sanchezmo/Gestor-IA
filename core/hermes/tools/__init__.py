"""
Hermes Tools - Herramientas reutilizables independientes del canal.

Principio: Las Tools NO saben de Telegram, WebSocket, HTTP, etc.
Reciben CompanyContext, UserContext y parámetros.
Devuelven ToolResult.

Esto permite que "/terceros" (Telegram) y "muéstrame clientes" (LLM)
usen EXACTAMENTE la misma Tool.
"""

from __future__ import annotations

from core.hermes.context import CompanyContext
from core.hermes.identity import UserContext

from .base import Tool, ToolDefinition, ToolResult, ToolRegistry, tool_registry

# Importar tools y funciones de registro al final para evitar importaciones circulares
from .thirdparty_tools import (
    ListThirdpartiesTool,
    SearchThirdpartiesTool,
    GetThirdpartyTool,
    CountThirdpartiesTool,
    register_core_thirdparty_tools,
)
from .product_tools import (
    ListProductsTool,
    SearchProductsTool,
    GetProductTool,
    CountProductsTool,
    register_core_product_tools,
)
from .invoices import (
    ListCustomerInvoicesTool,
    SearchCustomerInvoicesTool,
    GetCustomerInvoiceTool,
    CountCustomerInvoicesTool,
    ListSupplierInvoicesTool,
    SearchSupplierInvoicesTool,
    GetSupplierInvoiceTool,
    CountSupplierInvoicesTool,
    register_core_invoice_tools,
)


# Reexportar functions de registro
from .thirdparty_tools import register_core_thirdparty_tools
from .product_tools import register_core_product_tools
from .invoices import register_core_invoice_tools
