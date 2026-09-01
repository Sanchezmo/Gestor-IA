# Proposal: Supplier Invoice ERP Writer Phase 1

## Intent

Implement a safe, compliant Supplier Invoice Writer that executes the `CONFIRMING → SUPPLIER_CREATED → INVOICE_CREATED → ATTACHMENT_PENDING → COMPLETED` state machine transitions using **user-scoped Dolibarr API keys**, **post-write verification**, and **AI Act-compliant architecture** — stopping at `READY_FOR_CONTROLLED_ERP_WRITE=YES` (no actual ERP writes in this phase).

## Scope

### In Scope
- **ConfirmSupplierInvoiceHandler** (Command Layer V3) — full durable state machine implementation
- **Post-write verification** — read-back-and-compare after each Dolibarr CREATE (ref_supplier, totals, lines, supplier)
- **ERP_RESULT_UNKNOWN handling** — reconciliation via `DuplicateCheckDetail` + `ReconciliationDetail` before any retry
- **Attachment upload with retry** — idempotent by document_hash, no invoice recreation
- **AI Act compliance architecture** — feature registry, AIUsePolicy, traceability, human oversight, transparency, data minimisation, retention, HR firewall, runtime versioning
- **VPS-ready deployment configuration** — no hardcoded localhost, all endpoints config-driven
- **Remote private AI server support** — separate config for future cloud tasks

### Out of Scope
- Actual Dolibarr DEVELOPMENT writes (mock/fake tests only — GATE_A)
- HR/employment features (firewall only)
- Cloud AI routing/fallback (LOCAL_ONLY enforced)
- Production Dolibarr smoke tests (read-only only — GATE_C)
- Invoice extraction/validation changes (Reader phase complete)

## Capabilities

### New Capabilities
- `confirm-supplier-invoice`: Command handler executing CONFIRMING→SUPPLIER_CREATED→INVOICE_CREATED→ATTACHMENT_PENDING→COMPLETED with user-scoped keys
- `post-write-verification`: Read-back-and-compare logic for supplier invoices and attachments
- `erp-reconciliation`: ERP_RESULT_UNKNOWN handling with DuplicateCheckDetail/ReconciliationDetail
- `ai-act-compliance`: Feature-level AIUsePolicy registry, traceability logging, human oversight boundary, runtime versioning
- `vps-deployment-config`: InstanceConfig templates without localhost defaults, remote AI server separation

### Modified Capabilities
- `company-context`: Add `create_dolibarr_client_for_user(identity)` enforcing FAIL CLOSED on missing user API key
- `document-idempotency`: Extend with `mark_supplier_created()`, `mark_invoice_created()`, `mark_attachment_pending()`, `mark_erp_result_unknown()`
- `dolibarr-client`: Ensure user-scoped usage throughout, no admin fallback

## Approach

**Extend existing Command Layer (CreateSupplierInvoiceHandler) with ConfirmSupplierInvoiceHandler — NOT create parallel architecture.**

Key implementation:
1. **ConfirmSupplierInvoiceHandler** in `core/hermes/commands/handlers/supplier_invoice.py` using `company_context.create_dolibarr_client_for_user(identity)`
2. **State machine milestones** via `DocumentIdempotencyManager`: `mark_supplier_created()` → `mark_invoice_created()` → `mark_attachment_pending()` → `mark_completed()`
3. **Post-write verification**: `client.get_supplier_invoice(invoice_id)` → compare `ref_supplier`, `total`, `lines`, `supplier_id`
4. **ERP_RESULT_UNKNOWN**: On timeout → `mark_erp_result_unknown()` → reconciliation query → only then retry
5. **Attachment retry**: Separate `upload_document()` call, idempotent by document_hash, no invoice recreation
6. **AI Act**: Add `AIPolicyScope.LOCAL_ONLY` for `invoice_processing`, `extraction`, `validation`; log model, confidence, inference_count
7. **Config**: Remove localhost defaults from InstanceConfig template; add `task_policies` with explicit LOCAL_ONLY classification

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `core/hermes/commands/handlers/supplier_invoice.py` | New | ConfirmSupplierInvoiceHandler implementation |
| `core/hermes/audit.py` | Modified | DocumentIdempotencyManager milestone methods |
| `core/hermes/context.py` | Modified | create_dolibarr_client_for_user() enforcement |
| `core/hermes/invoices/models.py` | Modified | AIUsePolicy, ERP_RESULT_UNKNOWN state, traceability fields |
| `core/hermes/invoices/ingestion.py` | Modified | CONFIRMING state entry point |
| `config/instances/template.yaml` | Modified | Remove localhost, add remote_ai, task_policies |
| `core/integrations/dolibarr/client.py` | Modified | User-scoped usage verification |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| State machine transition bugs causing stuck documents | Medium | Comprehensive unit tests for each transition; idempotency at every milestone |
| ERP_RESULT_UNKNOWN race condition (Dolibarr created but timeout) | High | Reconciliation via commercial key before ANY retry; DuplicateCheckDetail mandatory |
| User API key rotation mid-flow | Low | FAIL CLOSED is correct; clear error surfacing for re-authentication |
| Large attachment upload timeouts | Medium | Chunked upload support; async retry with exponential backoff |
| Dolibarr version API differences | Medium | Version-aware mappers; integration test matrix |
| Multi-VAT tax structure rejection by Dolibarr | Medium | Mapper compatibility layer; test with multi-VAT invoices |

## Rollback Plan

1. **Feature flag**: `ENABLE_WRITER_PHASE1=false` disables ConfirmSupplierInvoiceHandler entirely
2. **Database**: No schema changes in Phase 1 (uses existing `gestor_ia_audit` table)
3. **Config**: New config fields are additive; old templates still parse
4. **Code**: Handler is new file; existing CreateSupplierInvoiceHandler untouched

## Dependencies

- Existing `DocumentIdempotencyManager` (MariaDB audit table)
- Existing `CompanyContext` with user-scoped client factory
- Existing `DolibarrClient` with REST API + DB fallback
- Redis for transient state (unchanged)

## Success Criteria

- [ ] **GATE_A**: `ConfirmSupplierInvoiceHandler` passes all mock/fake tests (no real Dolibarr)
- [ ] **GATE_B**: Audit DB verification — every milestone persists correct state in `gestor_ia_audit`
- [ ] **GATE_C**: Read-only Dolibarr smoke tests pass (no writes)
- [ ] **AI Act**: Feature registry classifies `invoice_processing`, `extraction`, `validation` as `LOCAL_ONLY` with traceability logs
- [ ] **Config**: InstanceConfig template has zero localhost/127.0.0.1 defaults; remote AI config present
- [ ] **Human oversight**: Explicit confirmation boundary at `PENDING_CONFIRMATION` → `CONFIRMING` transition
- [ ] **READY_FOR_CONTROLLED_ERP_WRITE=YES**: All acceptance criteria met, ready for controlled ERP write phase