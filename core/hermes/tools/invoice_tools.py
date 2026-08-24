"""
Compatibility shim for invoice_tools.py

This file exists for backward compatibility only.
New code should import from core.hermes.tools.invoices package.
"""

from __future__ import annotations

from core.hermes.tools.invoices import (
    CountCustomerInvoicesTool,
    CountSupplierInvoicesTool,
    GetCustomerInvoiceTool,
    GetSupplierInvoiceTool,
    ListCustomerInvoicesTool,
    ListSupplierInvoicesTool,
    SearchCustomerInvoicesTool,
    SearchSupplierInvoicesTool,
    register_core_customer_invoice_tools,
    register_core_invoice_tools,
    register_core_supplier_invoice_tools,
)

# Re-export for backward compatibility
__all__ = [
    "ListCustomerInvoicesTool",
    "SearchCustomerInvoicesTool",
    "GetCustomerInvoiceTool",
    "CountCustomerInvoicesTool",
    "ListSupplierInvoicesTool",
    "SearchSupplierInvoicesTool",
    "GetSupplierInvoiceTool",
    "CountSupplierInvoicesTool",
    "register_core_invoice_tools",
    "register_core_customer_invoice_tools",
    "register_core_supplier_invoice_tools",
]
