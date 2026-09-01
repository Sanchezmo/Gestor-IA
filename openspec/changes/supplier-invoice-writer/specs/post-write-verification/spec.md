# Post-Write Verification Specification

## Purpose

Mandatory read-back-and-compare verification after every Dolibarr CREATE operation to ensure data integrity before marking state transitions complete.

## Requirements

### Requirement: Invoice Read-Back Verification

The system MUST read back every created supplier invoice via GET /supplierinvoices/{id} and compare against intended data.

Comparison fields:
- supplier_id (socid) matches resolved supplier
- ref_supplier matches original supplier invoice number
- date matches invoice date
- total_ht (base amount) matches calculated base
- VAT breakdown per rate matches line VAT calculations
- withholding_tax amount matches calculated withholding
- total_ttc (grand total) matches calculated total
- lines[] count, descriptions, quantities, unit prices, vat_rates, discounts

#### Scenario: Verification passes — exact match

- GIVEN invoice created with invoice_id=456
- WHEN get_supplier_invoice(456) called
- AND all comparison fields match intended values
- THEN verification passes
- AND state may transition to next milestone

#### Scenario: Verification fails — supplier mismatch

- GIVEN invoice created with invoice_id=456
- WHEN get_supplier_invoice(456) returns socid=999 (expected 123)
- THEN verification fails
- AND transition to FAILED_FINAL
- AND flag for manual review
- AND log discrepancy details

#### Scenario: Verification fails — ref_supplier mismatch

- GIVEN invoice created with ref_supplier="INV-2024-001"
- WHEN get_supplier_invoice returns ref_supplier="INV-2024-002"
- THEN verification fails
- AND transition to FAILED_FINAL
- AND flag for manual review

#### Scenario: Verification fails — VAT breakdown mismatch

- GIVEN invoice lines with VAT: line1=21%, line2=10%
- WHEN get_supplier_invoice returns different VAT amounts
- THEN verification fails
- AND transition to FAILED_FINAL
- AND log expected vs actual VAT per rate

#### Scenario: Verification fails — total mismatch

- GIVEN calculated total_ttc = 1210.00
- WHEN get_supplier_invoice returns total_ttc = 1200.00
- THEN verification fails
- AND transition to FAILED_FINAL
- AND NO COMPLETED state reached

### Requirement: Attachment Read-Back Verification

The system MUST verify attachment upload by checking document exists in Dolibarr.

#### Scenario: Attachment verification passes

- GIVEN attachment uploaded with document_hash="abc123"
- WHEN Dolibarr document list queried for invoice
- AND document with matching hash found
- THEN verification passes
- AND state transitions to COMPLETED

#### Scenario: Attachment verification fails

- GIVEN attachment upload returned success
- WHEN document list queried
- AND no document with matching hash found
- THEN state remains ATTACHMENT_PENDING
- AND schedule attachment retry
- AND NO transition to COMPLETED

### Requirement: Safe State on Verification Failure

The system MUST enter a safe, reviewable state on any verification failure — NEVER mark COMPLETED.

#### Scenario: Verification failure enters safe state

- GIVEN post-write verification fails for any reason
- WHEN handling failure
- THEN state becomes FAILED_FINAL (not FAILED_RETRYABLE)
- AND all persisted ERP IDs (supplier_id, invoice_id) retained in audit
- AND detailed discrepancy report logged
- AND alert raised for manual intervention

### Requirement: Decimal Precision in Verification

The system MUST compare monetary values using Decimal with configured precision, never float equality.

#### Scenario: Decimal comparison avoids float errors

- GIVEN expected total = Decimal("1210.00")
- AND actual total from Dolibarr = Decimal("1210.00")
- WHEN comparing with 2 decimal places
- THEN comparison passes

#### Scenario: Float comparison would fail but Decimal passes

- GIVEN expected = 0.1 + 0.2 (float = 0.30000000000000004)
- AND actual = 0.3
- WHEN using Decimal("0.1") + Decimal("0.2") vs Decimal("0.3")
- THEN comparison passes with Decimal
- AND would fail with float equality