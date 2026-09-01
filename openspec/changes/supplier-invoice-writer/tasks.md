# Tasks: Supplier Invoice ERP Writer Phase 1

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 800-1200 |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | PR 1: Core Writer (T1-T7) → PR 2: AI Act Compliance (T8-T16) → PR 3: VPS Config + Tests + Docs (T17-T23) |
| Delivery strategy | ask-on-risk |
| Chain strategy | feature-branch-chain |

Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: feature-branch-chain
400-line budget risk: High

### Suggested Work Units

| Unit | Goal | Likely PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|-----------|----------------------|-----------------|-------------------|
| 1 | Core Writer: ConfirmSupplierInvoiceHandler + state machine + Dolibarr integration + verification + reconciliation | PR 1 | `pytest tests/unit/commands/test_confirm_handler.py -v` | `make test-integration-writer` (mock Dolibarr) | All files in `core/hermes/commands/handlers/`, `core/hermes/audit.py`, `core/integrations/dolibarr/` |
| 2 | AI Act Compliance: Registry, policies, traceability, oversight, retention, transparency, HR firewall, versioning | PR 2 | `pytest tests/unit/ai/test_compliance.py -v` | `make test-integration-ai` | All files in `core/ai/` |
| 3 | VPS Config + GATE_A Tests + Documentation | PR 3 | `pytest tests/gate_a/ -v` | `make test` | `config/instances/template.yaml`, `tests/`, `docs/compliance/` |

## Phase 1: Foundation — Core Writer Infrastructure

- [ ] 1.1 Create `core/integrations/dolibarr/mappers.py` with Draft→Dolibarr payload mappers for supplier invoice, lines, withholding (limitation documented per design)
- [ ] 1.2 Create `core/integrations/dolibarr/verification.py` with `verify_supplier_invoice()` using Decimal precision comparison per spec
- [ ] 1.3 Create `core/integrations/dolibarr/reconciliation.py` with `DolibarrReconciliationService`, `ReconciliationAction`, `DuplicateCheckDetail`, `ReconciliationDetail`
- [ ] 1.4 Modify `core/integrations/dolibarr/client.py` — remove admin fallback, enforce user-scoped Authorization header, add `get_supplier_invoice()`, `search_supplier_invoices()`, `search_thirdparties()`
- [ ] 1.5 Modify `core/hermes/context.py` — implement `create_dolibarr_client_for_user(identity)` with FAIL CLOSED semantics, `UserAPIKeyMissingError`, `InvalidUserAPIKeyError`
- [ ] 1.6 Modify `core/hermes/audit.py` — add `mark_supplier_created()`, `mark_invoice_created()`, `mark_attachment_pending()`, `mark_erp_result_unknown()`, extend `get_state()` return per spec
- [ ] 1.7 Modify `core/hermes/invoices/models.py` — add `AIUsePolicy` enum, `ERP_RESULT_UNKNOWN` state, `AITraceEntry`, `HumanOversightRecord`, `RuntimeVersion`
- [ ] 1.8 Modify `core/hermes/commands/pending_store.py` — atomic confirm validates user/instance/TTL per spec
- [ ] 1.9 Modify `core/hermes/audit_logger.py` — log ERP actions with correlation_id

## Phase 2: Core Implementation — ConfirmSupplierInvoiceHandler

- [ ] 2.1 Create `core/hermes/commands/handlers/supplier_invoice.py` — `ConfirmSupplierInvoiceHandler` skeleton extending `CommandHandler`, entry point from `ingestion.py`
- [ ] 2.2 Implement confirmation boundary revalidation (Redis TTL, instance_id, user_id, API key, duplicate check) per spec scenarios
- [ ] 2.3 Implement state machine transitions: CONFIRMING → SUPPLIER_CREATED → INVOICE_CREATED → ATTACHMENT_PENDING → COMPLETED with invalid transition rejection
- [ ] 2.4 Implement supplier resolution actions (4 outcomes): FOUND_SUPPLIER, FOUND_NOT_SUPPLIER (enable), NOT_FOUND (create), AMBIGUOUS (block)
- [ ] 2.5 Implement invoice creation via Dolibarr REST with full field mapping, Decimal monetary values
- [ ] 2.6 Implement post-write verification: `get_supplier_invoice()` → compare all fields → `VerificationResult` per spec
- [ ] 2.7 Implement attachment upload with exponential backoff retry, idempotent by `document_hash`
- [ ] 2.8 Implement ERP_RESULT_UNKNOWN handling: timeout → `mark_erp_result_unknown()` → reconciliation → ADOPTED/RETRY_SCHEDULED/BLOCKED_MANUAL/ERROR
- [ ] 2.9 Implement crash recovery paths: resume from SUPPLIER_CREATED, INVOICE_CREATED, ATTACHMENT_PENDING, ERP_RESULT_UNKNOWN, COMPLETED, Redis loss

## Phase 3: AI Act Compliance Architecture

- [ ] 3.1 Create `core/ai/registry.py` — `AIFeatureRegistry`, `AIRegistryEntry`, `AIUsePolicy` enum, policy resolver with `is_allowed()` per spec
- [ ] 3.2 Create `core/ai/traceability.py` — `AITraceabilityLogger` with `operation_id` correlation across extraction→preview→confirmation→ERP
- [ ] 3.3 Create `core/ai/oversight.py` — `HumanOversightRecorder` at confirmation boundary with `confirmed_by`, `preview_hash`, `feature_chain`
- [ ] 3.4 Create `core/ai/retention.py` — `RetentionConfig` per-instance, per-category TTL with purge job
- [ ] 3.5 Create `core/ai/transparency.py` — `TransparencyNotice` mechanism with configurable delivery (inline/modal/toast)
- [ ] 3.6 Create `core/ai/hr_firewall.py` — `RegulatoryReviewGate` blocking `REGULATORY_REVIEW_REQUIRED` features
- [ ] 3.7 Create `core/ai/versioning.py` — `RuntimeVersioning` capturing versions at operation start
- [ ] 3.8 Wire AI registry into `ConfirmSupplierInvoiceHandler` — policy enforcement, trace logging, oversight check at confirmation

## Phase 4: VPS-Ready Configuration

- [ ] 4.1 Modify `config/instances/template.yaml` — remove all localhost/127.0.0.1/0.0.0.0 defaults, add `ollama_base_url`, `dolibarr_internal_url`, `redis_*`, `mariadb_*`, `task_policies`, `cloud_ai_*`
- [ ] 4.2 Audit codebase for hardcoded localhost in runtime config resolution paths — fix any found (FAIL CLOSED on localhost in production)
- [ ] 4.3 Add config validation that rejects localhost/127.0.0.1 in production instance configs
- [ ] 4.4 Verify `create_dolibarr_client_for_user()` uses `dolibarr_internal_url` from config only (no env var fallback)
- [ ] 4.5 Verify Redis and MariaDB connections use instance config only

## Phase 5: Testing — GATE_A Mandatory

- [ ] 5.1 Create mock `DolibarrClient` fake for unit tests (in-memory state, simulates all endpoints)
- [ ] 5.2 Write unit tests for `ConfirmSupplierInvoiceHandler` state transitions (13 states, valid/invalid transitions)
- [ ] 5.3 Write parameterized tests for supplier resolution (4 outcomes) per spec scenarios
- [ ] 5.4 Write unit tests for post-write verification comparison (match/mismatch supplier, ref, VAT, totals) per spec
- [ ] 5.5 Write unit tests for ERP reconciliation outcomes (UNIQUE_MATCH, NO_MATCH, AMBIGUOUS, ERROR) per spec
- [ ] 5.6 Write unit tests for AI registry policy enforcement (each `AIUsePolicy` × execution_location)
- [ ] 5.7 Write unit tests for config validation rejecting localhost in production
- [ ] 5.8 Write integration tests: full happy path (mock Dolibarr) → assert COMPLETED state
- [ ] 5.9 Write integration tests: ERP_RESULT_UNKNOWN recovery (timeout → reconciliation → adopt/retry/block)
- [ ] 5.10 Write integration tests: crash recovery from each milestone state
- [ ] 5.11 Write integration tests: attachment retry without invoice recreation
- [ ] 5.12 Write E2E test: Telegram confirmation → COMPLETED (test containers: MariaDB, Redis, mock Dolibarr)
- [ ] 5.13 Write E2E test: AI trace correlation (operation_id flows through extraction→preview→confirmation→ERP)
- [ ] 5.14 Write AI compliance behavior tests (10 cases from spec: registry, policy, traceability, minimisation, retention, oversight, transparency, firewall, versioning)

## Phase 6: Documentation

- [ ] 6.1 Create `docs/compliance/ai-act.md` — AI Act compliance architecture, feature registry, policies, traceability, human oversight, transparency, data minimisation, retention, HR firewall, runtime versioning
- [ ] 6.2 Update `docs/architecture/command-layer.md` — document `ConfirmSupplierInvoiceHandler` state machine and integration
- [ ] 6.3 Update `docs/deployment/vps-config.md` — document VPS-ready config template, no localhost defaults, remote AI config

## Acceptance Criteria (GATE_A)

All tasks must satisfy:
1. **ConfirmSupplierInvoiceHandler** passes all mock/fake tests (no real Dolibarr)
2. **Audit DB verification** — every milestone persists correct state in `gestor_ia_audit`
3. **AI Act compliance** — feature registry classifies `invoice_processing`, `extraction`, `validation` as `LOCAL_ONLY` with traceability logs
4. **Config validation** — InstanceConfig template has zero localhost defaults; remote AI config present
5. **Human oversight** — explicit confirmation boundary at `PENDING_CONFIRMATION` → `CONFIRMING`
6. **READY_FOR_CONTROLLED_ERP_WRITE=YES** — all criteria met

## Dependencies

```
Phase 1 (1.1-1.9) → Phase 2 (2.1-2.9)
Phase 1.5 → Phase 2.2 (user-scoped client factory)
Phase 1.6 → Phase 2.3, 2.4, 2.5, 2.6, 2.7, 2.8 (idempotency milestones)
Phase 1.1, 1.2, 1.3 → Phase 2.4, 2.5, 2.6, 2.7, 2.8 (Dolibarr modules)
Phase 3 (3.1-3.8) → Phase 2.9 (AI wiring in handler)
Phase 4 (4.1-4.5) → All phases (config foundation)
Phase 1-4 → Phase 5 (tests require implementation)
Phase 3, 4 → Phase 6 (docs reflect implementation)
```

## Next Step
Ready for implementation (sdd-apply). **Decision required**: Confirm feature-branch-chain strategy for 3 PRs before starting apply phase.