# SDD Tasks: Invoice Read-Only Query Layer (Query Layer V3)

## Change ID
`invoice-query-layer-v3`

## Task Breakdown

### Phase 1: Dolibarr Client & Mappers (Foundation)

#### Task 1.1: Enhance DolibarrClient with Invoice Filters
**File**: `core/integrations/dolibarr/client.py`
- [ ] Add `date_from`, `date_to`, `due_from`, `due_to`, `thirdparty_id`, `sortfield`, `sortorder`, `sqlfilters` params to `list_invoices()`
- [ ] Add same params to `list_supplier_invoices()`
- [ ] Convert `date` objects to Unix timestamps for Dolibarr API
- [ ] If Dolibarr doesn't support a param, don't send it (graceful degradation)
- [ ] Update docstrings

**Acceptance**: Both methods accept all filter params without errors

#### Task 1.2: Add Invoice Mappers
**File**: `core/integrations/dolibarr/mappers.py`
- [ ] Add `dolibarr_to_customer_invoice(data: dict) -> CustomerInvoiceSummary`
- [ ] Add `dolibarr_to_supplier_invoice(data: dict) -> SupplierInvoiceSummary`
- [ ] Add `_map_invoice_status(status_code: int) -> InvoiceStatus` helper
- [ ] Convert timestamps to `date` objects
- [ ] Convert monetary fields to `Decimal`
- [ ] Extract thirdparty name from `soc_name` or `fk_soc` lookup

**Acceptance**: Mappers produce correctly typed summary objects

### Phase 2: Query Models & Formatters

#### Task 2.1: Add Invoice Enums and Argument Models
**File**: `core/hermes/query/models.py`
- [ ] Add `InvoiceAction` enum (10 actions)
- [ ] Add `InvoicePartyType` enum (CUSTOMER, SUPPLIER)
- [ ] Add `InvoiceStatus` enum (DRAFT, VALIDATED, PAID, CANCELLED)
- [ ] Add `InvoiceSortField` enum
- [ ] Add 8 argument models with `extra="forbid"`:
  - `ListCustomerInvoicesArgs`, `SearchCustomerInvoicesArgs`, `GetCustomerInvoiceArgs`, `CountCustomerInvoicesArgs`
  - `ListSupplierInvoicesArgs`, `SearchSupplierInvoicesArgs`, `GetSupplierInvoiceArgs`, `CountSupplierInvoicesArgs`
- [ ] Update `StructuredIntent` to accept `InvoiceAction` and `InvoiceArgs` union
- [ ] Update `structured_intent_to_tool_call()` to handle invoice actions

**Acceptance**: All models validate correctly, extra fields rejected

#### Task 2.2: Add Invoice Formatters
**File**: `core/hermes/query/models.py`
- [ ] `format_customer_invoices_for_telegram(invoices, limit, offset) -> str`
- [ ] `format_supplier_invoices_for_telegram(invoices, limit, offset) -> str`
- [ ] `format_customer_invoice_detail_for_telegram(invoice) -> str`
- [ ] `format_supplier_invoice_detail_for_telegram(invoice) -> str`
- [ ] `format_invoice_count_for_telegram(count, party_type) -> str`
- [ ] `format_invoice_sum_for_telegram(total, party_type, period) -> str`

**Format Examples**:
```
Customer:
FAC-2026-123
Cliente: ACME S.L.
Fecha: 20/08/2026
Vence: 19/09/2026
Total: 2.420,00 €
Estado: Pendiente

Supplier:
FP-2026-88
Proveedor: Pinturas ACME
Fecha: 19/08/2026
Vence: 18/09/2026
Total: 843,25 €
Estado: Pendiente
```

**Acceptance**: Formatters produce expected output for test data

#### Task 2.3: Add Invoice Tools Catalog for Prompt
**File**: `core/hermes/query/models.py`
- [ ] Add `INVOICE_TOOLS_CATALOG` with 8 ToolSchema entries
- [ ] Update `get_tools_catalog_for_prompt()` to include invoice tools

**Acceptance**: Catalog includes all 8 invoice tools with correct schemas

### Phase 3: Invoice Tools

#### Task 3.1: Create Invoice Tools Module
**File**: `core/hermes/tools/invoice_tools.py` (NEW)
- [ ] Define parameter dataclasses (8) with validation:
  - `ListCustomerInvoicesParams`, `SearchCustomerInvoicesParams`, `GetCustomerInvoiceParams`, `CountCustomerInvoicesParams`
  - `ListSupplierInvoicesParams`, `SearchSupplierInvoicesParams`, `GetSupplierInvoiceParams`, `CountSupplierInvoicesParams`
- [ ] Define summary dataclasses:
  - `CustomerInvoiceSummary`, `SupplierInvoiceSummary`
- [ ] Implement 8 Tool classes following `thirdparty_tools.py` pattern:
  - `ListCustomerInvoicesTool` (permission: `customer_invoice.read`)
  - `SearchCustomerInvoicesTool` (permission: `customer_invoice.read`)
  - `GetCustomerInvoiceTool` (permission: `customer_invoice.read`)
  - `CountCustomerInvoicesTool` (permission: `customer_invoice.read`)
  - `ListSupplierInvoicesTool` (permission: `supplier_invoice.read`)
  - `SearchSupplierInvoicesTool` (permission: `supplier_invoice.read`)
  - `GetSupplierInvoiceTool` (permission: `supplier_invoice.read`)
  - `CountSupplierInvoicesTool` (permission: `supplier_invoice.read`)
- [ ] Each tool: validate params → create DolibarrClient → call API → map results → return ToolResult
- [ ] Error handling: DolibarrException → safe message, validation → INVALID_PARAMS
- [ ] Add `register_core_invoice_tools()` function

**Acceptance**: All 8 tools registered, execute correctly with mock Dolibarr

#### Task 3.2: Add sqlfilters for Search
**File**: `core/hermes/tools/invoice_tools.py`
- [ ] Implement search sqlfilters for customer invoices:
  - Search across: ref, thirdparty name, total_ttc
  - `sqlfilters = "(t.ref:like:'%query%' OR t.soc_name:like:'%query%' OR t.total_ttc:like:'%query%')" + client:=1`
- [ ] Implement search sqlfilters for supplier invoices:
  - Search across: ref, thirdparty name, total_ttc
  - `sqlfilters = "(t.ref:like:'%query%' OR t.soc_name:like:'%query%' OR t.total_ttc:like:'%query%')" + fournisseur:=1`
- [ ] Escape special chars for LIKE queries

**Acceptance**: Search finds invoices by ref, thirdparty name, amount

### Phase 4: Intent Interpreter

#### Task 4.1: Extend Deterministic Parser
**File**: `core/hermes/query_layer.py` (legacy parser)
- [ ] Add regex patterns for invoice queries:
  - `facturas de clientes?` → list_customer_invoices
  - `facturas de proveedores?` → list_supplier_invoices
  - `busca factura (FAC|FP)-\S+` → search with ref
  - `facturas del (cliente|proveedor) (.+)` → search with thirdparty_name
  - `facturas de (.+) (de|del|en) (enero|febrero|...|diciembre)` → search with date_from/date_to
  - `cuántas facturas de (clientes|proveedores)` → count
  - `qué facturas de (clientes|proveedores) están (pendientes|pagadas)` → search with status
  - `qué facturas vencen (esta semana|este mes)` → search with due_from/due_to

**Acceptance**: Parser returns correct legacy intents for all example queries

#### Task 4.2: Update Ollama System Prompt
**File**: `core/hermes/query/interpreter.py`
- [ ] In `OllamaIntentInterpreter._build_system_prompt()`:
  - Add all 8 invoice tools to catalog
  - Add invoice examples (customer + supplier)
  - Add prompt injection examples for invoices
  - Add "NEEDS_CLARIFICATION" examples for ambiguous queries

**Acceptance**: Ollama correctly classifies invoice intents

#### Task 4.3: Update Legacy-to-Structured Conversion
**File**: `core/hermes/query/interpreter.py`
- [ ] In `DeterministicIntentInterpreter._legacy_to_structured()`:
  - Map legacy invoice intent types to `InvoiceAction`
  - Map legacy filter types to `InvoicePartyType`
  - Build correct argument models

**Acceptance**: Deterministic interpreter produces valid StructuredIntent for invoices

### Phase 5: Main Application Integration

#### Task 5.1: Register Invoice Tools
**File**: `core/hermes/main.py`
- [ ] Import `register_core_invoice_tools` from `core.hermes.tools.invoice_tools`
- [ ] Call `register_core_invoice_tools()` in `lifespan()` startup

**Acceptance**: Tools available in registry after startup

#### Task 5.2: Route Invoice Intents in Webhook
**File**: `core/hermes/main.py`
- [ ] In `telegram_webhook()`, after thirdparty handling, add invoice intent handling:
  - Check if `interpretation.intent.action` is in `INVOICE_ACTIONS` set
  - Map to tool name via `structured_intent_to_tool_call()`
  - Execute tool via `tool_registry.execute_tool()`
  - Format response using new formatters
  - Audit logging

**Acceptance**: Invoice queries work end-to-end via Telegram

#### Task 5.3: Add Optional Commands
**File**: `core/hermes/main.py`
- [ ] Add `/facturas` → `list_customer_invoices` with limit=10
- [ ] Add `/facturas_proveedor` → `list_supplier_invoices` with limit=10

**Acceptance**: Commands work and return formatted results

### Phase 6: Permissions

#### Task 6.1: Verify Permission Strings
- [ ] Confirm `customer_invoice.read` and `supplier_invoice.read` are valid Dolibarr permissions
- [ ] If not, document how to configure in Dolibarr (module: facture/fournisseur, submodule: facture/fournisseur, permission: read)

**Acceptance**: AuthorizationService.require() works with these permissions

### Phase 7: Tests

#### Task 7.1: Unit Tests for Models
**File**: `tests/unit/test_invoice_models.py` (NEW)
- [ ] Test argument model validation (valid, invalid, extra fields)
- [ ] Test Decimal money arithmetic
- [ ] Test status mapping
- [ ] Test formatter outputs
- [ ] Test structured_intent_to_tool_call for invoice actions

#### Task 7.2: Unit Tests for Tools
**File**: `tests/unit/test_invoice_tools.py` (NEW)
- [ ] Test each tool with mock DolibarrClient
- [ ] Test parameter validation
- [ ] Test authorization check (permission granted/denied)
- [ ] Test error handling (DolibarrException, timeout, 404)
- [ ] Test cross-instance isolation (different clients)

#### Task 7.3: Integration Tests for Interpreter
**File**: `tests/integration/test_invoice_interpreter.py` (NEW)
- [ ] Test deterministic parser patterns
- [ ] Test Ollama interpretation (mocked)
- [ ] Test composite interpreter fallback
- [ ] Test NEEDS_CLARIFICATION for ambiguous queries

#### Task 7.4: E2E Tests
**File**: `tests/e2e/test_invoice_e2e.py` (NEW)
- [ ] Happy path: "facturas del cliente ACME" → customer invoices
- [ ] Happy path: "facturas del proveedor Pinturas ACME" → supplier invoices
- [ ] Permissions: customer-only user denied supplier query
- [ ] Count: "cuántas facturas de clientes hay"
- [ ] Date filter: "facturas de clientes de agosto"
- [ ] Ambiguity: "facturas de ACME" → NEEDS_CLARIFICATION
- [ ] Write attempt: "crea una factura" → NO_MATCH
- [ ] SQL injection: "SELECT * FROM llx_facture" → NO_MATCH
- [ ] Instance spoofing: fake Ollama with instance_id → INVALID_OUTPUT
- [ ] Cross-instance: Company A user never queries Company B
- [ ] Dolibarr timeout → safe message
- [ ] Money precision: Decimal arithmetic verified
- [ ] Pagination: limit/offset respected

#### Task 7.5: Isolation Tests
**File**: `tests/isolation/test_invoice_isolation.py` (NEW)
- [ ] Customer invoice tools only call customer endpoints
- [ ] Supplier invoice tools only call supplier endpoints
- [ ] No cross-contamination of data

### Phase 8: Quality Gates

#### Task 8.1: Run Full Test Suite
- [ ] `make test` → all tests pass (unit + isolation + e2e)
- [ ] `make test-isolation` → all cross-instance tests pass
- [ ] `make lint` → ruff clean
- [ ] `make type-check` → mypy: 0 new errors (baseline 171)

#### Task 8.2: Verify No Regressions
- [ ] All existing 241+ tests still pass
- [ ] Thirdparty query layer still works
- [ ] Telegram webhook still handles /terceros correctly

#### Task 8.3: Git Hygiene
- [ ] `git diff --check` → no whitespace errors
- [ ] `git ls-files | grep -E '(__pycache__|\.pyc$)'` → empty
- [ ] No tracked cache files

### Phase 9: Documentation

#### Task 9.1: Update README
**File**: `README.md`
- [ ] Add to "IMPLEMENTADO" section:
  - ✅ Terceros read-only
  - ✅ Facturas cliente read-only
  - ✅ Facturas proveedor read-only
- [ ] Add to "PLANIFICADO" section:
  - 📋 Productos
  - 📋 Presupuestos
  - 📋 Escritura de facturas

#### Task 9.2: Document Dolibarr Limitations
**File**: `docs/invoice-query-limitations.md` (NEW)
- [ ] Document which Dolibarr REST filters are supported vs in-code
- [ ] Document status code mapping
- [ ] Document aggregation limitations
- [ ] Document any missing fields in REST response

## Task Dependencies

```
Phase 1 (1.1, 1.2) 
    ↓
Phase 2 (2.1, 2.2, 2.3) 
    ↓
Phase 3 (3.1, 3.2) 
    ↓
Phase 4 (4.1, 4.2, 4.3)
    ↓
Phase 5 (5.1, 5.2, 5.3)
    ↓
Phase 6 (6.1)
    ↓
Phase 7 (7.1, 7.2, 7.3, 7.4, 7.5)
    ↓
Phase 8 (8.1, 8.2, 8.3)
    ↓
Phase 9 (9.1, 9.2)
```

## Parallelization Opportunities

- Tasks 2.1, 2.2, 2.3 can be done in parallel after Phase 1
- Tasks 3.1, 3.2 can be done in parallel after Phase 2
- Tasks 4.1, 4.2, 4.3 can be done in parallel after Phase 2
- Tasks 7.1, 7.2, 7.3 can be done in parallel after Phases 3-4
- Task 6.1 can be done anytime after Phase 3

## Estimated Effort

| Phase | Tasks | Est. Lines | Complexity |
|-------|-------|------------|------------|
| 1 | 2 | ~150 | Medium |
| 2 | 3 | ~300 | Medium |
| 3 | 2 | ~600 | High |
| 4 | 3 | ~200 | Medium |
| 5 | 3 | ~100 | Low |
| 6 | 1 | ~10 | Low |
| 7 | 5 | ~800 | High |
| 8 | 3 | ~50 | Low |
| 9 | 2 | ~50 | Low |
| **Total** | **24** | **~2260** | **High** |

## Definition of Done

All tasks complete when:
1. All 24 tasks checked off
2. All quality gates pass (tests, lint, mypy)
3. E2E scenarios from spec.md verified manually
4. Final report generated with 41 items
5. No push - only local commits shown