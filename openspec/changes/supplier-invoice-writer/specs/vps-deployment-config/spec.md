# VPS Deployment Configuration Specification

## Purpose

Provide VPS-ready configuration with zero hardcoded localhost/127.0.0.1 defaults, configurable remote private AI server, and all external endpoints driven by instance config.

## Requirements

### Requirement: No Hardcoded Localhost in Runtime

The system MUST NOT contain hardcoded "localhost", "127.0.0.1", or "0.0.0.0" in any runtime configuration resolution path.

#### Scenario: InstanceConfig template has no localhost defaults

- GIVEN config/instances/template.yaml inspected
- WHEN parsing template
- THEN no field has default value "localhost", "127.0.0.1", or "0.0.0.0"
- AND all endpoint fields are empty or commented with placeholder

#### Scenario: Config loader rejects localhost in production

- GIVEN instance config loaded for production instance
- WHEN any endpoint resolves to localhost/127.0.0.1
- THEN validation fails with "localhost not allowed in production — configure explicit endpoint"

### Requirement: Configurable OLLAMA_BASE_URL

The system MUST support remote private AI server via OLLAMA_BASE_URL config.

Config fields:
- ollama_base_url: str (e.g., "https://ai.internal.company.com:11434")
- ollama_timeout_seconds: int (default 120)
- ollama_tls_verify: bool (default true)

#### Scenario: Remote Ollama configured and used

- GIVEN instance config has ollama_base_url="https://ai.internal:11434"
- WHEN AI inference requested
- THEN HTTP client targets configured URL
- AND TLS verification applied per config
- AND timeout respects configured value

#### Scenario: Local Ollama still supported via explicit config

- GIVEN instance config has ollama_base_url="http://localhost:11434"
- WHEN AI inference requested
- THEN local Ollama used (explicit opt-in)
- AND no implicit localhost fallback

### Requirement: Dolibarr Internal URL from Config

The system MUST read Dolibarr internal URL from instance config.

Config field:
- dolibarr_internal_url: str (e.g., "https://erp.internal.company.com")

#### Scenario: Dolibarr client uses configured internal URL

- GIVEN instance config has dolibarr_internal_url="https://erp.internal"
- WHEN create_dolibarr_client_for_user() called
- THEN client base URL = configured value
- AND no fallback to environment variable or default

### Requirement: Redis from Config

The system MUST read Redis connection from instance config.

Config fields:
- redis_host: str
- redis_port: int
- redis_db: int
- redis_password: str (optional)
- redis_tls: bool (default false)

#### Scenario: Redis client uses configured connection

- GIVEN instance config has redis_host="redis.internal", redis_port=6379
- WHEN Redis connection established
- THEN connection uses configured host/port
- AND password/TLS applied per config

### Requirement: MariaDB from Config

The system MUST read MariaDB connection from instance config.

Config fields:
- mariadb_host: str
- mariadb_port: int
- mariadb_database: str
- mariadb_user: str
- mariadb_password: str
- mariadb_tls: bool (default false)
- mariadb_ca_cert: str (optional path)

#### Scenario: MariaDB connection uses configured values

- GIVEN instance config has mariadb_host="db.internal", mariadb_database="gestor_ia"
- WHEN audit DB connection established
- THEN connection uses configured values
- AND TLS/CA cert applied per config

### Requirement: Task Policies with Explicit LOCAL_ONLY

The system MUST support task_policies configuration classifying features by AIUsePolicy.

Config structure:
```yaml
task_policies:
  invoice_processing:
    policy: "LOCAL_ONLY"
    require_human_oversight: true
  extraction:
    policy: "LOCAL_ONLY"
    require_human_oversight: true
  validation:
    policy: "LOCAL_ONLY"
    require_human_oversight: true
  # future cloud tasks:
  # heavy_analysis:
  #   policy: "CLOUD_ALLOWED"
  #   cloud_endpoint: "https://ai-cloud.internal"
```

#### Scenario: Task policies loaded and enforced

- GIVEN instance config with task_policies as above
- WHEN AI registry initializes
- THEN features mapped to policies
- AND LOCAL_ONLY enforced for invoice features

### Requirement: Remote AI Server Separation

The system MUST support separate config for future cloud AI tasks (not used in Phase 1).

Config fields (preparatory):
- cloud_ai_base_url: str (optional)
- cloud_ai_api_key: str (optional, secret)
- cloud_ai_allowed_features: list[str] (empty in Phase 1)

#### Scenario: Cloud AI config present but unused in Phase 1

- GIVEN instance config has cloud_ai_base_url configured
- WHEN Phase 1 runs (LOCAL_ONLY enforced)
- THEN cloud_ai_base_url NOT used
- AND validation ensures cloud_ai_allowed_features is empty