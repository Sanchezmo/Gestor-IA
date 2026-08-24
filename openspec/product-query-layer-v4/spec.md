# Product Query Layer V4 — Specification

**Change**: `product-query-layer-v4`
**Status**: Draft
**Depends on**: Business Insights V1 (completed at 183a467)

---

## 1. Functional Requirements

### 1.1 Product/Service Domain Model

Dolibarr manages both products and services under the same endpoint (`/products`) distinguished by the `type` field:
- `type = 0` → **PRODUCT** (physical goods, stockable)
- `type = 1` → **SERVICE** (non-stockable services)

The read-only catalog model must support both types with optional fields that Dolibarr may or may not return.

### 1.2 Core Query Operations

| Operation | Tool Name | Description |
|-----------|-----------|-------------|
| List | `list_products` | Paginated list with optional type/status filters |
| Search | `search_products` | Text search across ref, label, description |
| Get | `get_product` | Retrieve single product by ID or exact ref |
| Count | `count_products` | Total count with optional filters |

### 1.3 Natural Language Intents

Hermes must interpret these Spanish queries (deterministic parser + Ollama fallback):

| Intent | Example Queries |
|--------|-----------------|
| `LIST_PRODUCTS` | "lista productos", "lista servicios", "muéstrame el catálogo" |
| `SEARCH_PRODUCTS` | "busca pintura", "busca producto PINT-001", "busca servicio instalación" |
| `GET_PRODUCT` | "producto PINT-001", "detalle del producto 42", "qué precio tiene PINT-001" |
| `COUNT_PRODUCTS` | "cuántos productos tenemos", "cuántos servicios hay" |

Filters:
- `PRODUCT` → "productos", "producto"
- `SERVICE` → "servicios", "servicio"

---

## 2. DolibarrClient Contract

### 2.1 Enhanced `list_products`

```python
async def list_products(
    self,
    limit: int = 100,
    page: int = 1,                    # CHANGED: page-based (was offset)
    sortfield: str = "rowid",
    sortorder: str = "ASC",
    type: int | None = None,          # NEW: 0=PRODUCT, 1=SERVICE
    status: int | None = None,        # NEW: 0=draft, 1=active, etc.
    sqlfilters: str | None = None,    # NEW: advanced filters
    pagination_data: bool = False,    # NEW: return total count
) -> list[dict] | dict[str, Any]:
    """
    Returns list[dict] or {"data": [...], "pagination": {...}} if pagination_data=True
    """
```

**Validation**:
- `page >= 1` (default 1)
- `1 <= limit <= 100` (default 20, matching other tools)
- `type` must be 0 or 1 if provided
- `status >= 0` if provided

### 2.2 New `search_products`

```python
async def search_products(
    self,
    query: str,
    limit: int = 20,
    page: int = 1,
    type: int | None = None,
    status: int | None = None,
    sortfield: str = "label",
    sortorder: str = "ASC",
) -> list[dict]:
    """
    Search products/services by ref, label, description using sqlfilters.
    """
```

Uses `sqlfilters` with OR across: `t.ref:like:'%query%'`, `t.label:like:'%query%'`, `t.description:like:'%query%'`

### 2.3 Enhanced `get_product`

```python
async def get_product(self, product_id: int) -> dict[str, Any]:
    """Get product by Dolibarr ID (rowid)."""
```

### 2.4 New `get_product_by_ref` (optional, for natural language)

```python
async def get_product_by_ref(self, ref: str) -> dict[str, Any] | None:
    """Get product by exact reference (ref field). Returns None if not found."""
    # Uses list_products with sqlfilters: t.ref:=ref and limit=1
```

---

## 3. Domain Models

### 3.1 ProductSummary (for list/search responses)

```python
@dataclass(frozen=True, slots=True)
class ProductSummary:
    id: int                    # Dolibarr rowid
    ref: str                   # Reference (e.g., "PINT-001")
    label: str                 # Name/label
    type: Literal["PRODUCT", "SERVICE"]  # Derived from type field (0/1)
    status: int                # 0=draft, 1=active, etc.
    price: Decimal             # Base price (HT)
    price_ttc: Decimal         # Price with VAT
    vat_rate: Decimal          # VAT rate (e.g., 21.0)
    currency: str              # From CompanyContext (e.g., "EUR")
    stock_reel: Decimal | None = None      # Real stock (products only)
    desiredstock: Decimal | None = None
    seuil_stock_alerte: Decimal | None = None
    default_warehouse: str | None = None
    barcode: str | None = None
```

### 3.2 ProductDetail (for get_product response)

Extends `ProductSummary` with:
```python
description: str | None = None
price_min: Decimal | None = None
price_base_type: str | None = None       # "HT" or "TTC"
weight: Decimal | None = None
weight_units: str | None = None
length: Decimal | None = None
surface: Decimal | None = None
volume: Decimal | None = None
units: str | None = None
supplier_info: dict | None = None        # If available in response
extrafields: dict[str, Any] | None = None
```

### 3.3 Type Mapping

```python
DOLIBARR_TYPE_TO_LABEL = {0: "PRODUCT", 1: "SERVICE"}
DOLIBARR_TYPE_FROM_LABEL = {"PRODUCT": 0, "SERVICE": 1}
```

---

## 4. Tools Specification

### 4.1 Common Permission

All product tools require: `product.read`

```python
required_permissions = frozenset(["product.read"])
```

### 4.2 list_products Tool

**Parameters** (validated via dataclass + Pydantic schema):
```python
limit: int = 20 (1-100)
page: int = 1 (>=1)
type: Literal["PRODUCT", "SERVICE"] | None = None
status: int | None = None (>=0)
sort_field: Literal["rowid", "ref", "label", "description", "type", "status", "price", "price_ttc", "tva_tx", "stock_reel"] = "label"
sort_order: Literal["ASC", "DESC"] = "ASC"
```

**Dolibarr params mapping**:
- `type` → `sqlfilters` += `t.type:=0` or `t.type:=1`
- `status` → `sqlfilters` += `t.status:=N`
- `pagination_data=True` for count efficiency

**Response**:
```json
{
  "products": [ProductSummary...],
  "count": 20,
  "limit": 20,
  "page": 1,
  "has_more": true
}
```

### 4.3 search_products Tool

**Parameters**:
```python
query: str (1-200 chars, required)
limit: int = 20 (1-100)
page: int = 1 (>=1)
type: Literal["PRODUCT", "SERVICE"] | None = None
status: int | None = None
sort_field: ... = "label"
sort_order: ... = "ASC"
```

**Dolibarr params**: Builds `sqlfilters` with OR across ref, label, description + type/status filters

**Response**: Same as list_products

### 4.4 get_product Tool

**Parameters**:
```python
product_id: int (required, >0)
# OR for natural language:
ref: str (exact reference, alternative to product_id)
```

**Behavior**:
- If `product_id` provided → direct GET /products/{id}
- If `ref` provided → search with `t.ref:=ref` limit=1
- If multiple matches on ref → return ambiguous result (list of candidates)

**Response**:
```json
{
  "product": ProductDetail
}
```

### 4.5 count_products Tool

**Parameters**:
```python
type: Literal["PRODUCT", "SERVICE"] | None = None
status: int | None = None
```

**Implementation**: Uses `list_products(limit=1, pagination_data=True)` and returns `pagination.total`

**Response**:
```json
{"count": 142}
```

---

## 5. Query Layer Intents

### 5.1 New Enums (query/models.py)

```python
class ProductAction(StrEnum):
    LIST = "list_products"
    SEARCH = "search_products"
    GET = "get_product"
    COUNT = "count_products"

class ProductTypeFilter(StrEnum):
    ALL = "all"
    PRODUCT = "product"
    SERVICE = "service"

class ProductSortField(StrEnum):
    ROWID = "rowid"
    REF = "ref"
    LABEL = "label"
    DESCRIPTION = "description"
    TYPE = "type"
    STATUS = "status"
    PRICE = "price"
    PRICE_TTC = "price_ttc"
    VAT_RATE = "tva_tx"
    STOCK_REEL = "stock_reel"
```

### 5.2 Argument Models

```python
class ListProductsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=100)
    page: int = Field(default=1, ge=1)
    product_type: ProductTypeFilter = ProductTypeFilter.ALL
    status: int | None = Field(default=None, ge=0)
    sort_field: ProductSortField = ProductSortField.LABEL
    sort_order: SortOrder = SortOrder.ASC

class SearchProductsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(default=20, ge=1, le=100)
    page: int = Field(default=1, ge=1)
    product_type: ProductTypeFilter = ProductTypeFilter.ALL
    status: int | None = Field(default=None, ge=0)
    sort_field: ProductSortField = ProductSortField.LABEL
    sort_order: SortOrder = SortOrder.ASC

class GetProductArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int | None = Field(default=None, gt=0)
    ref: str | None = Field(default=None, min_length=1, max_length=100)
    @model_validator(mode="after")
    def check_one_identifier(self):
        if self.product_id is None and self.ref is None:
            raise ValueError("Either product_id or ref required")
        return self

class CountProductsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_type: ProductTypeFilter = ProductTypeFilter.ALL
    status: int | None = Field(default=None, ge=0)
```

### 5.3 Tool Catalog for Prompt

Add to `get_tools_catalog_for_prompt()`:
- `list_products`, `search_products`, `get_product`, `count_products`

---

## 6. Authorization

### 6.1 Permission Constant

Add to `GestorPermissions` (identity.py):
```python
PRODUCT_READ = "product.read"
```

Update `ALL` frozenset.

### 6.2 AuthorizationService Integration

No code changes needed — `AuthorizationService.can(user_context, "product.read")` and `require()` work automatically with the new permission in `UserContext.effective_permissions`.

### 6.3 Tool Registration

Each product tool declares:
```python
required_permissions = frozenset(["product.read"])
```

ToolRegistry.execute_tool() checks permissions BEFORE executing (DEFAULT DENY).

---

## 7. Telegram Formatters

### 7.1 Currency Handling

```python
def _format_money(amount: Decimal, currency: str = "EUR") -> str:
    """Format using CompanyContext.currency (ISO 4217)."""
    # EUR: 1.234,56 €
    # USD: $1,234.56
    # Use Babel or simple mapping for common currencies
```

### 7.2 list_products / search_products Format

```
PINT-001 — Pintura plástica blanca
Tipo: Producto
Precio: 42,50 €
IVA: 21 %
Stock: 125 uds

SER-001 — Aplicación de pintura
Tipo: Servicio
Precio: 150,00 €
IVA: 21 %

Mostrando 20 resultados (página 1)
```

### 7.3 get_product Format

```
📦 PINT-001 — Pintura plástica blanca
Tipo: Producto
Estado: Activo
Descripción: Pintura plástica mate para interiores, 4L
Precio: 42,50 € (IVA 21% incluido: 51,43 €)
Precio mínimo: 35,00 €
Stock real: 125 uds
Stock deseado: 100 uds
Alerta stock: 20 uds
Almacén: Principal
Código de barras: 8412345678901
```

### 7.4 count_products Format

```
Hay 142 productos registrados.
Hay 28 servicios registrados.
Hay 170 productos/servicios registrados (total).
```

---

## 8. Pagination Strategy

### 8.1 Page-Based (Consistent with Invoices/Thirdparties)

- Page 1 = first page
- `limit` max 100 (Dolibarr limit)
- `has_more` = `len(results) == limit`
- Loop protection: max 10 pages in fake client tests

### 8.2 Count Optimization

- `count_products` uses `pagination_data=True` → single API call with `limit=1`
- Falls back to full pagination only if Dolibarr doesn't return pagination metadata

---

## 9. Search Strategy

### 9.1 Server-Side (Preferred)

Use Dolibarr `sqlfilters` with OR across:
- `t.ref:like:'%query%'`
- `t.label:like:'%query%'`
- `t.description:like:'%query%'`

### 9.2 Fallback (If Needed)

Only if Dolibarr API doesn't support search filters: fetch page 1 with high limit and filter in Python. **Document why if used.**

---

## 10. Multi-Company Isolation

### 10.1 Requirements

- Each `CompanyContext` creates its own `DolibarrClient` with instance-specific `base_url` and `api_key`
- `ToolRegistry.execute_tool()` validates `instance_id` matches both contexts
- IdentityResolver resolves Telegram user → Dolibarr user per instance
- Tests must verify Instance A never sees Instance B's catalog

### 10.2 Test Data

Fake DolibarrClient returns different product sets per instance:
- Instance A: PRODUCT_A, PRODUCT_B, SERVICE_A
- Instance B: PRODUCT_X, SERVICE_Y

---

## 11. Security

### 11.1 Default Deny

- User without `product.read` → `PERMISSION_DENIED` before Dolibarr call
- Test: verify `DolibarrClient.list_products` NOT called on denied request

### 11.2 Read-Only Enforcement

- No write tools registered in this phase
- Query layer intents only map to read tools
- Prompt injection defenses in Ollama interpreter (existing)

### 11.3 SQL Injection Prevention

- All user input escaped via `escape_sql_like()` before `sqlfilters`
- No raw SQL construction
- Allowlists for sort fields, sort orders

---

## 12. Testing Requirements

### 12.1 Unit Tests

- Models: ProductSummary, ProductDetail, type mapping, Decimal precision
- Mappers: Dolibarr → normalized, edge cases (missing fields)
- Parameters: validation (page>=1, limit 1-100, type enum)
- Formatters: currency formatting, empty/partial data

### 12.2 Integration Tests (Real Stack)

```
Query Layer → ToolRegistry → AuthService → ProductTool → FakeDolibarrClient
```

- Fake client simulates pages 1-4 then empty
- Verify exact API calls (page, limit, filters)
- Verify permission check order (auth before Dolibarr)

### 12.3 Pagination Tests

- Page 1, 2, 3, 4 → data
- Page 5 → empty → stop
- Loop detection: >10 pages → AssertionError

### 12.4 Product/Service Filter Tests

- `type=PRODUCT` → only type 0
- `type=SERVICE` → only type 1
- `type=None` → both
- Count respects type filter

### 12.5 Cross-Instance Isolation Tests

- Instance A query → only A's catalog
- Instance B query → only B's catalog
- Same Telegram user ID in both → different Dolibarr users, different catalogs

### 12.6 Default Deny Test

- User without `product.read` → `PERMISSION_DENIED`
- Verify 0 Dolibarr calls

### 12.7 Security Tests

- Prompt injection → `NO_MATCH`
- SQL-like input → escaped, not executed
- Write intent ("crea producto") → `NO_MATCH`

---

## 13. Acceptance Criteria

| # | Criterion | Verification |
|---|-----------|--------------|
| 1 | `list_products` returns paginated products | Integration test |
| 2 | `search_products` finds by ref/label/description | Integration test |
| 3 | `get_product` by ID and by ref | Integration test |
| 4 | `count_products` uses pagination_data | Integration test |
| 5 | PRODUCT vs SERVICE filter works | Unit + integration |
| 6 | Money fields use Decimal | Unit test |
| 7 | Currency from CompanyContext | Formatter test |
| 8 | `product.read` permission enforced | Default deny test |
| 9 | Instance A ≠ Instance B catalog | Cross-instance test |
| 10 | Pagination loop protection | Test with >10 pages |
| 11 | Telegram formatter works | Formatter test |
| 12 | Query layer recognizes intents | Deterministic + Ollama tests |
| 13 | No mypy regressions | `make lint` passes |
| 14 | All existing tests pass | Regression suite |

---

## 14. Files to Create/Modify

### New Files
```
core/hermes/tools/product_tools.py          # Tools implementation
core/hermes/tools/product_models.py         # ProductSummary, ProductDetail
core/integrations/dolibarr/mappers.py       # Add product mappers (extend)
tests/unit/test_product_models.py
tests/unit/test_product_tools.py
tests/integration/test_product_query_layer.py
tests/isolation/test_product_cross_instance.py
openspec/product-query-layer-v4/spec.md     # This file
```

### Modified Files
```
core/integrations/dolibarr/client.py        # Enhanced list_products, search, get_by_ref
core/hermes/query/models.py                 # ProductAction, ProductTypeFilter, args
core/hermes/query/interpreter.py            # Add product examples to Ollama prompt
core/hermes/identity.py                     # Add PRODUCT_READ permission
core/hermes/main.py                         # Register product tools in lifespan
core/hermes/tools/__init__.py               # Export product tools
```

---

## 15. Dependencies

- **Business Insights V1** (completed): provides pattern for insights/tools/formatters
- **Invoice Query Layer V3**: provides pattern for invoice tools, query layer, pagination
- **Thirdparty Query Layer**: provides pattern for thirdparty tools, search, count
- **DolibarrClient**: base HTTP client, exception handling
- **ToolRegistry/AuthorizationService**: permission enforcement infrastructure
- **Query Layer**: deterministic parser + Ollama structured output

---

## 16. Rollback Plan

If issues arise:
1. Disable product tools in `main.py` lifespan registration
2. Remove tool registrations from `tool_registry`
3. Revert `DolibarrClient` product methods to pre-V4 state
4. Query layer intents will return `NO_MATCH` for product queries (safe fallback)

No database migrations or config changes required.