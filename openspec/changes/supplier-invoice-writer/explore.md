# Exploration: Supplier Invoice ERP Writer (Phase 1)

## Current State

The Gestor-IA codebase implements a **complete ingestion pipeline** for supplier invoices up to the **preview/confirmation boundary**:

### ✅ Already Implemented (Reader Phase)

| Component | Status | Key Characteristics |
|-----------|--------|---------------------|
| **CompanyContext** | ✅ Complete | Immutable, explicit per-request, carries InstanceConfig |
| **InstanceConfig** | ✅ Complete | Per-company config, no globals, secrets via SecretResolver |
| **Identity Resolution** | ✅ Complete | TelegramIdentity → DolibarrUser → UserContext, FAIL CLOSED on missing API key |
| **SupplierResolver** | ✅ Complete | READ-ONLY during preview, user-scoped DolibarrClient, tax_id → fiscal → name → fuzzy, AMBIGUOUS blocks |
| **InvoiceExtractor** | ✅ Complete | LOCAL_ONLY Ollama, JSON Schema structured output, PDF text+OCR, FAIL CLOSED if model unavailable |
| **Validator** | ✅ Complete | Deterministic, Decimal-only, HARD_ERRORS vs REVIEW_WARNINGS, tax reconstruction from lines |
| **DocumentIngestionService** | ✅ Complete | State machine (13 states), Redis (transient) + MariaDB (durable), idempotency by document_hash + commercial key |
| **DolibarrClient** | ✅ Complete | REST API, user-scoped, FAIL CLOSED on 401/403, DB fallback for broken REST API |
| **DocumentIdempotencyManager** | ✅ Complete | MariaDB (gestor_ia_audit), composite key (instance_id, supplier_tax_id, supplier_invoice_number), 13-state machine with valid transitions |
| **CreateSupplierInvoiceHandler** | ✅ Partial | Command Layer V3, creates invoice in Dolibarr, uses admin API key (NOT user-scoped) |

### 📋 Document State Machine (13 States)

```
RECEIVED → PROCESSING → REVIEW → PENDING_CONFIRMATION → CONFIRMING → 
  SUPPLIER_CREATED → INVOICE_CREATED → ATTACHMENT_PENDING → COMPLETED
                                    ↳ ERP_RESULT_UNKNOWN (timeout)
                                    ↳ FAILED_RETRYABLE / FAILED_FINAL
```

### 🔑 Key Architectural Decisions Already Enforced

- **Multi-company isolation**: 1 company = 1 Dolibarr instance = 1 InstanceConfig
- **User-scoped API keys**: Dolibarr operations use USER'S API key, NO admin fallback (FAIL CLOSED)
- **LOCAL_ONLY extraction**: Invoice processing never leaves the server (Ollama)
- **Explicit CompanyContext**: No global mutable state, propagated per-request
- **Durable idempotency**: MariaDB `gestor_ia_audit` with composite key `(instance_id, supplier_tax_id, supplier_invoice_number)`
- **ref_supplier as primary identity**: Supplier invoice number from supplier used for deduplication

---

## Gaps for Writer Phase 1

### 1. **Confirmation Boundary Missing** ❌
- No handler that executes the `CONFIRMING → SUPPLIER_CREATED → INVOICE_CREATED → ATTACHMENT_PENDING → COMPLETED` transition
- `DocumentIngestionService` stops at `REVIEW` state (preview generated)
- `CreateSupplierInvoiceHandler` exists but:
  - Uses **admin API key** (`company_context.create_dolibarr_client()`) instead of user-scoped
  - Does NOT implement the durable state machine transitions
  - No post-write verification (read back and compare)
  - No attachment upload logic

### 2. **Post-Write Verification Missing** ❌
- No `read back and compare` after Dolibarr CREATE
- No handling of `ERP_RESULT_UNKNOWN` (POST timeout → reconcile before retry)
- No attachment retry without invoice recreation

### 3. **AI Act Compliance Gaps** ❌
| Requirement | Current State | Gap |
|-------------|---------------|-----|
| Feature-level classification | Extraction is LOCAL_ONLY | Need explicit `AIUsePolicy` per feature |
| Data minimisation | Not formally documented | Need extraction schema minimisation proof |
| Human oversight | Preview exists | Need explicit "human-in-the-loop" confirmation boundary |
| Transparency | No traceability log | Need extraction confidence + AI model version logging |
| Runtime policy versioning | Static config | Need `AIUsePolicy` versioning |

### 4. **VPS/Remote AI Architecture Gaps** ❌
| Requirement | Current State | Gap |
|-------------|---------------|-----|
| No hardcoded localhost/127.0.0.1 | InstanceConfig has `host: "127.0.0.1"` defaults | Template uses localhost; must be config-driven |
| No fixed ports | `dolibarr_apache_port: 8081` default | Must come from config |
| No /home/saulo paths | `documents_path: "/var/lib/gestor-ia/{instance_id}/documents"` | OK - uses template variable |
| Remote AI server separate from VPS | Ollama endpoint in AIConfig | Need explicit separation in config |

### 5. **HR/Employment Firewall** ❌
- No `REGULATORY_REVIEW_REQUIRED` gating for future HR features
- Need explicit firewall in architecture

---

## Approaches

### Approach A: Extend Existing Command Layer (Recommended)
**Description**: Build `ConfirmSupplierInvoiceHandler` that:
- Uses `company_context.create_dolibarr_client_for_user(identity)` (user-scoped)
- Implements full state machine: `PENDING_CONFIRMATION → CONFIRMING → SUPPLIER_CREATED → INVOICE_CREATED → ATTACHMENT_PENDING → COMPLETED`
- Post-write verification: read back invoice, compare totals, lines, supplier
- Handles `ERP_RESULT_UNKNOWN`: reconciliation via `DuplicateCheckDetail` + `ReconciliationDetail`
- Attachment upload with retry (no invoice recreation)

| Pros | Cons |
|------|------|
| Reuses existing `CommandHandler` pattern | Requires careful state machine implementation |
| User-scoped API keys enforced by CompanyContext | Must handle idempotency at each milestone |
| Explicit audit trail via DocumentIdempotencyManager | Post-write verification adds latency |
| Aligns with existing `CreateSupplierInvoiceHandler` | Complex error handling for ERP_RESULT_UNKNOWN |

**Effort**: Medium-High

### Approach B: New Writer Service (Not Recommended)
**Description**: Create parallel `SupplierInvoiceWriterService` outside Command Layer

| Pros | Cons |
|------|------|
| Clean separation of concerns | **VIOLATES "DO NOT CREATE PARALLEL ARCHITECTURES"** |
| Could use different patterns | Duplicates DolibarrClient, idempotency, state machine |
| | Breaks CompanyContext propagation pattern |

**Effort**: High (and architecturally wrong)

### Approach C: Extend Ingestion Service with Writer Methods
**Description**: Add `confirm_and_write()` method to `DocumentIngestionService`

| Pros | Cons |
|------|------|
| Keeps logic together | **VIOLATES single responsibility** - ingestion ≠ writing |
| Reuses existing state machine | Mixes READ (preview) with WRITE (ERP) |
| | Harder to test and audit |

**Effort**: Medium (but wrong pattern)

---

## Recommendation

**Approach A: Extend Command Layer with `ConfirmSupplierInvoiceHandler`**

This is the only approach that:
1. **Respects existing architecture** - extends Command Layer V3, doesn't create parallel paths
2. **Enforces user-scoped permissions** - uses `CompanyContext.create_dolibarr_client_for_user()`
3. **Implements durable state machine** - uses `DocumentIdempotencyManager` for each milestone
4. **Maintains AI Act compliance** - explicit human confirmation boundary at `PENDING_CONFIRMATION`
5. **Supports VPS/Remote AI** - all endpoints from InstanceConfig, no hardcoded values
6. **Enables multi-company isolation** - instance_id carried throughout

### Key Implementation Requirements

| Requirement | Implementation |
|-------------|----------------|
| **User-scoped Dolibarr writes** | `ctx.create_dolibarr_client_for_user(identity)` - FAIL CLOSED if no user API key |
| **Commercial idempotency** | `(instance_id, supplier_tax_id, supplier_invoice_number)` via `DocumentIdempotencyManager` |
| **State machine milestones** | `mark_supplier_created()`, `mark_invoice_created()`, `mark_attachment_pending()`, `mark_completed()` |
| **Post-write verification** | `client.get_supplier_invoice(invoice_id)` → compare `ref_supplier`, `total`, `lines`, `supplier_id` |
| **ERP_RESULT_UNKNOWN handling** | On timeout: `mark_erp_result_unknown()` → reconciliation query → only then retry |
| **Attachment retry** | Separate `upload_document()` call, no invoice recreation, idempotent by document_hash |
| **AI Act: feature classification** | Add `AIPolicyScope.LOCAL_ONLY` for invoice_processing, extraction, validation |
| **AI Act: traceability** | Log extraction model, confidence, inference_count in audit |
| **HR firewall** | Add `REGULATORY_REVIEW_REQUIRED` enum state, block HR features at config level |

### Configuration Changes Needed

1. **InstanceConfig template**: Remove localhost defaults, require explicit `internal_url`, `ollama_endpoint`
2. **AIConfig**: Add `task_policies` with explicit `invoice_processing: LOCAL_ONLY`, `extraction: LOCAL_ONLY`, `validation: LOCAL_ONLY`
3. **Add remote AI server config**: Separate `remote_ai_endpoint`, `remote_ai_model` for future cloud tasks

---

## Risks

1. **State machine complexity**: 13 states with valid transitions - bugs in transition logic could cause stuck documents
2. **ERP_RESULT_UNKNOWN race**: Timeout on POST → Dolibarr may have created invoice → reconciliation must be bulletproof
3. **User API key rotation**: If user's Dolibarr API key rotates mid-flow, writes fail (FAIL CLOSED - correct but UX impact)
4. **Attachment upload failures**: Large PDFs may timeout → need chunked upload or async retry
5. **Multi-VAT validation**: Dolibarr may not accept tax breakdown structure → need mapper compatibility
6. **Dolibarr version differences**: API behavior varies by version → need version-aware mappers

---

## Ready for Proposal

**Yes** - The exploration is complete. The orchestrator should:

1. Create a **Proposal** (`sdd-propose`) for "Supplier Invoice ERP Writer Phase 1" with:
   - Scope: Confirmation boundary + Writer handler + State machine milestones + Post-write verification
   - Explicit OUT OF SCOPE: Actual Dolibarr writes in tests (mocks only), HR features, Cloud AI routing
   - AI Act compliance requirements as acceptance criteria

2. Key proposal decisions needed:
   - Confirm `ConfirmSupplierInvoiceHandler` as the write entry point
   - Define exact state machine transitions for Writer phase
   - Specify reconciliation algorithm for `ERP_RESULT_UNKNOWN`
   - Confirm `ref_supplier` as primary deduplication key

3. The proposal should reference this exploration document and the existing architecture files:
   - `core/hermes/context.py` (CompanyContext)
   - `core/hermes/invoices/ingestion.py` (DocumentIngestionService, state machine)
   - `core/hermes/audit.py` (DocumentIdempotencyManager)
   - `core/hermes/commands/handlers/supplier_invoice.py` (existing handler to extend)
   - `core/integrations/dolibarr/client.py` (DolibarrClient)
   - `core/hermes/invoices/models.py` (SupplierInvoiceDraft, DocumentState)