# SDD Design: Invoice Read-Only Query Layer (Query Layer V3)

## Change ID
`invoice-query-layer-v3`

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           QUERY LAYER V3 ARCHITECTURE                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Telegram                                                                  │
│     ↓                                                                      │
│  InstanceResolver (domain/webhook path)                                    │
│     ↓                                                                      │
│  CompanyContext (instance_id, dolibarr_config, ai_config, permissions)     │
│     ↓                                                                      │
│  IdentityResolver → UserContext (dolibarr_user_id, effective_permissions)  │
│     ↓                                                                      │
│  CompositeIntentInterpreter                                                │
│     ├── DeterministicIntentInterpreter (regex patterns)                   │
│     └── OllamaIntentInterpreter (structured output)                       │
│     ↓                                                                      │
│  StructuredIntent (InvoiceAction + typed arguments)                        │
│     ↓                                                                      │
│  Pydantic Validation (extra="forbid")                                      │
│     ↓                                                                      │
│  AuthorizationService.require(permission)                                  │
│     ├── customer_invoice.read  → Customer Invoice Tools                    │
│     └── supplier_invoice.read  → Supplier Invoice Tools                    │
│     ↓                                                                      │
│  ToolRegistry.execute_tool()                                               │
│     ↓                                                                      │
│  Invoice Tools (8 tools)                                                   │
│     ├── ListCustomerInvoicesTool    → customer_invoice.read               │
│     ├── SearchCustomerInvoicesTool  → customer_invoice.read               │
│     ├── GetCustomerInvoiceTool      → customer_invoice.read               │
│     ├── CountCustomerInvoicesTool   → customer_invoice.read               │
│     ├── ListSupplierInvoicesTool    → supplier_invoice.read               │
│     ├── SearchSupplierInvoicesTool  → supplier_invoice.read               │
│     ├── GetSupplierInvoiceTool      → supplier_invoice.read               │
│     └── CountSupplierInvoicesTool   → supplier_invoice.read               │
│     ↓                                                                      │
│  CompanyContext.create_dolibarr_client()                                   │
│     ↓                                                                      │
│  DolibarrClient (instance-specific)                                        │
│     ├── list_invoices()          → GET /invoices                          │
│     ├── get_invoice()            → GET /invoices/{id}                     │
│     ├── list_supplier_invoices() → GET /supplierinvoices                  │
│     └── get_supplier_invoice()   → GET /supplierinvoices/{id}             │
│     ↓                                                                      │
│  Dolibarr REST API                                                         │
│     ↓                                                                      │
│  Mappers (dolibarr_to_customer_invoice, dolibarr_to_supplier_invoice)     │
│     ↓                                                                      │
│  Typed Summary Models (CustomerInvoiceSummary, SupplierInvoiceSummary)    │
│     ↓                                                                      │
│  ToolResult.ok(data, metadata)                                             │
│     ↓                                                                      │
│  Formatters (deterministic)                                                │
│     ├── format_customer_invoices_for_telegram()                            │
│     ├── format_supplier_invoices_for_telegram()                            │
│     ├── format_customer_invoice_detail_for_telegram()                      │
│     └── format_supplier_invoice_detail_for_telegram()                      │
│     ↓                                                                      │
│  Telegram Response                                                         │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Design

### 1. Query Models (`core/hermes/query/models.py`)

#### New Enums
```python
class InvoiceAction(StrEnum):
    # Customer
    LIST_CUSTOMER_INVOICES = "list_customer_invoices"
    SEARCH_CUSTOMER_INVOICES = "search_customer_invoices"
    GET_CUSTOMER_INVOICE = "get_customer_invoice"
    COUNT_CUSTOMER_INVOICES = "count_customer_invoices"
    SUM_CUSTOMER_INVOICES = "sum_customer_invoices"
    
    # Supplier
    LIST_SUPPLIER_INVOICES = "list_supplier_invoices"
    SEARCH_SUPPLIER_INVOICES = "search_supplier_invoices"
    GET_SUPPLIER_INVOICE = "get_supplier_invoice"
    COUNT_SUPPLIER_INVOICES = "count_supplier_invoices"
    SUM_SUPPLIER_INVOICES = "sum_supplier_invoices"

class InvoicePartyType(StrEnum):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"

class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    VALIDATED = "validated"
    PAID = "paid"
    CANCELLED = "cancelled"

class InvoiceSortField(StrEnum):
    ROWID = "rowid"
    REF = "ref"
    DATE = "date"
    DATE_LIM_REGLEMENT = "date_lim_reglement"
    TOTAL_TTC = "total_ttc"
    THIRDPARTY_NAME = "soc_name"
    STATUS = "status"
```

#### New Argument Models (all with `extra="forbid"`)
- `ListCustomerInvoicesArgs`
- `SearchCustomerInvoicesArgs`
- `GetCustomerInvoiceArgs`
- `CountCustomerInvoicesArgs`
- `ListSupplierInvoicesArgs`
- `SearchSupplierInvoicesArgs`
- `GetSupplierInvoiceArgs`
- `CountSupplierInvoicesArgs`

#### Updated StructuredIntent
```python
class StructuredIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    
    action: ThirdpartyAction | InvoiceAction  # Union
    arguments: ThirdpartyArgs | InvoiceArgs   # Union
    confidence: float | None = None
    raw_text: str | None = None
```

#### Formatters
```python
def format_customer_invoices_for_telegram(invoices: list[dict], limit: int, offset: int) -> str
def format_supplier_invoices_for_telegram(invoices: list[dict], limit: int, offset: int) -> str
def format_customer_invoice_detail_for_telegram(invoice: dict) -> str
def format_supplier_invoice_detail_for_telegram(invoice: dict) -> str
def format_invoice_count_for_telegram(count: int, party_type: InvoicePartyType) -> str
def format_invoice_sum_for_telegram(total: Decimal, party_type: InvoicePartyType, period: str | None) -> str
```

#### Tool Catalog for Prompt
```python
INVOICE_TOOLS_CATALOG: list[ToolSchema] = [
    ToolSchema(name="list_customer_invoices", description="...", arguments_schema=...),
    ToolSchema(name="search_customer_invoices", description="...", arguments_schema=...),
    ToolSchema(name="get_customer_invoice", description="...", arguments_schema=...),
    ToolSchema(name="count_customer_invoices", description="...", arguments_schema=...),
    ToolSchema(name="list_supplier_invoices", description="...", arguments_schema=...),
    ToolSchema(name="search_supplier_invoices", description="...", arguments_schema=...),
    ToolSchema(name="get_supplier_invoice", description="...", arguments_schema=...),
    ToolSchema(name="count_supplier_invoices", description="...", arguments_schema=...),
]
```

#### Intent to Tool Call Mapping
```python
def structured_intent_to_tool_call(intent: StructuredIntent) -> tuple[str, dict]:
    # Map InvoiceAction to tool_name + params
    # Similar to existing thirdparty mapping
```

### 2. Dolibarr Client Extensions (`core/integrations/dolibarr/client.py`)

#### Enhanced list_invoices
```python
async def list_invoices(
    self,
    limit: int = 100,
    offset: int = 0,
    status: int | None = None,
    date_from: date | None = None,        # NEW
    date_to: date | None = None,          # NEW
    due_from: date | None = None,         # NEW
    due_to: date | None = None,           # NEW
    thirdparty_id: int | None = None,     # NEW
    sortfield: str = "date",              # NEW
    sortorder: str = "DESC",              # NEW
    sqlfilters: str | None = None,        # NEW
) -> list[dict[str, Any]]:
```

#### Enhanced list_supplier_invoices
```python
async def list_supplier_invoices(
    self,
    limit: int = 100,
    offset: int = 0,
    status: int | None = None,
    thirdparty_id: int | None = None,
    date_from: date | None = None,        # NEW
    date_to: date | None = None,          # NEW
    due_from: date | None = None,         # NEW
    due_to: date | None = None,           # NEW
    sortfield: str = "date",              # NEW
    sortorder: str = "DESC",              # NEW
    sqlfilters: str | None = None,        # NEW
) -> list[dict[str, Any]]:
```

**Note**: If Dolibarr REST doesn't support these params, they will be ignored (not sent) and filtering done in-code with pagination safety limits.

### 3. Mappers (`core/integrations/dolibarr/mappers.py`)

#### Customer Invoice Mapper
```python
def dolibarr_to_customer_invoice(data: dict[str, Any]) -> CustomerInvoiceSummary:
    """Map Dolibarr invoice response to CustomerInvoiceSummary."""
    # Map status code to InvoiceStatus enum
    # Convert timestamps to date objects
    # Convert monetary fields to Decimal
    # Extract thirdparty name from nested object or fk_soc lookup
```

#### Supplier Invoice Mapper
```python
def dolibarr_to_supplier_invoice(data: dict[str, Any]) -> SupplierInvoiceSummary:
    """Map Dolibarr supplier invoice response to SupplierInvoiceSummary."""
    # Similar to customer but uses 'socid' field for thirdparty
```

#### Status Mapping
```python
def _map_invoice_status(status_code: int) -> InvoiceStatus:
    mapping = {
        0: InvoiceStatus.DRAFT,
        1: InvoiceStatus.VALIDATED,
        2: InvoiceStatus.PAID,
        3: InvoiceStatus.CANCELLED,
    }
    return mapping.get(status_code, InvoiceStatus.DRAFT)
```

### 4. Invoice Tools (`core/hermes/tools/invoice_tools.py`)

#### Parameter Classes (dataclasses with validation)
```python
@dataclass(frozen=True, slots=True)
class ListCustomerInvoicesParams:
    limit: int = 20
    offset: int = 0
    status: InvoiceStatus | None = None
    thirdparty_id: int | None = None
    thirdparty_name: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    due_from: date | None = None
    due_to: date | None = None
    sort_field: InvoiceSortField = InvoiceSortField.DATE
    sort_order: SortOrder = SortOrder.DESC
    
    def to_dolibarr_params(self) -> dict[str, Any]:
        # Build params for DolibarrClient.list_invoices
        # Use sqlfilters for thirdparty_name search
```

#### Summary Dataclasses
```python
@dataclass(frozen=True, slots=True)
class CustomerInvoiceSummary:
    id: int
    ref: str
    thirdparty_id: int
    thirdparty_name: str
    date: date
    due_date: date | None
    status: InvoiceStatus
    total_ht: Decimal
    total_ttc: Decimal
    paid_amount: Decimal
    remaining_amount: Decimal
```

#### Tool Classes (8 tools)
Each tool follows the exact pattern from `thirdparty_tools.py`:
- `ListCustomerInvoicesTool` (permission: `customer_invoice.read`)
- `SearchCustomerInvoicesTool` (permission: `customer_invoice.read`)
- `GetCustomerInvoiceTool` (permission: `customer_invoice.read`)
- `CountCustomerInvoicesTool` (permission: `customer_invoice.read`)
- `ListSupplierInvoicesTool` (permission: `supplier_invoice.read`)
- `SearchSupplierInvoicesTool` (permission: `supplier_invoice.read`)
- `GetSupplierInvoiceTool` (permission: `supplier_invoice.read`)
- `CountSupplierInvoicesTool` (permission: `supplier_invoice.read`)

#### Registration Function
```python
def register_core_invoice_tools() -> None:
    tool_registry.register_core_tool(ListCustomerInvoicesTool())
    tool_registry.register_core_tool(SearchCustomerInvoicesTool())
    tool_registry.register_core_tool(GetCustomerInvoiceTool())
    tool_registry.register_core_tool(CountCustomerInvoicesTool())
    tool_registry.register_core_tool(ListSupplierInvoicesTool())
    tool_registry.register_core_tool(SearchSupplierInvoicesTool())
    tool_registry.register_core_tool(GetSupplierInvoiceTool())
    tool_registry.register_core_tool(CountSupplierInvoicesTool())
```

### 5. Intent Interpreter Extensions (`core/hermes/query/interpreter.py`)

#### System Prompt Updates
Add invoice tools to the catalog in `_build_system_prompt()`:
- Include all 8 invoice tools in the tools list
- Add examples for invoice queries
- Add prompt injection examples for invoices

#### Deterministic Parser Patterns
Add regex patterns to `query_layer.py` (legacy parser) for:
- "facturas de clientes" → list_customer_invoices
- "facturas de proveedores" → list_supplier_invoices
- "busca factura FAC-..." → search_customer_invoices
- "busca factura FP-..." → search_supplier_invoices
- "cuántas facturas de clientes" → count_customer_invoices
- "cuántas facturas de proveedores" → count_supplier_invoices
- "facturas de [nombre] [mes]" → search with date filters

#### Composite Interpreter
No changes needed — uses the same parser-first + Ollama fallback strategy.

### 6. Main Application (`core/hermes/main.py`)

#### Register Tools
In `lifespan()` startup:
```python
from core.hermes.tools.invoice_tools import register_core_invoice_tools
register_core_invoice_tools()
```

#### Telegram Webhook Routing
In `telegram_webhook()`, after thirdparty handling, add invoice intent handling:
```python
elif interpretation.intent.action in INVOICE_ACTIONS:
    # Map to tool, execute, format
    # Use new formatters
```

#### New Commands (Optional)
```python
elif text == "/facturas":
    # Route to list_customer_invoices with limit=10
elif text == "/facturas_proveedor":
    # Route to list_supplier_invoices with limit=10
```

### 7. Permissions

Add to permission system:
- `customer_invoice.read` — for all 4 customer invoice tools
- `supplier_invoice.read` — for all 4 supplier invoice tools

These are checked by `AuthorizationService.require()` before tool execution.

## Data Flow Examples

### Example 1: "facturas del cliente ACME"
```
User Text: "facturas del cliente ACME"
         ↓
Deterministic Parser: matches pattern "facturas del cliente (.+)"
         ↓
StructuredIntent:
  action: SEARCH_CUSTOMER_INVOICES
  arguments: SearchCustomerInvoicesArgs(query="ACME", party_type=CUSTOMER)
         ↓
AuthorizationService.require(user, "customer_invoice.read")
         ↓
ToolRegistry.execute_tool("search_customer_invoices", query="ACME", ...)
         ↓
SearchCustomerInvoicesTool.execute()
         ↓
params.to_dolibarr_params() → sqlfilters with name LIKE '%ACME%' AND client:=1
         ↓
DolibarrClient.list_invoices(sqlfilters=...)
         ↓
Dolibarr REST: GET /invoices?sqlfilters=...
         ↓
Response → dolibarr_to_customer_invoice() → list[CustomerInvoiceSummary]
         ↓
ToolResult.ok(data={invoices: [...], count: 5, limit: 20, offset: 0, has_more: false})
         ↓
format_customer_invoices_for_telegram()
         ↓
Telegram: "1. FAC-2026-123 - ACME S.L. - 20/08/2026 - 2.420,00 € - Pendiente\n..."
```

### Example 2: "qué debemos a proveedores"
```
User Text: "qué debemos a proveedores"
         ↓
OllamaIntentInterpreter: matches SUM_SUPPLIER_INVOICES
         ↓
StructuredIntent:
  action: SUM_SUPPLIER_INVOICES
  arguments: CountSupplierInvoicesArgs()  # or SumSupplierInvoicesArgs
         ↓
AuthorizationService.require(user, "supplier_invoice.read")
         ↓
ToolRegistry.execute_tool("sum_supplier_invoices" or "count_supplier_invoices")
         ↓
If REST supports remaining_amount aggregation:
  SumSupplierInvoicesTool.execute() → aggregate in code with pagination safety
  Return: "Total pendiente proveedores: 15.678,90 € (23 facturas)"
Else:
  Return: "No puedo calcular el total pendiente porque la API de Dolibarr no expone el importe restante de forma fiable"
```

## Error Handling Strategy

| Error Type | Detection | Response |
|------------|-----------|----------|
| Dolibarr 401 | DolibarrException.status_code == 401 | "No he podido consultar las facturas en este momento" |
| Dolibarr 403 | DolibarrException.status_code == 403 | Same safe message |
| Dolibarr 404 | DolibarrException.status_code == 404 (get_invoice) | "Factura no encontrada" |
| Dolibarr 500 | DolibarrException.status_code >= 500 | Safe message |
| Timeout | httpx.TimeoutException | Safe message |
| Connection | httpx.RequestError | Safe message |
| Validation | Pydantic ValidationError | INVALID_PARAMS |
| Permission | ForbiddenError | "No tienes permiso para consultar facturas de [cliente/proveedor]" |
| Ambiguity | NEEDS_CLARIFICATION status | Clarification message |

## Security Boundaries

1. **No SQL**: Invoice tools import only `DolibarrClient`, never `DatabaseConfig`, `SQLAlchemy`, or MariaDB client
2. **No Write Actions**: `InvoiceAction` enum contains ONLY read actions
3. **No Cross-Instance**: `CompanyContext` determines DolibarrClient; no `instance_id` in arguments
4. **No Extra Fields**: All argument models use `extra="forbid"`
5. **Prompt Injection**: System prompt explicitly lists hostile patterns → NO_MATCH
6. **AI Policy**: `LOCAL_ONLY` enforced in `create_ollama_interpreter()`

## Testing Strategy

### Unit Tests
- Argument model validation (valid/invalid inputs)
- Decimal money arithmetic
- Status code mapping
- Date filter building
- Formatter output format
- sqlfilters construction

### Integration Tests
- Each tool calls DolibarrClient with correct params
- Authorization checked before Dolibarr call
- Cross-instance isolation (mock different clients)
- Error handling paths

### E2E Tests (Telegram Webhook)
- Natural language queries → correct tool → correct response
- Permission denied → no Dolibarr call
- Write attempts → NO_MATCH
- SQL injection → NO_MATCH
- Instance spoofing → validation error
- Dolibarr errors → safe messages
- Pagination → correct limit/offset

## File Structure

```
core/hermes/
├── query/
│   ├── models.py          # + InvoiceAction, InvoiceStatus, InvoiceSortField, arg models, formatters
│   ├── interpreter.py     # + invoice patterns in deterministic parser, invoice tools in system prompt
│   └── factory.py         # (no changes)
├── tools/
│   ├── __init__.py
│   ├── thirdparty_tools.py
│   └── invoice_tools.py   # NEW: 8 invoice tools
├── main.py                # + register_core_invoice_tools(), webhook routing for invoices
└── ...

core/integrations/dolibarr/
├── client.py              # + enhanced list_invoices, list_supplier_invoices
├── mappers.py             # + dolibarr_to_customer_invoice, dolibarr_to_supplier_invoice
└── ...
```

## Dependencies

### New Imports in Modified Files
- `core/hermes/query/models.py`: `Decimal`, `date`, `InvoiceStatus`, `InvoiceAction`, `InvoicePartyType`, `InvoiceSortField`
- `core/integrations/dolibarr/client.py`: `date` (already imported), `Decimal` for monetary params if needed
- `core/integrations/dolibarr/mappers.py`: `Decimal`, `date`, `InvoiceStatus`
- `core/hermes/tools/invoice_tools.py`: All new types, `Tool`, `ToolDefinition`, `ToolResult`, `tool_registry`, `CompanyContext`, `UserContext`, `DolibarrClient`, `DolibarrException`, `InvoiceStatus`, `InvoiceSortField`, `Decimal`, `date`
- `core/hermes/main.py`: `register_core_invoice_tools`, invoice formatters, `InvoiceAction`

### No New External Dependencies
All implementation uses existing dependencies: `pydantic`, `httpx`, `decimal`, `dataclasses`, `datetime`

## Migration Notes

- No database migrations needed
- No config changes needed (permissions added to Dolibarr groups via existing mechanism)
- Existing thirdparty tools unchanged
- Backward compatible: new tools additive only