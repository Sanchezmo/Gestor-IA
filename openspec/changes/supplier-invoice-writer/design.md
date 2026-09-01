# Design: Supplier Invoice ERP Writer Phase 1

## Technical Approach

Extend the existing Command Layer (CreateSupplierInvoiceHandler) with a new **ConfirmSupplierInvoiceHandler** that executes the durable state machine transitions `CONFIRMING → SUPPLIER_CREATED → INVOICE_CREATED → ATTACHMENT_PENDING → COMPLETED` using user-scoped Dolibarr API keys. The handler integrates with `DocumentIdempotencyManager` for milestone persistence, `DolibarrReconciliationService` for `ERP_RESULT_UNKNOWN` handling, and implements mandatory post-write verification. AI Act compliance is achieved through a typed `AIFeatureRegistry`, `AIUsePolicy` enforcement, `AITraceabilityLogger`, and explicit human oversight at the confirmation boundary.

## Architecture Decisions

### Decision: ConfirmSupplierInvoiceHandler extends CommandHandler

| Option | Tradeoff | Decision |
|--------|----------|----------|
| New handler extending CommandHandler | Reuses existing patterns, audit trail, context propagation | ✅ Chosen |
| New WriterService outside Command Layer | Cleaner separation but duplicates state machine, idempotency, client logic | ❌ Rejected |
| Add methods to DocumentIngestionService | Keeps logic together but mixes READ/WRITE responsibilities | ❌ Rejected |

**Rationale**: The Command Layer V3 already provides durable state machine, audit logging, and CompanyContext propagation. Extending it maintains architectural consistency and avoids parallel write paths.

### Decision: User-Scoped API Keys — FAIL CLOSED

| Option | Tradeoff | Decision |
|--------|----------|----------|
| `create_dolibarr_client_for_user()` only, raise on missing key | Strict tenant isolation, auditability | ✅ Chosen |
| Fallback to admin key on 401/missing | Convenience but violates data isolation | ❌ Rejected |

**Rationale**: Multi-company isolation requires each user's Dolibarr operations to use their own credentials. FAIL CLOSED prevents accidental cross-tenant access.

### Decision: Post-Write Verification is Mandatory

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Read-back-and-compare after every CREATE | Guarantees data integrity, catches Dolibarr-side mutations | ✅ Chosen |
| Trust Dolibarr response only | Simpler but misses silent data corruption | ❌ Rejected |

**Rationale**: Dolibarr may transform data on write (tax recalculation, rounding). Verification ensures what was intended matches what was persisted.

### Decision: ERP_RESULT_UNKNOWN Requires Reconciliation Before Retry

| Option | Tradeoff | Decision |
|--------|----------|----------|
| Reconcile via commercial key, then decide | Prevents duplicates from blind retry | ✅ Chosen |
| Blind retry with idempotency key | Simpler but risks duplicate invoices if Dolibarr created before timeout | ❌ Rejected |

**Rationale**: Network timeout ≠ operation failed. Dolibarr may have created the resource. Reconciliation by `(instance_id, supplier_tax_id, supplier_invoice_number)` is the only safe path.

### Decision: AI Act Compliance as Typed Registry, Not Ad-Hoc Checks

| Option | Tradeoff | Decision |
|--------|----------|----------|
| `AIFeatureRegistry` with `AIUsePolicy` enum per feature | Type-safe, auditable, enforces at registry level | ✅ Chosen |
| Scattered `if feature == "extraction": LOCAL_ONLY` checks | Easy to miss, hard to audit, no versioning | ❌ Rejected |

**Rationale**: Regulatory compliance requires explicit, versioned, auditable policy decisions — not scattered conditionals.

### Decision: Config-Driven Endpoints — Zero Hardcoded Localhost

| Option | Tradeoff | Decision |
|--------|----------|----------|
| InstanceConfig template with no defaults, validation rejects localhost | VPS-ready, explicit configuration | ✅ Chosen |
| Keep localhost defaults for local dev | Convenience but production accidents | ❌ Rejected |

**Rationale**: Production deployments must have explicit endpoints. Local dev uses explicit `http://localhost:11434` in config — no implicit fallback.

---

## Data Flow

```text
User Confirmation (Telegram callback)
         │
         ▼
┌─────────────────────────────────────┐
│ ConfirmSupplierInvoiceHandler       │
│ 1. Validate pending command (Redis) │
│ 2. Check user API key (FAIL CLOSED) │
│ 3. Duplicate check (audit + Dolibarr)│
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ CONFIRMING → SUPPLIER_RESOLUTION    │
│ 1. Search thirdparties by tax_id    │
│ 2. Resolve: FOUND_SUPPLIER /        │
│    FOUND_NOT_SUPPLIER / NOT_FOUND / │
│    AMBIGUOUS                        │
│ 3. Create/enable supplier if needed │
│ 4. mark_supplier_created()          │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ SUPPLIER_CREATED → INVOICE_CREATE   │
│ 1. Map Draft → Dolibarr payload     │
│ 2. POST /supplierinvoices           │
│ 3. GET /supplierinvoices/{id}       │
│ 4. Verify: socid, ref_supplier,     │
│    totals, lines, VAT breakdown     │
│ 5. mark_invoice_created()           │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ INVOICE_CREATED → ATTACHMENT_UPLOAD │
│ 1. POST /documents (supplierinvoices)│
│ 2. Retry with exponential backoff   │
│ 3. Verify by document_hash          │
│ 4. mark_attachment_pending()        │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│ ATTACHMENT_PENDING → COMPLETED      │
│ 1. mark_completed()                 │
│ 2. Log AI trace with all correlated │
│    IDs (preview_hash, confirmation, │
│    ERP write ID)                    │
└─────────────────────────────────────┘

Error Path (ERP_RESULT_UNKNOWN):
         │
         ▼
┌─────────────────────────────────────┐
│ POST timeout / network error        │
│ 1. mark_erp_result_unknown()        │
│ 2. DolibarrReconciliationService    │
│    .reconcile_supplier_invoice()    │
│ 3. Outcomes: UNIQUE_MATCH (adopt),  │
│    NO_MATCH (retry if safe),        │
│    AMBIGUOUS/ERROR (block)          │
└─────────────────────────────────────┘
```

---

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `core/hermes/commands/handlers/supplier_invoice.py` | Create | `ConfirmSupplierInvoiceHandler` implementing full state machine with user-scoped client |
| `core/hermes/audit.py` | Modify | Add `mark_supplier_created()`, `mark_invoice_created()`, `mark_attachment_pending()`, `mark_erp_result_unknown()` to `DocumentIdempotencyManager` |
| `core/hermes/context.py` | Modify | Add `create_dolibarr_client_for_user(identity)` with FAIL CLOSED semantics |
| `core/hermes/invoices/models.py` | Modify | Add `AIUsePolicy` enum, `ERP_RESULT_UNKNOWN` state, `AITraceEntry`, `HumanOversightRecord`, `RuntimeVersion` |
| `core/hermes/invoices/ingestion.py` | Modify | Add `PENDING_CONFIRMATION` entry point, trigger handler on confirmation |
| `core/integrations/dolibarr/client.py` | Modify | Remove admin fallback, enforce user-scoped Authorization header, add `get_supplier_invoice()` |
| `core/integrations/dolibarr/reconciliation.py` | Create | `DolibarrReconciliationService` with commercial key search and `ReconciliationDetail` outcomes |
| `core/integrations/dolibarr/mappers.py` | Create | Draft → Dolibarr payload mappers for supplier invoice, lines, withholding (documented limitation) |
| `core/integrations/dolibarr/verification.py` | Create | Post-write verification: read-back comparison with Decimal precision |
| `core/ai/registry.py` | Create | `AIFeatureRegistry`, `AIRegistryEntry`, `AIUsePolicy` enum, policy resolver |
| `core/ai/traceability.py` | Create | `AITraceabilityLogger` with operation_id correlation across extraction→preview→confirmation→ERP |
| `core/ai/retention.py` | Create | `RetentionConfig` per-instance, per-category TTL with purge job |
| `core/ai/oversight.py` | Create | `HumanOversightRecorder` at confirmation boundary |
| `core/ai/transparency.py` | Create | `TransparencyNotice` mechanism with configurable delivery |
| `core/ai/hr_firewall.py` | Create | `RegulatoryReviewGate` blocking `REGULATORY_REVIEW_REQUIRED` features |
| `core/ai/versioning.py` | Create | `RuntimeVersioning` capturing versions at operation start |
| `config/instances/template.yaml` | Modify | Remove localhost defaults, add `ollama_base_url`, `dolibarr_internal_url`, `redis_*`, `mariadb_*`, `task_policies`, `cloud_ai_*` |
| `core/hermes/commands/pending_store.py` | Modify | Atomic confirm validates user/instance/TTL |
| `core/hermes/audit_logger.py` | Modify | Log ERP actions with correlation_id |

---

## Interfaces / Contracts

### AIUsePolicy Enum

```python
# core/hermes/invoices/models.py
from enum import Enum

class AIUsePolicy(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_HUMAN_OVERSIGHT = "REQUIRE_HUMAN_OVERSIGHT"
    LOCAL_ONLY = "LOCAL_ONLY"
    CLOUD_ALLOWED = "CLOUD_ALLOWED"
    REGULATORY_REVIEW_REQUIRED = "REGULATORY_REVIEW_REQUIRED"
```

### AIFeatureRegistry

```python
# core/ai/registry.py
from dataclasses import dataclass
from typing import Dict

@dataclass(frozen=True)
class AIRegistryEntry:
    feature_id: str
    system_id: str
    provider: str
    model: str
    purpose: str
    risk_level: str  # MINIMAL, LIMITED, HIGH, UNACCEPTABLE
    data_policy: str
    human_oversight: bool
    transparency: bool
    enabled: bool
    version: str

class AIFeatureRegistry:
    def __init__(self, entries: Dict[str, AIRegistryEntry], task_policies: Dict[str, AIUsePolicy]):
        self._entries = entries
        self._policies = task_policies

    def get_policy(self, feature_id: str) -> AIUsePolicy:
        return self._policies.get(feature_id, AIUsePolicy.DENY)

    def is_allowed(self, feature_id: str, execution_location: str) -> bool:
        policy = self.get_policy(feature_id)
        if policy == AIUsePolicy.DENY:
            return False
        if policy == AIUsePolicy.REGULATORY_REVIEW_REQUIRED:
            return False
        if policy == AIUsePolicy.LOCAL_ONLY and execution_location != "local":
            return False
        return True
```

### DocumentIdempotencyManager — New Milestone Methods

```python
# core/hermes/audit.py
class DocumentIdempotencyManager:
    def mark_supplier_created(self, durable_id: str, supplier_id: int) -> None:
        """Persist supplier creation milestone with ERP supplier ID."""
    
    def mark_invoice_created(self, durable_id: str, invoice_id: int) -> None:
        """Persist invoice creation milestone with ERP invoice ID."""
    
    def mark_attachment_pending(self, durable_id: str, attachment_id: str) -> None:
        """Persist attachment upload milestone."""
    
    def mark_erp_result_unknown(
        self, 
        durable_id: str, 
        operation_type: str,  # "supplier_create" | "invoice_create" | "attachment_upload"
        context: dict  # {timeout_ms, attempt, commercial_key}
    ) -> None:
        """Persist ERP_RESULT_UNKNOWN state with reconciliation context."""
    
    def get_state(self, durable_id: str) -> DocumentState:
        """Extended return: includes erp_supplier_id, erp_invoice_id, erp_attachment_id, milestones[]"""
```

### DolibarrReconciliationService

```python
# core/integrations/dolibarr/reconciliation.py
from dataclasses import dataclass
from enum import Enum

class ReconciliationAction(str, Enum):
    ADOPTED = "ADOPTED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    BLOCKED_MANUAL = "BLOCKED_MANUAL"
    ERROR = "ERROR"

@dataclass(frozen=True)
class DuplicateCheckDetail:
    match_count: int
    matches: list[dict]  # {id, ref_supplier, supplier_tax_id, status}
    searched_key: dict   # {instance_id, supplier_tax_id, supplier_invoice_number}

@dataclass(frozen=True)
class ReconciliationDetail:
    action_taken: ReconciliationAction
    adopted_id: int | None
    match_count: int
    timestamp: str  # ISO8601

class DolibarrReconciliationService:
    def __init__(self, client_factory: Callable[[Identity], DolibarrClient]):
        self._client_factory = client_factory

    def reconcile_supplier_invoice(
        self, 
        identity: Identity,
        supplier_tax_id: str,
        supplier_invoice_number: str,
        supplier_dolibarr_id: int | None = None
    ) -> ReconciliationDetail:
        """Search by commercial key, return action and adopted ID if UNIQUE_MATCH."""
```

### Post-Write Verification

```python
# core/integrations/dolibarr/verification.py
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class VerificationResult:
    passed: bool
    discrepancies: list[str]  # Empty if passed

def verify_supplier_invoice(
    client: DolibarrClient,
    invoice_id: int,
    expected: SupplierInvoiceDraft,
    resolved_supplier_id: int
) -> VerificationResult:
    """GET /supplierinvoices/{id} and compare all fields with Decimal precision."""
```

### Human Oversight Record

```python
# core/ai/oversight.py
from dataclasses import dataclass

@dataclass(frozen=True)
class HumanOversightRecord:
    confirmed_by: int  # Telegram user_id
    confirmed_at: str  # ISO8601
    preview_hash: str  # SHA256 of preview shown to user
    feature_chain: list[str]  # ["extraction", "validation", "invoice_processing"]
```

### Runtime Versioning

```python
# core/ai/versioning.py
from dataclasses import dataclass

@dataclass(frozen=True)
class RuntimeVersion:
    gestor_ia_version: str
    workflow_version: str
    ai_config_version: str  # Hash of AI registry config
    provider_version: str   # Ollama version
    model_version: str      # Model tag/hash
    dolibarr_client_version: str
    config_schema_version: str
```

---

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `ConfirmSupplierInvoiceHandler` state transitions | Mock `DocumentIdempotencyManager`, `DolibarrClient`, `CompanyContext`; verify each milestone method called in order |
| Unit | Supplier resolution (4 outcomes) | Parameterized tests: FOUND_SUPPLIER, FOUND_NOT_SUPPLIER, NOT_FOUND, AMBIGUOUS |
| Unit | Post-write verification comparison | Mock `get_supplier_invoice` returning matching/mismatched data; assert `VerificationResult` |
| Unit | ERP reconciliation outcomes | Mock Dolibarr search returning 0/1/N matches + errors; assert `ReconciliationDetail` |
| Unit | AI registry policy enforcement | Test `is_allowed()` for each `AIUsePolicy` × execution_location combination |
| Unit | Config validation rejects localhost | Load template with localhost → expect validation error |
| Integration | Full happy path (mock Dolibarr) | Wire handler → idempotency → client → verification; assert COMPLETED state |
| Integration | ERP_RESULT_UNKNOWN recovery | Simulate timeout → mark_erp_result_unknown → reconciliation → adopt/retry/block |
| Integration | Crash recovery from each milestone | Persist state at SUPPLIER_CREATED/INVOICE_CREATED/ATTACHMENT_PENDING; restart handler; assert resume |
| Integration | Attachment retry without invoice recreation | Fail upload → retry → verify only attachment POST called |
| E2E | Telegram confirmation → COMPLETED | Integration test with test containers (MariaDB, Redis, mock Dolibarr) |
| E2E | AI trace correlation | Verify `operation_id` flows through extraction → preview → confirmation → ERP write |

---

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary.

---

## Migration / Rollout

**No migration required.** Phase 1 adds new handler and extends existing components without schema changes:

1. **Feature flag**: `ENABLE_WRITER_PHASE1=false` disables `ConfirmSupplierInvoiceHandler` entirely
2. **Database**: Uses existing `gestor_ia_audit` table; new milestone columns are additive
3. **Config**: New fields in `template.yaml` are additive; old templates still parse
4. **Code**: New handler file; `CreateSupplierInvoiceHandler` untouched for backward compatibility

**Rollout steps**:
1. Deploy config template with new fields (no code change)
2. Deploy code with feature flag OFF
3. Validate config loads, registry initializes
4. Enable feature flag for canary instance
5. Run mock/fake tests (GATE_A)
6. Run read-only Dolibarr smoke tests (GATE_C)
7. Full enable

---

## Open Questions

- [ ] **Withholding tax mapping**: Dolibarr REST API may not support `withholding_tax` on supplier invoices directly. Spec documents this as limitation. Need to verify Dolibarr version capabilities and document workaround (custom field? separate credit note?).
- [ ] **Attachment idempotency key**: `document_hash` used for idempotency — is SHA256 of file content sufficient? What if same file uploaded to different invoices?
- [ ] **AI trace storage**: Where are `AITraceEntry` records persisted? New table? Extend `gestor_ia_audit`? Spec says "audit DB" — confirm schema.
- [ ] **RegulatoryReviewGate enforcement**: Spec says DISABLED for `REGULATORY_REVIEW_REQUIRED`. Is this a config toggle or hardcoded block? Need to clarify for HR firewall implementation.
- [ ] **Multi-VAT Dolibarr compatibility**: Spec mentions "Dolibarr may not accept tax breakdown structure". Need integration test matrix across Dolibarr versions to define mapper compatibility layer.
- [ ] **Redis loss recovery**: Spec says "durable state sufficient". Confirm `DocumentIdempotencyManager.get_state()` reconstructs full state from MariaDB alone without Redis.

---

## Summary

**Approach**: New `ConfirmSupplierInvoiceHandler` extending Command Layer V3, implementing full durable state machine with user-scoped Dolibarr client, post-write verification, ERP reconciliation, and AI Act compliance architecture.

**Key Decisions**: 6 (handler pattern, FAIL CLOSED, mandatory verification, reconciliation-before-retry, typed AI registry, zero-hardcoded-config)

**Files Affected**: 17 new/modified files across handlers, audit, context, models, ingestion, Dolibarr client/mappers/verification/reconciliation, AI registry/traceability/oversight/retention/transparency/hr_firewall/versioning, config template, pending store, audit logger

**Testing Strategy**: Unit (state transitions, resolution, verification, reconciliation, policies, config), Integration (happy path, ERP_RESULT_UNKNOWN recovery, crash recovery, attachment retry), E2E (Telegram flow, AI trace correlation)

**Next Step**: Ready for tasks (sdd-tasks).