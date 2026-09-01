# Document Idempotency Delta Specification

## Purpose

Delta specification for extending DocumentIdempotencyManager with writer-phase milestone methods.

## ADDED Requirements

### Requirement: mark_supplier_created(durable_id, supplier_id)

The system MUST persist supplier creation milestone with Dolibarr supplier ID.

(Previously: no supplier milestone — only document-level idempotency)

#### Scenario: Supplier milestone persisted

- GIVEN durable operation in CONFIRMING state
- WHEN supplier created in Dolibarr (supplier_id=789)
- THEN mark_supplier_created(durable_id, 789) called
- AND audit record updated with:
  - milestone: "supplier_created"
  - erp_supplier_id: 789
  - timestamp: ISO8601
- AND state transitions to SUPPLIER_CREATED

### Requirement: mark_invoice_created(durable_id, invoice_id)

The system MUST persist invoice creation milestone with Dolibarr invoice ID.

(Previously: mark_completed only — no intermediate invoice milestone)

#### Scenario: Invoice milestone persisted

- GIVEN durable operation in SUPPLIER_CREATED state
- WHEN invoice created in Dolibarr (invoice_id=456)
- THEN mark_invoice_created(durable_id, 456) called
- AND audit record updated with:
  - milestone: "invoice_created"
  - erp_invoice_id: 456
  - timestamp: ISO8601
- AND state transitions to INVOICE_CREATED

### Requirement: mark_attachment_pending(durable_id, attachment_id)

The system MUST persist attachment upload milestone.

#### Scenario: Attachment milestone persisted

- GIVEN durable operation in INVOICE_CREATED state
- WHEN attachment upload initiated (attachment_id="doc_123")
- THEN mark_attachment_pending(durable_id, "doc_123") called
- AND audit record updated with:
  - milestone: "attachment_pending"
  - erp_attachment_id: "doc_123"
  - timestamp: ISO8601
- AND state transitions to ATTACHMENT_PENDING

### Requirement: mark_erp_result_unknown(durable_id, operation_type, context)

The system MUST persist ERP_RESULT_UNKNOWN state with reconciliation context.

#### Scenario: ERP_RESULT_UNKNOWN persisted

- GIVEN Dolibarr POST times out
- WHEN mark_erp_result_unknown(durable_id, "invoice_create", context) called
- THEN audit record updated with:
  - milestone: "erp_result_unknown"
  - operation_type: "invoice_create"
  - context_json: {timeout_ms, attempt, commercial_key}
  - timestamp: ISO8601
- AND state transitions to ERP_RESULT_UNKNOWN

### Requirement: mark_completed(durable_id) — Extended

The system MUST persist final completion with all ERP IDs.

(Previously: mark_completed with minimal data)

#### Scenario: Completion with full ERP trace

- GIVEN durable operation in ATTACHMENT_PENDING
- WHEN attachment verified
- THEN mark_completed(durable_id) called
- AND audit record finalized with:
  - milestone: "completed"
  - erp_supplier_id: (from supplier_created)
  - erp_invoice_id: (from invoice_created)
  - erp_attachment_id: (from attachment_pending)
  - final_state: "COMPLETED"
  - timestamp: ISO8601

## MODIFIED Requirements

### Requirement: get_state(durable_id) — Extended Return

The system MUST return all milestone ERP IDs in state query.

(Previously: returned only document_hash and basic state)

#### Scenario: State query returns full milestone trace

- GIVEN durable operation with milestones persisted
- WHEN get_state(durable_id) called
- THEN return includes:
  - state: "INVOICE_CREATED"
  - erp_supplier_id: 789
  - erp_invoice_id: 456
  - erp_attachment_id: null
  - milestones: ["supplier_created", "invoice_created"]