# AI Act Compliance Specification

## Purpose

Implement AI Act-compliant architecture for invoice processing features: feature registry, use policies, traceability, human oversight, transparency, data minimisation, retention, HR firewall, and runtime versioning.

## Requirements

### Requirement: AI Feature Registry

The system MUST maintain an AI Feature Registry with one record per AI capability.

Registry fields (AIRegistryEntry):
- feature_id: str (e.g., "invoice_processing", "extraction", "validation")
- system_id: str (e.g., "gestor-ia")
- provider: str (e.g., "ollama", "local-llama")
- model: str (e.g., "llama3.1:8b", "qwen2.5:7b")
- purpose: str (e.g., "extract invoice fields from PDF", "validate extracted data")
- risk_level: str (MINIMAL, LIMITED, HIGH, UNACCEPTABLE)
- data_policy: str (data categories processed)
- human_oversight: bool
- transparency: bool (user-facing notice)
- enabled: bool
- version: str (feature config version)

#### Scenario: Registry contains invoice processing features

- GIVEN system starts
- WHEN AI registry loaded
- THEN features registered:
  - invoice_processing: risk=LIMITED, human_oversight=true, transparency=true
  - extraction: risk=LIMITED, human_oversight=true, transparency=true
  - validation: risk=LIMITED, human_oversight=true, transparency=true
- AND all have LOCAL_ONLY policy enforced

### Requirement: AIUsePolicy Enforcement

The system MUST enforce AIUsePolicy per feature at runtime.

Policy values:
- ALLOW: unrestricted
- DENY: blocked entirely
- REQUIRE_HUMAN_OVERSIGHT: human must approve output
- LOCAL_ONLY: only local inference allowed (no cloud)
- CLOUD_ALLOWED: cloud inference permitted
- REGULATORY_REVIEW_REQUIRED: legal/compliance review before use

#### Scenario: Invoice features enforce LOCAL_ONLY + REQUIRE_HUMAN_OVERSIGHT

- GIVEN feature_id="invoice_processing"
- WHEN policy checked
- THEN effective policy = LOCAL_ONLY + REQUIRE_HUMAN_OVERSIGHT
- AND any cloud inference attempt blocked
- AND output requires human confirmation before use

#### Scenario: HR feature blocked by REGULATORY_REVIEW_REQUIRED

- GIVEN feature_id="hr_screening" (hypothetical)
- WHEN policy checked
- THEN effective policy = REGULATORY_REVIEW_REQUIRED
- AND all inference blocked until review complete
- AND firewall prevents accidental enablement

### Requirement: AI Traceability Logging

The system MUST log every AI inference with full traceability — correlated to preview, confirmation, and ERP write.

Trace record (AITraceEntry):
- operation_id: UUID (correlates preview→confirmation→ERP write)
- instance_id: str
- feature_id: str
- provider: str
- model: str
- policy: AIUsePolicy applied
- execution_location: "local" | "cloud"
- success: bool
- latency_ms: int
- confidence: float (if applicable)
- inference_count: int
- timestamp: ISO8601
- correlated_preview_hash: str (hash of preview shown to user)
- correlated_confirmation_id: str (Telegram callback ID)
- correlated_erp_write_id: str (durable operation ID)

#### Scenario: Trace logged for invoice extraction

- GIVEN user uploads PDF
- WHEN extraction runs
- THEN AITraceEntry created with:
  - feature_id="extraction"
  - provider="ollama"
  - model="llama3.1:8b"
  - execution_location="local"
  - confidence=0.92
  - correlated_preview_hash="sha256:abc..."
- AND trace stored in audit DB

#### Scenario: Trace logged for validation

- GIVEN extraction complete, user reviews preview
- WHEN validation runs
- THEN AITraceEntry created with:
  - feature_id="validation"
  - correlated_preview_hash=same_as_extraction
  - correlated_confirmation_id=callback_id

#### Scenario: Trace correlated to ERP write

- GIVEN user confirms
- WHEN ERP write executes
- THEN AITraceEntry for processing includes correlated_erp_write_id
- AND full chain traceable: preview → confirmation → ERP result

### Requirement: Data Minimisation in AI Traces

The system MUST NOT store sensitive data in AI trace logs.

Prohibited in traces:
- PDF content or binary
- OCR text output
- Prompts sent to model
- API keys or credentials
- Full invoice data (only hash/reference allowed)

Allowed in traces:
- Feature ID, provider, model
- Confidence scores
- Latency, success/failure
- Hashes for correlation (preview_hash, not content)

#### Scenario: Trace contains no prohibited data

- GIVEN AITraceEntry created
- WHEN inspecting trace content
- THEN no PDF, OCR, prompts, keys, or invoice PII present
- AND only metadata and correlation hashes present

### Requirement: Configurable Retention

The system MUST support configurable retention periods for AI traces by instance and data category.

Retention config per instance:
- ai_trace_retention_days: int (default 90)
- ai_trace_retention_by_category: dict (e.g., {"invoice": 365, "hr": 2555})

#### Scenario: Retention applied per category

- GIVEN instance config has ai_trace_retention_by_category={"invoice": 365}
- WHEN trace for feature_id="invoice_processing" ages
- THEN retention = 365 days
- AND purge job respects category-specific retention

### Requirement: Human Oversight Boundary

The system MUST enforce explicit human confirmation at PENDING_CONFIRMATION → CONFIRMING.

Oversight record:
- confirmed_by: user_id (Telegram)
- confirmed_at: ISO8601
- preview_hash: str (hash of preview shown)
- feature_chain: list of feature_ids used (extraction → validation → processing)

#### Scenario: Human oversight recorded at confirmation

- GIVEN user views preview (hash="sha256:xyz")
- WHEN user clicks "Confirm" callback
- THEN oversight record created with:
  - confirmed_by=user_id
  - preview_hash="sha256:xyz"
  - feature_chain=["extraction", "validation", "invoice_processing"]
- AND oversight record linked to durable operation

#### Scenario: No ERP write without oversight record

- GIVEN durable operation in CONFIRMING
- WHEN checking preconditions
- THEN oversight record MUST exist
- IF missing: transition to FAILED_FINAL

### Requirement: Transparency Notice Mechanism

The system MUST support configurable user-facing transparency notices when AI is used.

Notice config:
- enabled: bool
- message_template: str (with {feature_id}, {model} placeholders)
- delivery: "inline" | "modal" | "toast"

#### Scenario: Transparency notice shown on preview

- GIVEN transparency enabled for instance
- WHEN preview generated
- THEN notice displayed: "This preview was generated using {model} via {feature_id}. Please review carefully before confirming."
- AND notice logged in trace

### Requirement: HR Firewall

The system MUST gate any HR/employment AI feature behind REGULATORY_REVIEW_REQUIRED.

#### Scenario: HR feature blocked by firewall

- GIVEN feature_id="hr_cv_screening"
- WHEN policy evaluated
- THEN policy = REGULATORY_REVIEW_REQUIRED
- AND inference blocked at registry level
- AND audit log entry: "HR firewall blocked hr_cv_screening — regulatory review required"

### Requirement: Runtime Versioning

The system MUST expose version identifiers for all runtime components.

Version record (RuntimeVersion):
- gestor_ia_version: str (e.g., "1.4.0")
- workflow_version: str (e.g., "supplier-invoice-writer-v1")
- ai_config_version: str (hash of AI registry config)
- provider_version: str (Ollama version)
- model_version: str (model tag/hash)
- dolibarr_client_version: str
- config_schema_version: str

#### Scenario: Version snapshot at operation start

- GIVEN ConfirmSupplierInvoiceHandler starts
- WHEN operation begins
- THEN RuntimeVersion captured and stored in audit
- AND all versions traceable for reproducibility