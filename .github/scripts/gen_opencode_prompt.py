#!/usr/bin/env python3
"""Generate OpenCode prompt for issue implementation."""

import os
import sys

def main():
    title = os.environ.get('ISSUE_TITLE', '')
    number = os.environ.get('ISSUE_NUMBER', '')
    body = os.environ.get('ISSUE_BODY', '')

    if not title or not number:
        print("ERROR: ISSUE_TITLE and ISSUE_NUMBER environment variables required", file=sys.stderr)
        sys.exit(1)

    prompt = f"""# Issue: {title}

**Issue Number:** #{number}

**Description:**
{body}

---

## Instructions for OpenCode

1. **Read and understand** the issue completely
2. **Create implementation** exclusively within the scope defined in this issue
3. **Follow the project architecture** documented in README.md and AGENTS.md
4. **Run relevant tests** after implementation:
   - `make test-unit` for unit tests
   - `make test-integration` for integration tests (if applicable)
   - `make test-isolation` for cross-instance isolation tests (CRITICAL)
5. **Run quality checks**: `make pre-commit` (lint, format, type-check)
6. **Commit changes** with clear, conventional commit messages
7. **Do NOT** modify tests to hide bugs
8. **Do NOT** skip tests to get green
9. **Do NOT** touch business logic unrelated to this issue (invoices, Telegram, Dolibarr, multi-company isolation)
10. **Respect secrets policy**: Never log or commit secrets

## Project Context

- **Architecture**: Multi-instance, shared Hermes Core, CompanyContext per request
- **Core location**: `core/hermes/` - generic, no business logic
- **Company-specific code**: `companies/{{instance}}/` - extensions only
- **Tests**: `tests/` - unit, integration, isolation (critical), e2e, commands, insights
- **Config**: `instances/{{id}}/config.yml` + `instance.env` (gitignored)
- **Secrets**: Never in Git, use .env / instance.env / secrets_refs

## Required Output

After implementation, provide a summary including:
- What was changed (files)
- Tests executed and results
- Any risks or decisions made
- Reference to this issue (#{number})
"""

    with open('/tmp/opencode_prompt.md', 'w') as f:
        f.write(prompt)

    print("prompt_path=/tmp/opencode_prompt.md")

if __name__ == '__main__':
    main()