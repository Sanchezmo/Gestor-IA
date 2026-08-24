# Product Query Layer V4 — Technical Design

**Change**: `product-query-layer-v4`
**Status**: Draft

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                      TELEGRAM WEBHOOK                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      HERMES MAIN                                │
│  • Instance resolution (CompanyContext)                         │
│  • Identity resolution (UserContext)                            │
│  • Webhook secret validation                                    │
│  • Idempotency (Redis)                                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    QUERY LAYER                                  │
│  • DeterministicIntentInterpreter (regex)                       │
│  • OllamaIntentInterpreter (structured output)                  │
│  • CompositeIntentInterpreter (parser-first)                    │
│  • Intent → StructuredIntent → Tool call                        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  AUTHORIZATION SERVICE                          │
│  • Default deny: can()/require()                                │
│  • Permission: product.read                                     │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    TOOL REGISTRY                                │
│  • Tool discovery (core + instance)                             │
│  • Permission check BEFORE execute                              │
│  • Cross-instance validation                                    │
│  • ToolResult standardization                                   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PRODUCT TOOLS                                 │
│  • ListProductsTool    → list_products                          │
│  • SearchProductsTool  → search_products                        │
│  • GetProductTool      → get_product / get_product_by_ref       │
│  • CountProductsTool   → count_products                         │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  DOLIBARR CLIENT                                │
│  • Per-instance HTTP client (base_url + api_key)                │
│  • Page-based pagination                                        │
│  • sqlfilters for search/filters                                │
│  • pagination_data for efficient count                          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                  DOLIBARR REST API                              │
│  • GET /products                                                │
│  • GET /products/{id}                                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. DolibarrClient Changes

### 2.1 Modified `list_products` (client.py)

```python
async def list_products(
    self,
    limit: int = 100,
    page: int = 1,                    # CHANGED: was offset
    sortfield: str = "rowid",
    sortorder: str = "ASC",
    type: int | None = None,          # NEW
    status: int | None = None,        # NEW
    sqlfilters: str | None = None,    # NEW
    pagination_data: bool = False,    # NEW
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    List products/services with page-based pagination and filters.
    
    Returns:
        list[dict] if pagination_data=False
        {"data": [...], "pagination": {"total": N, "page": p, "limit": l, "pages": p}} if True
    """
    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 100

    params: dict[str, Any] = {
        "limit": limit,
        "page": page,
        "sortfield": sortfield,
        "sortorder": sortorder,
    }

    # Build sqlfilters
    sqlfilters_parts: list[str] = []
    if sqlfilters:
        sqlfilters_parts.append(sqlfilters)
    if type is not None:
        if type not in (0, 1):
            raise ValueError("type must be 0 (PRODUCT) or 1 (SERVICE)")
        sqlfilters_parts.append(f"t.type:={type}")
    if status is not None:
        if status < 0:
            raise ValueError("status must be >= 0")
        sqlfilters_parts.append(f"t.status:={status}")

    if sqlfilters_parts:
        params["sqlfilters"] = " AND ".join(sqlfilters_parts)

    if pagination_data:
        params["pagination_data"] = "1"

    result = await self._request("GET", "products", params=params)
    
    if isinstance(result, dict):
        if pagination_data:
            return {
                "data": result.get("data", []),
                "pagination": {
                    "total": result.get("pagination", {}).get("total", 0),
                    "page": result.get("pagination", {}).get("page", page),
                    "limit": result.get("pagination", {}).get("limit", limit),
                    "pages": result.get("pagination", {}).get("pages", 0),
                }
            }
        return result.get("data", [])
    return result
```

### 2.2 New `search_products` (client.py)

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
) -> list[dict[str, Any]]:
    """
    Search products/services by text across ref, label, description.
    Uses sqlfilters with OR conditions.
    """
    if not query or not query.strip():
        return []
    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 100

    # Escape for Dolibarr LIKE
    escaped = query.strip().replace("'", "''").replace("%", "\\%").replace("_", "\\_")
    
    search_conditions = [
        f"t.ref:like:'%{escaped}%'",
        f"t.label:like:'%{escaped}%'",
        f"t.description:like:'%{escaped}%'",
    ]
    
    sqlfilters_parts = [f"({' OR '.join(search_conditions)})"]
    
    if type is not None:
        if type not in (0, 1):
            raise ValueError("type must be 0 (PRODUCT) or 1 (SERVICE)")
        sqlfilters_parts.append(f"t.type:={type}")
    if status is not None:
        if status < 0:
            raise ValueError("status must be >= 0")
        sqlfilters_parts.append(f"t.status:={status}")

    params = {
        "limit": limit,
        "page": page,
        "sortfield": sortfield,
        "sortorder": sortorder,
        "sqlfilters": " AND ".join(sqlfilters_parts),
    }

    result = await self._request("GET", "products", params=params)
    return result.get("data", []) if isinstance(result, dict) else result
```

### 2.3 New `get_product_by_ref` (client.py)

```python
async def get_product_by_ref(self, ref: str) -> dict[str, Any] | None:
    """
    Get product by exact reference (ref field).
    Uses list_products with sqlfilters for exact match.
    """
    if not ref or not ref.strip():
        return None
    
    escaped = ref.strip().replace("'", "''").replace("%", "\\%").replace("_", "\\_")
    
    result = await self.list_products(
        limit=1,
        page=1,
        sqlfilters=f"t.ref:='{escaped}'",
    )
    
    if isinstance(result, dict) and "data" in result:
        data = result["data"]
    else:
        data = result
    
    return data[0] if data else None
```

### 2.4 Keep Existing `get_product` (by ID)

No changes needed - already works with `GET /products/{id}`.

---

## 3. Product Mappers (mappers.py)

### 3.1 `dolibarr_to_product_summary`

```python
def dolibarr_to_product_summary(data: dict[str, Any], currency: str = "EUR") -> dict[str, Any]:
    """
    Convert Dolibarr product to ProductSummary dict.
    """
    # Type mapping
    type_map = {0: "PRODUCT", 1: "SERVICE", "0": "PRODUCT", "1": "SERVICE"}
    product_type = type_map.get(data.get("type", 0), "PRODUCT")
    
    # Money fields - use Decimal
    price = _to_decimal(data.get("price"))
    price_ttc = _to_decimal(data.get("price_ttc"))
    price_min = _to_decimal(data.get("price_min")) if data.get("price_min") is not None else None
    vat_rate = _to_decimal(data.get("tva_tx"))
    
    # Stock fields (may not be present for services)
    stock_reel = _to_decimal(data.get("stock_reel")) if data.get("stock_reel") is not None else None
    desiredstock = _to_decimal(data.get("desiredstock")) if data.get("desiredstock") is not None else None
    seuil_stock_alerte = _to_decimal(data.get("seuil_stock_alerte")) if data.get("seuil_stock_alerte") is not None else None
    
    return {
        "id": data.get("id") or data.get("rowid"),
        "ref": data.get("ref"),
        "label": data.get("label"),
        "type": product_type,
        "status": int(data.get("status", 0)),
        "price": price,
        "price_ttc": price_ttc,
        "vat_rate": vat_rate,
        "currency": currency,
        "stock_reel": stock_reel,
        "desiredstock": desiredstock,
        "seuil_stock_alerte": seuil_stock_alerte,
        "default_warehouse": data.get("fk_default_warehouse"),
        "barcode": data.get("barcode"),
    }
```

### 3.2 `dolibarr_to_product_detail`

```python
def dolibarr_to_product_detail(data: dict[str, Any], currency: str = "EUR") -> dict[str, Any]:
    """
    Convert Dolibarr product to ProductDetail dict (extends summary).
    """
    summary = dolibarr_to_product_summary(data, currency)
    
    # Additional fields
    summary.update({
        "description": data.get("description"),
        "price_min": _to_decimal(data.get("price_min")) if data.get("price_min") is not None else None,
        "price_base_type": data.get("price_base_type"),  # "HT" or "TTC"
        "weight": _to_decimal(data.get("weight")) if data.get("weight") is not None else None,
        "weight_units": data.get("weight_units"),
        "length": _to_decimal(data.get("length")) if data.get("length") is not None else None,
        "surface": _to_decimal(data.get("surface")) if data.get("surface") is not None else None,
        "volume": _to_decimal(data.get("volume")) if data.get("volume") is not None else None,
        "units": data.get("units"),
        # Supplier info if present
        "supplier_info": _extract_supplier_info(data) if _has_supplier_info(data) else None,
        # Extrafields
        "extrafields": _extract_extrafields(data) if _has_extrafields(data) else None,
    })
    
    return summary
```

### 3.3 Helper Functions

```python
def _extract_supplier_info(data: dict) -> dict | None:
    """Extract supplier information if present in Dolibarr response."""
    # Dolibarr may include supplier info in product response
    supplier_fields = ["fk_soc", "soc_name", "supplier_ref", "supplier_price"]
    supplier_data = {k: data.get(k) for k in supplier_fields if k in data and data.get(k) is not None}
    return supplier_data if supplier_data else None

def _has_supplier_info(data: dict) -> bool:
    return any(k in data and data.get(k) is not None for k in ["fk_soc", "soc_name", "supplier_ref"])

def _extract_extrafields(data: dict) -> dict | None:
    """Extract extrafields (keys starting with 'extrafield_' or similar)."""
    extrafields = {k: v for k, v in data.items() if k.startswith("extrafield_") or k.startswith("options_")}
    return extrafields if extrafields else None

def _has_extrafields(data: dict) -> bool:
    return any(k.startswith("extrafield_") or k.startswith("options_") for k in data.keys())
```

---

## 4. Product Tools (product_tools.py)

### 4.1 Parameter Dataclasses

```python
# Allowlists
ALLOWED_PRODUCT_SORT_FIELDS: frozenset[str] = frozenset({
    "rowid", "ref", "label", "description", "type", "status",
    "price", "price_ttc", "tva_tx", "stock_reel", "date_creation", "date_modification"
})

ALLOWED_SORT_ORDERS: frozenset[str] = frozenset({"ASC", "DESC"})

PRODUCT_TYPE_MAP = {"PRODUCT": 0, "SERVICE": 1}
PRODUCT_TYPE_LABELS = {0: "PRODUCT", 1: "SERVICE"}

@dataclass(frozen=True, slots=True)
class ListProductsParams:
    limit: int = 20
    page: int = 1
    product_type: Literal["PRODUCT", "SERVICE"] | None = None
    status: int | None = None
    sort_field: Literal[
        "rowid", "ref", "label", "description", "type", "status",
        "price", "price_ttc", "tva_tx", "stock_reel", "date_creation", "date_modification"
    ] = "label"
    sort_order: Literal["ASC", "DESC"] = "ASC"

    def __post_init__(self):
        if self.limit < 1 or self.limit > 100:
            raise ValueError("limit must be 1-100")
        if self.page < 1:
            raise ValueError("page must be >= 1")
        if self.sort_order not in ALLOWED_SORT_ORDERS:
            raise ValueError(f"sort_order must be ASC or DESC")
        if self.sort_field not in ALLOWED_PRODUCT_SORT_FIELDS:
            raise ValueError(f"sort_field not allowed: {self.sort_field}")
        if self.status is not None and self.status < 0:
            raise ValueError("status must be >= 0")

    def to_dolibarr_params(self) -> dict[str, Any]:
        params = {
            "limit": self.limit,
            "page": self.page,
            "sortfield": self.sort_field,
            "sortorder": self.sort_order,
        }
        if self.product_type is not None:
            params["type"] = PRODUCT_TYPE_MAP[self.product_type]
        if self.status is not None:
            params["status"] = self.status
        return params

@dataclass(frozen=True, slots=True)
class SearchProductsParams:
    query: str
    limit: int = 20
    page: int = 1
    product_type: Literal["PRODUCT", "SERVICE"] | None = None
    status: int | None = None
    sort_field: Literal[...] = "label"  # same as ListProductsParams
    sort_order: Literal["ASC", "DESC"] = "ASC"

    def __post_init__(self):
        if not self.query or not self.query.strip():
            raise ValueError("query cannot be empty")
        if len(self.query) > 200:
            raise ValueError("query too long (max 200 chars)")
        # ... same validations as ListProductsParams

@dataclass(frozen=True, slots=True)
class GetProductParams:
    product_id: int | None = None
    ref: str | None = None

    def __post_init__(self):
        if self.product_id is None and self.ref is None:
            raise ValueError("Either product_id or ref required")
        if self.product_id is not None and self.product_id <= 0:
            raise ValueError("product_id must be > 0")
        if self.ref is not None and not self.ref.strip():
            raise ValueError("ref cannot be empty")

@dataclass(frozen=True, slots=True)
class CountProductsParams:
    product_type: Literal["PRODUCT", "SERVICE"] | None = None
    status: int | None = None

    def __post_init__(self):
        if self.status is not None and self.status < 0:
            raise ValueError("status must be >= 0")

    def to_dolibarr_params(self) -> dict[str, Any]:
        params = {"limit": 1, "pagination_data": True}
        sqlfilters_parts = []
        if self.product_type is not None:
            sqlfilters_parts.append(f"t.type:={PRODUCT_TYPE_MAP[self.product_type]}")
        if self.status is not None:
            sqlfilters_parts.append(f"t.status:={self.status}")
        if sqlfilters_parts:
            params["sqlfilters"] = " AND ".join(sqlfilters_parts)
        return params
```

### 4.2 Tool Classes

Each tool follows the exact pattern from `thirdparty_tools.py` and `invoices/`:

```python
class ListProductsTool(Tool):
    def __init__(self):
        definition = ToolDefinition(
            name="list_products",
            description="Listar productos/servicios de Dolibarr con paginación y filtros",
            parameters_schema={...},  # JSON schema from ListProductsParams
            required_permissions=frozenset(["product.read"]),
            is_core=True,
        )
        super().__init__(definition)

    async def execute(self, company_context, user_context, **params):
        # 1. Validate params → ListProductsParams
        # 2. Create DolibarrClient from company_context
        # 3. Call client.list_products(**params.to_dolibarr_params())
        # 4. Map results using dolibarr_to_product_summary with company_context.currency
        # 5. Return ToolResult.ok with products, count, limit, page, has_more
        # 6. Handle DolibarrException → ToolResult.error(DOLIBARR_ERROR)
        # 7. Handle generic Exception → ToolResult.error(INTERNAL_ERROR)
```

### 4.3 Response Format

```python
# List/Search response
{
    "products": [
        {
            "id": 1,
            "ref": "PINT-001",
            "label": "Pintura plástica blanca",
            "type": "PRODUCT",
            "status": 1,
            "price": "42.50",
            "price_ttc": "51.43",
            "vat_rate": "21.00",
            "currency": "EUR",
            "stock_reel": "125.00",
            "desiredstock": "100.00",
            "seuil_stock_alerte": "20.00",
            "default_warehouse": "ALM-001",
            "barcode": "8412345678901",
        },
        ...
    ],
    "count": 20,
    "limit": 20,
    "page": 1,
    "has_more": true
}

# Get response
{
    "product": {
        "id": 1,
        "ref": "PINT-001",
        "label": "Pintura plástica blanca",
        "type": "PRODUCT",
        "status": 1,
        "description": "Pintura plástica mate para interiores, 4L",
        "price": "42.50",
        "price_ttc": "51.43",
        "price_min": "35.00",
        "price_base_type": "HT",
        "vat_rate": "21.00",
        "currency": "EUR",
        "stock_reel": "125.00",
        "desiredstock": "100.00",
        "seuil_stock_alerte": "20.00",
        "default_warehouse": "ALM-001",
        "weight": "4.0",
        "weight_units": "kg",
        "barcode": "8412345678901",
        "supplier_info": {...},
        "extrafields": {...}
    }
}

# Count response
{"count": 142}
```

---

## 5. Query Layer Integration

### 5.1 Deterministic Parser Extensions (query_layer.py)

Add to `QueryParser` class:

```python
# LIST patterns
LIST_PATTERNS = [
    # ... existing ...
    (r"^lista\s+(productos?|servicios?|cat[aá]logo)\s*$", None),
    (r"^muestra\s+(productos?|servicios?|cat[aá]logo)\s*$", None),
    (r"^ver\s+(productos?|servicios?|cat[aá]logo)\s*$", None),
]

# SEARCH patterns
SEARCH_PATTERNS = [
    # ... existing ...
    (r"^busca\s+(producto|servicio)\s+(.+)$", None),
    (r"^busca\s+(.+)$", None),  # generic search
    (r"^encuentra\s+(producto|servicio)\s+(.+)$", None),
    (r"^encuentra\s+(.+)$", None),
]

# COUNT patterns
COUNT_PATTERNS = [
    # ... existing ...
    (r"^cu[aá]ntos\s+(productos?|servicios?)\s+(hay|tienes?|tiene?)\s*$", None),
    (r"^cu[aá]ntos\s+(productos?|servicios?)\s*$", None),
    (r"^cuenta\s+(productos?|servicios?)\s*$", None),
    (r"^n[uú]mero\s+de\s+(productos?|servicios?)\s*$", None),
]

# GET patterns
GET_PATTERNS = [
    # ... existing ...
    (r"^(detalle|ver|muestra)\s+(?:del\s+)?(?:producto|servicio)\s+(\d+)\s*$", None),
    (r"^(detalle|ver|muestra)\s+(?:del\s+)?(?:producto|servicio)\s+([A-Z0-9\-]+)\s*$", None),  # by ref
    (r"^producto\s+(\d+)\s*$", None),
    (r"^servicio\s+(\d+)\s*$", None),
    (r"^qu[eé]\s+precio\s+tiene\s+([A-Z0-9\-]+)\s*$", None),  # "qué precio tiene PINT-001"
]
```

Add `ProductIntentType` and `ProductFilterType` enums, `ProductIntent` dataclass similar to `ThirdpartyIntent`.

### 5.2 Structured Output Models (query/models.py)

Add to existing file:

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

# Argument models
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

class CountProductsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_type: ProductTypeFilter = ProductTypeFilter.ALL
    status: int | None = Field(default=None, ge=0)

# Union
ProductArgs = ListProductsArgs | SearchProductsArgs | GetProductArgs | CountProductsArgs
```

### 5.3 Intent → Tool Call Mapping (query/models.py)

Extend `structured_intent_to_tool_call()`:

```python
elif action == ProductAction.LIST:
    args = cast(ListProductsArgs, arguments)
    product_type = None
    if args.product_type == ProductTypeFilter.PRODUCT:
        product_type = "PRODUCT"
    elif args.product_type == ProductTypeFilter.SERVICE:
        product_type = "SERVICE"
    
    return "list_products", {
        "limit": args.limit,
        "page": args.page,
        "product_type": product_type,
        "status": args.status,
        "sort_field": args.sort_field.value,
        "sort_order": args.sort_order.value,
    }

elif action == ProductAction.SEARCH:
    # ... similar with query
    return "search_products", {...}

elif action == ProductAction.GET:
    args = cast(GetProductArgs, arguments)
    params = {}
    if args.product_id is not None:
        params["product_id"] = args.product_id
    if args.ref is not None:
        params["ref"] = args.ref
    return "get_product", params

elif action == ProductAction.COUNT:
    # ...
    return "count_products", {...}
```

### 5.4 Tool Catalog for Prompt

Add to `get_tools_catalog_for_prompt()`:

```python
PRODUCT_TOOLS_CATALOG: list[ToolSchema] = [
    ToolSchema(
        name="list_products",
        description="Listar productos/servicios con paginación y filtros opcionales",
        arguments_schema=ListProductsArgs.model_json_schema(),
    ),
    ToolSchema(
        name="search_products",
        description="Buscar productos/servicios por referencia, nombre, descripción",
        arguments_schema=SearchProductsArgs.model_json_schema(),
    ),
    ToolSchema(
        name="get_product",
        description="Obtener detalle de un producto/servicio por ID o referencia",
        arguments_schema=GetProductArgs.model_json_schema(),
    ),
    ToolSchema(
        name="count_products",
        description="Contar total de productos/servicios con filtros opcionales",
        arguments_schema=CountProductsArgs.model_json_schema(),
    ),
]
```

### 5.5 Ollama Prompt Examples

Add to `OllamaIntentInterpreter._build_system_prompt()`:

```
Usuario: "lista productos"
{
  "status": "matched",
  "intent": { "action": "list_products", "arguments": { "product_type": "product", "limit": 20, "page": 1 }},
  ...
}

Usuario: "lista servicios"
{
  "status": "matched",
  "intent": { "action": "list_products", "arguments": { "product_type": "service", "limit": 20, "page": 1 }},
  ...
}

Usuario: "busca pintura blanca"
{
  "status": "matched",
  "intent": { "action": "search_products", "arguments": { "query": "pintura blanca", "limit": 20 }},
  ...
}

Usuario: "busca producto PINT-001"
{
  "status": "matched",
  "intent": { "action": "search_products", "arguments": { "query": "PINT-001", "product_type": "product", "limit": 20 }},
  ...
}

Usuario: "qué precio tiene PINT-001"
{
  "status": "matched",
  "intent": { "action": "get_product", "arguments": { "ref": "PINT-001" }},
  ...
}

Usuario: "cuántos productos tenemos"
{
  "status": "matched",
  "intent": { "action": "count_products", "arguments": { "product_type": "product" }},
  ...
}

Usuario: "cuántos servicios hay"
{
  "status": "matched",
  "intent": { "action": "count_products", "arguments": { "product_type": "service" }},
  ...
}
```

---

## 6. Telegram Formatters (product_formatters.py)

### 6.1 Currency Formatting

```python
CURRENCY_FORMATS = {
    "EUR": {"symbol": "€", "position": "after", "decimal": ",", "thousands": "."},
    "USD": {"symbol": "$", "position": "before", "decimal": ".", "thousands": ","},
    "GBP": {"symbol": "£", "position": "before", "decimal": ".", "thousands": ","},
    # Add more as needed
}

def _format_money(amount: Decimal, currency: str = "EUR") -> str:
    fmt = CURRENCY_FORMATS.get(currency, CURRENCY_FORMATS["EUR"])
    # Format with thousands separator
    formatted = f"{amount:,.2f}"
    # Swap separators based on currency
    if fmt["decimal"] == ",":
        formatted = formatted.replace(",", "X").replace(".", ",").replace("X", ".")
    if fmt["position"] == "after":
        return f"{formatted} {fmt['symbol']}"
    else:
        return f"{fmt['symbol']}{formatted}"
```

### 6.2 List/Search Formatter

```python
def format_products_for_telegram(
    products: list[dict[str, Any]], 
    limit: int, 
    page: int,
    currency: str = "EUR"
) -> str:
    if not products:
        return "No se han encontrado productos/servicios."
    
    lines = ["Productos/servicios encontrados:"]
    for i, p in enumerate(products, 1):
        type_label = "Producto" if p.get("type") == "PRODUCT" else "Servicio"
        price_str = _format_money(p.get("price_ttc", Decimal("0")), currency)
        vat_str = f" (IVA {p.get('vat_rate', 0)}%)" if p.get("vat_rate") else ""
        stock_str = ""
        if p.get("type") == "PRODUCT" and p.get("stock_reel") is not None:
            stock_str = f" | Stock: {p['stock_reel']} uds"
        
        lines.append(
            f"{i}. {p.get('ref', '—')} — {p.get('label', 'Sin nombre')}\n"
            f"   Tipo: {type_label} | Precio: {price_str}{vat_str}{stock_str}"
        )
    
    if len(products) >= limit:
        lines.append(f"\nMostrando {limit} resultados (página {page}).")
    
    return "\n".join(lines)
```

### 6.3 Detail Formatter

```python
def format_product_detail_for_telegram(product: dict[str, Any], currency: str = "EUR") -> str:
    type_emoji = "📦" if product.get("type") == "PRODUCT" else "🔧"
    type_label = "Producto" if product.get("type") == "PRODUCT" else "Servicio"
    
    status_map = {0: "Borrador", 1: "Activo", 2: "Descontinuado"}
    status = status_map.get(product.get("status", 0), f"Desconocido ({product.get('status')})")
    
    lines = [
        f"{type_emoji} *{product.get('ref', '—')} — {product.get('label', 'Sin nombre')}*",
        f"Tipo: {type_label}",
        f"Estado: {status}",
    ]
    
    if product.get("description"):
        lines.append(f"Descripción: {product['description']}")
    
    price_ht = _format_money(product.get("price", Decimal("0")), currency)
    price_ttc = _format_money(product.get("price_ttc", Decimal("0")), currency)
    lines.append(f"Precio base: {price_ht}")
    lines.append(f"Precio con IVA: {price_ttc}")
    
    if product.get("vat_rate"):
        lines.append(f"IVA: {product['vat_rate']}%")
    
    if product.get("price_min"):
        lines.append(f"Precio mínimo: {_format_money(product['price_min'], currency)}")
    
    # Stock (only for products)
    if product.get("type") == "PRODUCT":
        if product.get("stock_reel") is not None:
            lines.append(f"Stock real: {product['stock_reel']} uds")
        if product.get("desiredstock") is not None:
            lines.append(f"Stock deseado: {product['desiredstock']} uds")
        if product.get("seuil_stock_alerte") is not None:
            lines.append(f"Alerta stock: {product['seuil_stock_alerte']} uds")
        if product.get("default_warehouse"):
            lines.append(f"Almacén por defecto: {product['default_warehouse']}")
    
    if product.get("barcode"):
        lines.append(f"Código de barras: {product['barcode']}")
    
    if product.get("supplier_info"):
        lines.append(f"Proveedor: {product['supplier_info'].get('soc_name', '—')}")
    
    return "\n".join(lines)
```

### 6.4 Count Formatter

```python
def format_product_count_for_telegram(count: int, product_type: ProductTypeFilter) -> str:
    if product_type == ProductTypeFilter.PRODUCT:
        return f"Hay {count} productos registrados."
    elif product_type == ProductTypeFilter.SERVICE:
        return f"Hay {count} servicios registrados."
    else:
        return f"Hay {count} productos/servicios registrados."
```

---

## 7. Permission Integration

### 7.1 Add to GestorPermissions (identity.py)

```python
class GestorPermissions:
    # ... existing ...
    PRODUCT_READ = "product.read"
    
    ALL: frozenset[str] = frozenset([
        # ... existing ...
        PRODUCT_READ,
    ])
```

### 7.2 Tool Permission Declaration

Each tool in `product_tools.py`:
```python
required_permissions = frozenset(["product.read"])
```

### 7.3 Default Deny Flow

1. Telegram webhook → `IdentityResolver` → `UserContext` with `effective_permissions`
2. `ToolRegistry.execute_tool()` → `tool.check_permissions(user_context)`
3. If `False` → `ToolResult.error(PERMISSION_DENIED)` **before** DolibarrClient creation
4. Audit log: `AUTHORIZATION_DENIED` with required permission

---

## 8. Registration & Wiring

### 8.1 `core/hermes/tools/__init__.py`

```python
from .product_tools import (
    ListProductsTool,
    SearchProductsTool,
    GetProductTool,
    CountProductsTool,
    register_core_product_tools,
)

__all__ = [
    # ... existing ...
    "ListProductsTool",
    "SearchProductsTool",
    "GetProductTool",
    "CountProductsTool",
    "register_core_product_tools",
]
```

### 8.2 `core/hermes/main.py` - lifespan()

```python
from core.hermes.tools.product_tools import register_core_product_tools

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # ... existing ...
    
    # Register core tools
    register_core_thirdparty_tools()
    register_core_invoice_tools()
    register_core_product_tools()  # NEW
    
    # ... existing ...
```

### 8.3 Webhook Command Routing (main.py)

Add `/productos` and `/servicios` commands similar to `/terceros`, `/facturas`:

```python
elif text == "/productos":
    # Require product.read, execute list_products with product_type="PRODUCT"

elif text == "/servicios":
    # Require product.read, execute list_products with product_type="SERVICE"
```

---

## 9. Testing Architecture

### 9.1 Fake DolibarrClient for Tests

```python
class FakeDolibarrClient:
    """Fake client for testing - simulates Dolibarr REST API."""
    
    def __init__(self, products: list[dict] = None):
        self._products = products or self._default_products()
        self._call_log = []
        self._page_call_count = 0
    
    def _default_products(self) -> list[dict]:
        return [
            {
                "id": 1, "rowid": 1, "ref": "PROD-001", "label": "Producto A",
                "type": 0, "status": 1, "price": "100.00", "price_ttc": "121.00",
                "tva_tx": "21.00", "stock_reel": "50", "description": "Desc A",
            },
            {
                "id": 2, "rowid": 2, "ref": "PROD-002", "label": "Producto B",
                "type": 0, "status": 1, "price": "200.00", "price_ttc": "242.00",
                "tva_tx": "21.00", "stock_reel": "25", "description": "Desc B",
            },
            {
                "id": 3, "rowid": 3, "ref": "SERV-001", "label": "Servicio X",
                "type": 1, "status": 1, "price": "150.00", "price_ttc": "181.50",
                "tva_tx": "21.00", "stock_reel": None, "description": "Desc X",
            },
        ]
    
    async def list_products(self, **kwargs) -> list[dict]:
        self._call_log.append(("list_products", kwargs))
        self._page_call_count += 1
        if self._page_call_count > 10:
            raise AssertionError("Pagination loop detected: >10 pages requested")
        
        page = kwargs.get("page", 1)
        limit = kwargs.get("limit", 20)
        type_filter = kwargs.get("type")
        status_filter = kwargs.get("status")
        sqlfilters = kwargs.get("sqlfilters")
        
        # Filter products
        filtered = self._products
        if type_filter is not None:
            filtered = [p for p in filtered if p.get("type") == type_filter]
        if status_filter is not None:
            filtered = [p for p in filtered if p.get("status") == status_filter]
        if sqlfilters:
            filtered = self._apply_sqlfilters(filtered, sqlfilters)
        
        # Pagination
        start = (page - 1) * limit
        end = start + limit
        page_data = filtered[start:end]
        
        if kwargs.get("pagination_data"):
            return {
                "data": page_data,
                "pagination": {
                    "total": len(filtered),
                    "page": page,
                    "limit": limit,
                    "pages": (len(filtered) + limit - 1) // limit,
                }
            }
        return page_data
    
    async def get_product(self, product_id: int) -> dict:
        self._call_log.append(("get_product", product_id))
        for p in self._products:
            if p.get("id") == product_id or p.get("rowid") == product_id:
                return p
        raise DolibarrException("Not found", endpoint=f"products/{product_id}", status_code=404)
    
    async def search_products(self, **kwargs) -> list[dict]:
        self._call_log.append(("search_products", kwargs))
        # Reuse list_products with sqlfilters
        return await self.list_products(**kwargs)
    
    def _apply_sqlfilters(self, products: list[dict], sqlfilters: str) -> list[dict]:
        # Simple implementation for test fake
        # Real implementation would parse sqlfilters
        return products
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, *args):
        pass
```

### 9.2 Test Structure

```
tests/
├── unit/
│   ├── test_product_models.py       # ProductSummary, ProductDetail, Decimal
│   ├── test_product_tools.py        # Parameter validation, mappers
│   └── test_product_formatters.py   # Telegram formatting, currency
├── integration/
│   └── test_product_query_layer.py  # Full stack: Query→ToolRegistry→Auth→Tool→FakeClient
├── isolation/
│   └── test_product_cross_instance.py  # Instance A vs B isolation
└── e2e/
    └── test_product_telegram_e2e.py    # Full Telegram webhook flow
```

---

## 10. Error Handling

### 10.1 Error Codes (Consistent with Existing)

| Code | When |
|------|------|
| `INVALID_PARAMS` | Parameter validation failed |
| `PERMISSION_DENIED` | Missing `product.read` |
| `NOT_FOUND` | Product ID/ref not found |
| `DOLIBARR_ERROR` | Dolibarr API error (timeout, 5xx, 401) |
| `INTERNAL_ERROR` | Unexpected exception |
| `CROSS_INSTANCE_ERROR` | Instance ID mismatch |

### 10.2 Safe Error Messages

- Never expose Dolibarr internals (endpoint, status_code, API key)
- User-friendly: "No he podido consultar Dolibarr en este momento"
- Log details server-side for debugging

---

## 11. Performance Considerations

- **Pagination**: Page-based, max 100 per page (Dolibarr limit)
- **Count**: Single API call with `pagination_data=True` + `limit=1`
- **Search**: Server-side via `sqlfilters` (no full catalog download)
- **Caching**: Not in V4 (future: Redis cache for catalog)
- **Connection reuse**: `DolibarrClient` uses async context manager per request

---

## 12. Migration Path (Future Phases)

| Phase | Adds |
|-------|------|
| V4 (this) | Read-only query layer |
| V5 | Product write (create/update), stock movements |
| V6 | Price rules, quotes, orders |
| V7 | Advanced inventory (warehouses, reservations) |

---

## 13. Open Questions (Resolved in Implementation)

1. **Dolibarr `type` field values**: Verified as 0=product, 1=service via docs
2. **`pagination_data` on `/products`**: Assumed supported (added in Dolibarr 14+); fallback implemented
3. **Currency in product response**: Dolibarr doesn't return currency per product; use `CompanyContext.currency`
4. **Stock for services**: `stock_reel` is null/absent for services; handle gracefully
5. **Extrafields**: Include if present, don't require