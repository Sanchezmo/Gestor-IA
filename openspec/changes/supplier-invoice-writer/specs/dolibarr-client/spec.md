# Dolibarr Client Delta Specification

## Purpose

Delta specification for enforcing user-scoped Dolibarr client usage throughout the writer phase — no admin fallback, no shared credentials.

## MODIFIED Requirements

### Requirement: User-Scoped Client Enforcement

All DolibarrClient instances used in writer phase MUST be created via CompanyContext.create_dolibarr_client_for_user() with user's API key.

(Previously: DolibarrClient could be instantiated directly with admin/shared credentials)

The system SHALL:
- Remove direct DolibarrClient instantiation in writer code paths
- Require CompanyContext factory for all writer operations
- Validate Authorization header contains user-specific key on each request

#### Scenario: Writer handler uses factory only

- GIVEN ConfirmSupplierInvoiceHandler executing
- WHEN supplier lookup needed
- THEN client = company_context.create_dolibarr_client_for_user(identity)
- AND client used for GET /thirdparties search
- AND NO direct DolibarrClient() construction

#### Scenario: Writer handler uses factory for invoice creation

- GIVEN state SUPPLIER_CREATED
- WHEN creating invoice
- THEN client = company_context.create_dolibarr_client_for_user(identity)
- AND POST /supplierinvoices uses user's Authorization header
- AND response handled with user's permissions context

#### Scenario: Writer handler uses factory for attachment

- GIVEN state INVOICE_CREATED
- WHEN uploading attachment
- THEN client = company_context.create_dolibarr_client_for_user(identity)
- AND POST /documents uses user's Authorization header

### Requirement: No Admin Fallback in Client

The DolibarrClient class MUST NOT contain fallback logic to admin/shared credentials.

(Previously: Client had fallback to instance admin key on 401)

#### Scenario: 401 on user key — error propagated, no fallback

- GIVEN client created with user API key
- WHEN request returns 401 Unauthorized
- THEN DolibarrClient raises DolibarrAuthError
- AND NO retry with admin key
- AND NO silent fallback
- AND error bubbles to handler for FAIL CLOSED

### Requirement: User-Scoped Search Operations

All search operations (thirdparty, supplier invoice, product) MUST use user-scoped client.

#### Scenario: Supplier lookup uses user-scoped client

- GIVEN needing to find supplier by tax_id
- WHEN search_thirdparties(tax_id) called
- THEN uses user-scoped client
- AND results reflect user's Dolibarr permissions
- AND user sees only what their key allows

#### Scenario: Duplicate check uses user-scoped client

- GIVEN checking for existing invoice by ref_supplier
- WHEN search_supplier_invoices(ref_supplier) called
- THEN uses user-scoped client
- AND results reflect user's Dolibarr permissions

### Requirement: Reconciliation Uses User-Scoped Client

The reconcile_with_dolibarr() function MUST use user-scoped client.

#### Scenario: Reconciliation respects user permissions

- GIVEN ERP_RESULT_UNKNOWN state
- WHEN reconcile_with_dolibarr() searches by commercial key
- THEN uses user-scoped client
- AND only finds invoices visible to that user's key
- AND reconciliation outcome based on user-visible data