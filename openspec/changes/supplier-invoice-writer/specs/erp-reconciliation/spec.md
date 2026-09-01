# ERP Reconciliation Specification

## Purpose

Handle ERP_RESULT_UNKNOWN state when Dolibarr POST times out or network fails before response — reconcile by searching Dolibarr before any retry to prevent duplicate invoices.

## Requirements

### Requirement: ERP_RESULT_UNKNOWN Trigger

The system MUST enter ERP_RESULT_UNKNOWN on POST timeout or network error before receiving HTTP response.

#### Scenario: POST timeout triggers ERP_RESULT_UNKNOWN

- GIVEN Dolibarr POST /supplierinvoices sent
- WHEN no response received within timeout (default 30s)
- THEN mark_erp_result_unknown(durable_id, "invoice_create", context)
- AND state transitions to ERP_RESULT_UNKNOWN
- AND NO retry attempted yet

#### Scenario: Network error before response triggers ERP_RESULT_UNKNOWN

- GIVEN Dolibarr POST sent
- WHEN connection reset / DNS failure / TLS error before response
- THEN mark_erp_result_unknown(durable_id, "invoice_create", context)
- AND state transitions to ERP_RESULT_UNKNOWN

### Requirement: NO Blind Retry on ERP_RESULT_UNKNOWN

The system MUST NOT blindly retry the POST when in ERP_RESULT_UNKNOWN state.

#### Scenario: Blind retry blocked

- GIVEN state is ERP_RESULT_UNKNOWN
- WHEN retry logic triggered
- THEN reconciliation MUST execute first
- AND only reconciliation outcome determines next action

### Requirement: Reconciliation via Commercial Key Search

The system MUST search Dolibarr by commercial idempotency key: (instance_id, supplier_tax_id, supplier_invoice_number).

#### Scenario: Reconciliation query execution

- GIVEN state ERP_RESULT_UNKNOWN for invoice creation
- WHEN reconcile_with_dolibarr() called
- THEN search Dolibarr supplierinvoices by ref_supplier + supplier tax_id
- AND return DuplicateCheckDetail with match count and details

### Requirement: Reconciliation Outcomes

The system MUST handle four reconciliation outcomes deterministically.

#### Scenario: UNIQUE_MATCH — adopt existing invoice

- GIVEN reconciliation finds exactly ONE invoice matching (supplier_tax_id, supplier_invoice_number)
- WHEN processing outcome
- THEN adopt existing invoice_id
- AND mark_invoice_created(durable_id, existing_invoice_id)
- AND transition to INVOICE_CREATED
- AND proceed to attachment

#### Scenario: NO_MATCH — controlled retry evaluation

- GIVEN reconciliation finds ZERO matching invoices
- WHEN processing outcome
- THEN evaluate retry safety:
  - idempotency key not used in Dolibarr
  - durable state confirms no prior success
  - retry_count < max_retries
- IF safe: transition to CONFIRMING for controlled retry
- IF unsafe: transition to FAILED_FINAL

#### Scenario: AMBIGUOUS — block and require manual resolution

- GIVEN reconciliation finds MULTIPLE matching invoices
- WHEN processing outcome
- THEN transition to FAILED_FINAL
- AND block all automatic retries
- AND log all matching invoice IDs for manual review
- AND alert user/admin

#### Scenario: RECONCILIATION_ERROR — block and require manual resolution

- GIVEN reconciliation query fails (Dolibarr unavailable, auth error)
- WHEN processing outcome
- THEN transition to FAILED_FINAL
- AND block all automatic retries
- AND log error details
- AND alert for manual intervention

### Requirement: Reconciliation Applies to Supplier Creation Too

The system MUST also reconcile supplier creation (ERP_RESULT_UNKNOWN on supplier POST).

#### Scenario: Supplier reconciliation UNIQUE_MATCH

- GIVEN ERP_RESULT_UNKNOWN on supplier creation
- WHEN reconcile searches thirdparties by tax_id
- AND exactly one match found
- THEN adopt existing supplier_id
- AND mark_supplier_created(durable_id, existing_supplier_id)
- AND transition to SUPPLIER_CREATED

#### Scenario: Supplier reconciliation AMBIGUOUS

- GIVEN ERP_RESULT_UNKNOWN on supplier creation
- WHEN reconcile finds multiple thirdparties with same tax_id
- THEN transition to FAILED_FINAL
- AND block write
- AND require manual resolution

### Requirement: DuplicateCheckDetail and ReconciliationDetail Structures

The system MUST use structured detail objects for audit trail.

DuplicateCheckDetail:
- match_count: int
- matches: list of {id, ref_supplier, supplier_tax_id, status}
- searched_key: {instance_id, supplier_tax_id, supplier_invoice_number}

ReconciliationDetail:
- action_taken: "ADOPTED" | "RETRY_SCHEDULED" | "BLOCKED_MANUAL" | "ERROR"
- adopted_id: int (if ADOPTED)
- match_count: int
- timestamp: ISO8601