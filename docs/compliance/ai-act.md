# AI Act Compliance Architecture

**Technical compliance architecture only. Legal classification must be reviewed when features, intended purposes, deployment contexts or applicable regulations change.**

---

## Overview

Gestor-IA implements **AI Compliance by Design** for its AI-powered features. This document describes the technical architecture that enables compliance with the EU AI Act (Regulation 2024/1689) for the features currently implemented.

> **Disclaimer**: This document describes technical controls. It does not constitute legal advice or certification of compliance. Final legal classification depends on feature scope, intended purpose, deployment context, provider/deployer role, and applicable regulatory guidance.

---

## Feature-Level Classification

Gestor-IA does **not** classify the entire product with a single risk level. Instead, each AI feature is registered and classified independently.

### Current AI Features

| Feature ID | System ID | Provider | Model | Purpose | Risk Class | Data Policy | Human Oversight |
|------------|-----------|----------|-------|---------|------------|-------------|-----------------|
| `supplier_invoice_extraction` | `gestor-ia-invoice-processing` | Ollama (local) | qwen3.5:4b-invoice | Extract structured accounting data from supplier invoices | Limited | LOCAL_ONLY | Required |

### Future Features (Gated)

| Feature ID | System ID | Risk Class | Data Policy | Status |
|------------|-----------|------------|-------------|--------|
| `candidate_ranking` | `gestor-ia-hr` | High | REGULATORY_REVIEW_REQUIRED | Disabled |
| `employee_evaluation` | `gestor-ia-hr` | High | REGULATORY_REVIEW_REQUIRED | Disabled |

HR-related features are **disabled by default** and require explicit regulatory review approval before activation.

---

## AI Feature Registry

The `AIFeatureRegistry` (`core/hermes/ai_registry.py`) maintains a registry of all AI features with:

```python
@dataclass(frozen=True)
class AIFeature:
    feature_id: str                    # Unique identifier
    system_id: str                     # Logical system grouping
    provider: str                      # "ollama", "nvidia", "openai", etc.
    model: str                         # Model identifier
    model_version: str | None          # Specific version/tag
    intended_purpose: str              # Human-readable purpose
    risk_classification: AIRiskClassification  # MINIMAL, LIMITED, HIGH, UNACCEPTABLE
    data_policy: AIUsePolicy           # LOCAL_ONLY, CLOUD_ALLOWED, etc.
    human_oversight_required: bool     # Whether human confirmation is mandatory
    transparency_required: bool        # Whether transparency notice is required
    enabled: bool                      # Feature flag
    effective_from: datetime           # Activation date
    effective_to: datetime | None      # Expiration date
    metadata: dict                     # Extensible metadata
```

### Policy Enforcement

The registry enforces policies at runtime:

- `LOCAL_ONLY` features **cannot** use cloud providers (NVIDIA, OpenAI, Anthropic)
- `REGULATORY_REVIEW_REQUIRED` features are **disabled** until explicitly approved
- `DENY` policy blocks the feature entirely
- Policy checks occur before every AI execution

---

## AI Use Policy

The `AIUsePolicy` enum defines allowed data handling and provider routing:

| Policy | Description | Invoice Extraction |
|--------|-------------|-------------------|
| `ALLOW` | Standard AI usage permitted | ✓ |
| `REQUIRE_HUMAN_OVERSIGHT` | Human must confirm AI output | ✓ |
| `LOCAL_ONLY` | Only private/local AI providers | ✓ |
| `CLOUD_ALLOWED` | Cloud providers permitted | ✗ |
| `REGULATORY_REVIEW_REQUIRED` | Feature gated by compliance review | HR features |
| `DENY` | AI completely blocked | N/A |

### Invoice Processing Policy

```python
task_policies: {
    "invoice_processing": "LOCAL_ONLY",
    "content_generation": "CLOUD_ALLOWED",
    "general_chat": "CLOUD_ALLOWED"
}
```

**Invoice processing is hardcoded to `LOCAL_ONLY`** — no cloud fallback is possible.

---

## AI Traceability

Every AI execution is logged with full correlation to business operations via `AITraceabilityLogger`:

```python
@dataclass(frozen=True)
class AITraceRecord:
    operation_id: str           # Correlates with document hash
    instance_id: str            # Multi-tenant isolation
    feature_id: str             # Which AI feature
    provider: str               # ollama, nvidia, openai
    model: str                  # Model name
    model_version: str | None   # Specific version
    policy: AIUsePolicy         # Applied policy
    local_or_cloud: str         # "local" or "cloud"
    timestamp: datetime         # Execution time
    success: bool               # Outcome
    latency_ms: int             # Performance
    fallback_used: bool         # Whether fallback occurred
    error_code: str | None      # Error classification
    # Correlation IDs (no sensitive data)
    correlation_id: str | None
    preview_id: str | None
    confirmation_id: str | None
    erp_write_id: str | None
```

### Correlation Chain

```
Document Ingestion
    → AI Extraction (operation_id = document_hash)
    → Preview Generation (preview_id)
    → Human Confirmation (confirmation_id, confirmed_by, preview_hash)
    → ERP Write (erp_write_id = invoice_id)
    → Audit Log (correlation_id throughout)
```

This enables full reconstruction of: what AI ran, what policy applied, who confirmed, what ERP action occurred.

---

## Data Minimisation

AI traces **never store** sensitive content by default. The `DataMinimisationFilter` strips forbidden fields:

```python
FORBIDDEN_FIELDS = frozenset([
    "pdf_content", "image_content", "ocr_text", "full_prompt",
    "bank_account", "api_key", "telegram_token", "dolibarr_key",
    "password", "secret", "credit_card", "iban", "swift_bic"
])
```

Only metadata is retained: hashes, operation IDs, model/provider info, policy, result codes.

---

## Retention Configuration

Retention is configurable per instance and data category:

```python
@dataclass(frozen=True)
class RetentionConfig:
    instance_id: str
    category: str      # "ai_trace", "document", "audit", etc.
    ttl_days: int
    description: str
```

- **AI traces**: Configurable TTL (default: instance-defined)
- **Documents**: Separate retention from AI traces
- **Audit logs**: Separate retention (critical actions never auto-deleted)
- **Durable idempotency**: Never expires (prevents duplicate ERP writes)

---

## Human Oversight

Human oversight is **mandatory** for invoice extraction. The `HumanOversightRecorder` captures:

```python
@dataclass(frozen=True)
class HumanOversightRecord:
    operation_id: str
    instance_id: str
    feature_id: str
    confirmed_by: str          # Telegram user ID
    confirmed_at: datetime
    preview_hash: str          # Hash of preview content confirmed
    preview_version: str       # Version of preview
    approval_action: str       # "confirmed", "corrected", "cancelled"
```

### Invariants

1. **AI Output ≠ Business Decision ≠ ERP Write**
2. Every ERP write requires explicit human confirmation
3. Confirmation records what was confirmed (preview hash)
4. No automated confirmation or auto-approval

---

## Transparency

Configurable transparency notices per feature and channel:

```python
@dataclass(frozen=True)
class TransparencyNotice:
    feature_id: str
    channel: str      # "telegram", "web", "api", "onboarding"
    message: str
    enabled: bool
```

### Current Implementation

- **Onboarding**: Notice that invoice processing uses local AI
- **First use**: Explicit notice before first extraction
- **Help command**: Lists AI-powered features
- **Per-channel**: Configurable per Telegram, Web, API

Hermes never pretends to be human. Notices are non-intrusive and context-appropriate.

---

## HR / High-Risk Firewall

Features related to employment decisions are **blocked by default**:

```python
# Gated features (require REGULATORY_REVIEW_REQUIRED approval)
- candidate_ranking
- employee_evaluation
- promotion_decisions
- disciplinary_actions
- worker_monitoring
- task_allocation_by_personal_traits
```

The `RegulatoryReviewGate` enforces:

```python
def is_allowed(self, feature_id: str, feature: AIFeature) -> bool:
    if feature.data_policy == AIUsePolicy.REGULATORY_REVIEW_REQUIRED:
        return feature_id in self._approved_features  # Empty by default
    return True
```

### Allowed ERP Queries

Administrative ERP queries (e.g., "list employees", "show payroll") are **not blocked** — only automated decision-making features are gated.

---

## Runtime Versioning

Every AI operation captures runtime versions for auditability:

```python
@dataclass(frozen=True)
class RuntimeVersions:
    gestor_ia_version: str       # Application version
    workflow_version: str        # Workflow definition version
    ai_config_version: str       # AI config version
    provider_version: str        # e.g., "ollama:qwen3.5:4b-invoice"
    policy_version: str          # AI policy version
    git_sha: str | None          # Git commit (if available)
```

Captured at operation start and correlated with AI trace and audit log.

---

## Provider / Deployer Separation

Gestor-IA architecture distinguishes roles:

| Role | Responsibility |
|------|----------------|
| **AI Model Provider** | Ollama (local), NVIDIA, OpenAI, Anthropic — provide foundation models |
| **Gestor-IA Product** | Integrates models, enforces policies, provides traceability |
| **Deploying Company** | Configures instances, chooses models, sets policies |
| **End User/Operator** | Confirms AI outputs, triggers ERP writes |

Gestor-IA does **not** develop foundation models. It integrates third-party models with compliance controls.

---

## Audit Correlation

AI traces correlate with ERP audit via shared `correlation_id`:

```
AI Trace (operation_id)
    ↓ correlation_id
Document Ingestion (document_hash)
    ↓ correlation_id
Preview Generation (preview_id)
    ↓ correlation_id
Human Confirmation (confirmation_id, confirmed_by)
    ↓ correlation_id
ERP Write (erp_write_id = invoice_id)
    ↓ correlation_id
Audit Log (resource_id, action, actor, result)
```

This enables end-to-end reconstruction without storing sensitive data.

---

## Local / Private AI Policy

`LOCAL_ONLY` means **"only permitted private AI providers"**, not "localhost only".

### Valid Configurations

| Deployment | Ollama Endpoint | Valid? |
|------------|-----------------|--------|
| Local dev | `http://127.0.0.1:11434` | ✓ |
| VPS + local Ollama | `http://ollama:11434` (Docker network) | ✓ |
| VPS + remote GPU | `http://gpu-server:11434` (WireGuard/VPN) | ✓ |
| VPS + remote GPU | `https://gpu.company.com` (TLS) | ✓ |
| Any | `https://api.openai.com` | ✗ (cloud) |

### Enforcement

- `invoice_processing` task policy = `LOCAL_ONLY` (hardcoded)
- Registry rejects cloud providers for `LOCAL_ONLY` features
- No automatic cloud fallback on local model failure → **FAIL CLOSED**

---

## Compliance Checklist (Technical)

| Requirement | Implementation | Status |
|-------------|----------------|--------|
| Feature-level risk classification | `AIFeatureRegistry` + `AIRiskClassification` | ✓ |
| AI use policy enforcement | `AIUsePolicy` enum + registry checks | ✓ |
| Traceability & correlation | `AITraceabilityLogger` + correlation IDs | ✓ |
| Data minimisation | `DataMinimisationFilter` | ✓ |
| Configurable retention | `RetentionPolicy` + `RetentionConfig` | ✓ |
| Human oversight recording | `HumanOversightRecorder` | ✓ |
| Transparency notices | `TransparencyManager` | ✓ |
| HR/high-risk firewall | `RegulatoryReviewGate` | ✓ |
| Runtime versioning | `RuntimeVersionCapture` | ✓ |
| Provider/deployer separation | Architecture + documentation | ✓ |
| Audit correlation | Shared `correlation_id` across systems | ✓ |
| Local-only enforcement | Registry policy checks + FAIL CLOSED | ✓ |
| No cloud fallback for invoices | Hardcoded `LOCAL_ONLY` policy | ✓ |

---

## Deployment Considerations

### Development
- All endpoints default to localhost for convenience
- Real config via `instances/{id}/config.yml` + `instance.env`

### VPS Production
- `database.host`: Shared MariaDB server IP
- `dolibarr.internal_url`: Internal Docker/container network URL
- `ai.ollama_endpoint`: Private AI server (WireGuard/VPN/TLS)
- Redis: Shared server, logical DB separation per instance

### Security
- No hardcoded secrets — all via `secrets_refs` → environment variables
- User-scoped Dolibarr API keys — no admin fallback
- Instance isolation at every layer (DB, Redis, filesystem, Dolibarr)

---

## Limitations & Future Work

1. **Withholding tax mapping**: Dolibarr REST API may not support direct withholding line items
2. **Attachment idempotency**: SHA256 of content used; same file to different invoices needs review
3. **AI trace storage**: Currently logs to audit system; dedicated table may be needed for query performance
4. **Multi-VAT Dolibarr compatibility**: Integration test matrix needed
5. **Regulatory review workflow**: UI/admin command for feature approval needed

---

## References

- EU AI Act (Regulation 2024/1689)
- ISO/IEC 42001 (AI Management Systems)
- NIST AI Risk Management Framework
- Gestor-IA Architecture Decision Records (ADRs)