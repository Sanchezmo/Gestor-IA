# Company Context Delta Specification

## Purpose

Delta specification for modifications to CompanyContext to enforce user-scoped Dolibarr client creation with FAIL CLOSED semantics.

## MODIFIED Requirements

### Requirement: create_dolibarr_client_for_user(identity)

The system MUST create Dolibarr client using ONLY the requesting user's API key — NO admin fallback, NO shared credentials.

(Previously: create_dolibarr_client() used admin/shared credentials with optional user override)

The system SHALL:
- Accept user identity (Telegram user_id + instance_id)
- Look up user's Dolibarr API key in instance config
- FAIL CLOSED if no API key configured for user
- Return DolibarrClient configured with user's key only
- NOT fall back to instance-level admin key

#### Scenario: User has API key — client created

- GIVEN user_id=12345 has dolibarr_api_key="user_key_abc" in instance config
- WHEN create_dolibarr_client_for_user(identity) called
- THEN DolibarrClient created with Authorization: Bearer user_key_abc
- AND client works for user's permissions only

#### Scenario: User missing API key — FAIL CLOSED

- GIVEN user_id=12345 has NO dolibarr_api_key in instance config
- WHEN create_dolibarr_client_for_user(identity) called
- THEN raise UserAPIKeyMissingError
- AND NO client returned
- AND NO fallback to admin key attempted

#### Scenario: Invalid API key — FAIL CLOSED

- GIVEN user has dolibarr_api_key but Dolibarr returns 401 on test call
- WHEN create_dolibarr_client_for_user(identity) called
- THEN raise InvalidUserAPIKeyError
- AND NO fallback to admin key

### Requirement: Instance Config User API Key Storage

The system MUST store user API keys in instance config under users mapping.

Config structure addition:
```yaml
users:
  "12345":  # Telegram user_id
    dolibarr_api_key: "user_specific_key"
    dolibarr_user_id: 42  # optional Dolibarr user ID for reference
```

#### Scenario: Config validates user API key presence

- GIVEN instance config loaded
- WHEN validating for writer phase
- THEN all users expected to write MUST have dolibarr_api_key
- AND missing keys flagged at startup (warn, not fail — allows read-only users)