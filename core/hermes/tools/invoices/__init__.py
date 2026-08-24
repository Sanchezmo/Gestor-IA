"""
Tools de facturas (cliente y proveedor) - READ ONLY.

Paquete modular separado por dominio:
- customer: facturas de cliente
- supplier: facturas de proveedor
- common: utilidades compartidas
- models: modelos de datos
"""

# Reexportar tools públicas para compatibilidad
# Función de registro
from .customer import (
    CountCustomerInvoicesTool,
    GetCustomerInvoiceTool,
    ListCustomerInvoicesTool,
    SearchCustomerInvoicesTool,
    register_core_customer_invoice_tools,
)
from .supplier import (
    CountSupplierInvoicesTool,
    GetSupplierInvoiceTool,
    ListSupplierInvoicesTool,
    SearchSupplierInvoicesTool,
    register_core_supplier_invoice_tools,
)


def register_core_invoice_tools() -> None:
    """Registrar todas las tools de facturas en el registry global."""
    register_core_customer_invoice_tools()
    register_core_supplier_invoice_tools()


__all__ = [
    # Customer tools
    "ListCustomerInvoicesTool",
    "SearchCustomerInvoicesTool",
    "GetCustomerInvoiceTool",
    "CountCustomerInvoicesTool",
    # Supplier tools
    "ListSupplierInvoicesTool",
    "SearchSupplierInvoicesTool",
    "GetSupplierInvoiceTool",
    "CountSupplierInvoicesTool",
    # Registration
    "register_core_invoice_tools",
    "register_core_customer_invoice_tools",
    "register_core_supplier_invoice_tools",
]
