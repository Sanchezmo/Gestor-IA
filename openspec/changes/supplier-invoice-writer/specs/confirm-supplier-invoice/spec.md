# Confirm Supplier Invoice Specification

## Purpose

Execute the durable state machine for supplier invoice ERP writes from user confirmation through completion, using user-scoped Dolibarr API keys and enforcing human oversight at the confirmation boundary.

## Requirements

### Requirement: Confirmation Boundary Revalidation

The system MUST revalidate all preconditions at the PENDING_CONFIRMATION → CONFIRMING transition before any ERP write.

The system SHALL verify:
- Pending command exists in Redis with valid TTL
- instance_id matches the confirmed instance
- Telegram user_id matches the confirming user
- Dolibarr mapping is current for the instance
- User API key is available (FAIL CLOSED if missing)
- Durable operation is in valid state (not CANCELLED, EXPIRED, COMPLETED, FAILED_FINAL)
- Duplicate check passes against both durable state AND Dolibarr (supplier_tax_id + supplier_invoice_number)

#### Scenario: Valid confirmation boundary

- GIVEN a durable operation in PENDING_CONFIRMATION state
- AND pending command exists with matching instance_id and user_id
- AND user API key is configured for the instance
- AND no duplicate exists in durable state or Dolibarr
- WHEN user confirms via Telegram callback
- THEN transition to CONFIRMING state
- AND persist state change to audit log

#### Scenario: Expired pending command

- GIVEN a durable operation in PENDING_CONFIRMATION state
- AND pending command TTL has expired
- WHEN user attempts confirmation
- THEN transition to EXPIRED state
- AND reject confirmation with "command expired" error

#### Scenario: User API key missing

- GIVEN a durable operation in PENDING_CONFIRMATION state
- AND user has no Dolibarr API key configured for the instance
- WHEN user attempts confirmation
- THEN transition to FAILED_FINAL state
- AND reject with "user API key required" error

#### Scenario: Duplicate detected at confirmation

- GIVEN a durable operation in PENDING_CONFIRMATION state
- AND duplicate check finds existing invoice in Dolibarr with same (supplier_tax_id, supplier_invoice_number)
- WHEN user attempts confirmation
- THEN transition to FAILED_FINAL state
- AND reject with "duplicate invoice" error

### Requirement: Durable State Machine — Core Transitions

The system MUST implement a 13-state durable state machine with valid transitions only.

Valid transitions:
```
RECEIVED → PROCESSING → REVIEW → PENDING_CONFIRMATION → CONFIRMING
CONFIRMING → SUPPLIER_CREATED
SUPPLIER_CREATED → INVOICE_CREATED
INVOICE_CREATED → ATTACHMENT_PENDING
ATTACHMENT_PENDING → COMPLETED
```

Terminal states: COMPLETED, FAILED_RETRYABLE, FAILED_FINAL, CANCELLED, EXPIRED, ERP_RESULT_UNKNOWN

Invalid transitions MUST be rejected with state transition error.

#### Scenario: Happy path through all states

- GIVEN a new supplier invoice in RECEIVED state
- WHEN processing completes successfully
- THEN state progresses: RECEIVED → PROCESSING → REVIEW → PENDING_CONFIRMATION
- WHEN user confirms
- THEN state progresses: CONFIRMING → SUPPLIER_CREATED → INVOICE_CREATED → ATTACHMENT_PENDING → COMPLETED

#### Scenario: Invalid transition rejected

- GIVEN a durable operation in SUPPLIER_CREATED state
- WHEN code attempts transition to RECEIVED
- THEN reject with "invalid state transition" error
- AND state remains SUPPLIER_CREATED

### Requirement: Durable State Machine — Error States

The system MUST transition to appropriate error states on failures.

#### Scenario: Supplier creation fails (retryable)

- GIVEN state CONFIRMING
- WHEN Dolibarr supplier creation returns 5xx or network timeout
- THEN transition to FAILED_RETRYABLE
- AND persist error details for retry

#### Scenario: Supplier creation fails (non-retryable)

- GIVEN state CONFIRMING
- WHEN Dolibarr returns 4xx validation error (invalid data)
- THEN transition to FAILED_FINAL
- AND persist error details
- AND NO automatic retry

#### Scenario: User cancellation

- GIVEN state PENDING_CONFIRMATION
- WHEN user cancels via Telegram
- THEN transition to CANCELLED
- AND NO ERP writes attempted

### Requirement: DocumentIdempotencyManager Milestones

The system MUST persist milestone markers at each state transition via DocumentIdempotencyManager.

Milestone methods:
- mark_supplier_created(durable_id, supplier_id)
- mark_invoice_created(durable_id, invoice_id)
- mark_attachment_pending(durable_id, attachment_id)
- mark_erp_result_unknown(durable_id, operation_type, context)
- mark_completed(durable_id)

#### Scenario: Milestone persistence on supplier creation

- GIVEN state CONFIRMING
- WHEN supplier created successfully in Dolibarr (supplier_id=123)
- THEN mark_supplier_created(durable_id, 123) called
- AND state transitions to SUPPLIER_CREATED
- AND audit record contains supplier_id

#### Scenario: Milestone persistence on invoice creation

- GIVEN state SUPPLIER_CREATED
- WHEN invoice created successfully in Dolibarr (invoice_id=456)
- THEN mark_invoice_created(durable_id, 456) called
- AND state transitions to INVOICE_CREATED
- AND audit record contains invoice_id

### Requirement: Supplier Resolution Actions

The system MUST resolve supplier before invoice creation with four outcomes.

#### Scenario: FOUND_SUPPLIER — existing supplier used

- GIVEN supplier lookup finds exactly one match by tax_id
- AND thirdparty is already a supplier (supplier=1)
- WHEN resolving supplier
- THEN use existing supplier_id
- AND proceed to invoice creation

#### Scenario: FOUND_NOT_SUPPLIER — enable as supplier

- GIVEN supplier lookup finds exactly one match by tax_id
- AND thirdparty exists but supplier=0
- WHEN resolving supplier
- THEN call Dolibarr thirdparty update to set supplier=1
- AND use updated supplier_id
- AND proceed to invoice creation

#### Scenario: NOT_FOUND — create new supplier

- GIVEN supplier lookup finds NO match by tax_id
- WHEN resolving supplier
- THEN create new thirdparty with ONLY validated data from invoice (name, tax_id, address, email)
- AND NO invented fields (no phone, no website, no default payment terms)
- AND set supplier=1
- AND proceed to invoice creation

#### Scenario: AMBIGUOUS — block write

- GIVEN supplier lookup finds MULTIPLE matches by tax_id
- WHEN resolving supplier
- THEN transition to FAILED_FINAL
- AND block ERP write
- AND require manual user resolution
- AND log ambiguous matches for review

### Requirement: Supplier Invoice Creation via Dolibarr REST

The system MUST create supplier invoices with correct API field mapping.

Required fields:
- socid (supplier_id from resolution)
- ref_supplier (supplier invoice number)
- date (invoice date)
- datep (payment date, optional)
- lines[]: product_id/description, qty, unit_price, vat_rate, discount_percent
- vat_rate per line
- global_discount_percent (header level)
- withholding_tax (retención) if present

All monetary values MUST use Decimal internally, NEVER float.

#### Scenario: Create invoice with full mapping

- GIVEN resolved supplier_id=123
- AND validated invoice data with lines, VAT, discount, withholding
- WHEN creating invoice via Dolibarr POST /supplierinvoices
- THEN request body contains all mapped fields correctly
- AND monetary values are Decimal strings
- AND response contains invoice_id

#### Scenario: Multi-VAT line handling

- GIVEN invoice lines with different VAT rates (21%, 10%, 4%)
- WHEN creating invoice
- THEN each line contains its specific vat_rate
- AND Dolibarr accepts multi-VAT structure

### Requirement: Crash Recovery Paths

The system MUST recover correctly from any intermediate state on restart.

#### Scenario: Recover from SUPPLIER_CREATED

- GIVEN durable operation in SUPPLIER_CREATED state on restart
- WHEN handler resumes
- THEN continue to invoice creation (do NOT recreate supplier)
- AND use persisted supplier_id from audit

#### Scenario: Recover from INVOICE_CREATED

- GIVEN durable operation in INVOICE_CREATED state on restart
- WHEN handler resumes
- THEN continue to attachment upload (do NOT recreate invoice)
- AND use persisted invoice_id from audit

#### Scenario: Recover from ATTACHMENT_PENDING

- GIVEN durable operation in ATTACHMENT_PENDING state on restart
- WHEN handler resumes
- THEN retry attachment upload only
- AND on success transition to COMPLETED

#### Scenario: Recover from ERP_RESULT_UNKNOWN

- GIVEN durable operation in ERP_RESULT_UNKNOWN state on restart
- WHEN handler resumes
- THEN execute reconciliation BEFORE any retry
- AND follow reconciliation outcome

#### Scenario: Recover from COMPLETED

- GIVEN durable operation in COMPLETED state on restart
- WHEN handler resumes
- THEN NO ERP writes attempted
- AND return success immediately

#### Scenario: Redis loss — durable state sufficient

- GIVEN Redis data lost but MariaDB audit intact
- WHEN handler starts for existing durable_id
- THEN reconstruct state from audit table
- AND resume from last persisted milestone