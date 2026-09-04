#!/usr/bin/env python3
"""Generate OpenCode prompt for PR review feedback."""

import os
import sys

def main():
    pr_number = os.environ.get('PR_NUMBER', '')
    pr_title = os.environ.get('PR_TITLE', '')
    pr_branch = os.environ.get('PR_BRANCH', '')
    pr_base = os.environ.get('PR_BASE', '')
    all_feedback = os.environ.get('ALL_FEEDBACK', '')

    if not pr_number or not pr_title or not pr_branch:
        print("ERROR: PR_NUMBER, PR_TITLE, PR_BRANCH environment variables required", file=sys.stderr)
        sys.exit(1)

    prompt = f"""# PR Review: #{pr_number}

**PR Title:** {pr_title}
**Branch:** `{pr_branch}`
**Base:** `{pr_base}`

---

## Review Feedback to Address

{all_feedback}

---

## Instructions for OpenCode

1. **Read all review feedback** carefully
2. **Analyze the current PR changes** (run `git diff main...HEAD`)
3. **Implement fixes** for each actionable review comment
4. **Follow the project architecture** documented in README.md and AGENTS.md
5. **Run relevant tests** after changes:
   - `make test-unit` for unit tests
   - `make test-integration` for integration tests (if applicable)
   - `make test-isolation` for cross-instance isolation tests (CRITICAL)
6. **Run quality checks**: `make pre-commit` (lint, format, type-check)
7. **Commit changes** to the **SAME branch** ({pr_branch})
8. **Do NOT** create a new branch or PR
9. **Do NOT** modify tests to hide bugs
10. **Do NOT** skip tests to get green
11. **Respect secrets policy**: Never log or commit secrets

## Project Context

- **Architecture**: Multi-instance, shared Hermes Core, CompanyContext per request
- **Core location**: `core/hermes/` - generic, no business logic
- **Company-specific code**: `companies/{{instance}}/` - extensions only
- **Tests**: `tests/` - unit, integration, isolation (critical), e2e, commands, insights
- **Config**: `instances/{{id}}/config.yml` + `instance.env` (gitignored)
- **Secrets**: Never in Git, use .env / instance.env / secrets_refs

## Required Output

After implementing fixes, provide a summary including:
- Which review comments were addressed
- What was changed (files)
- Tests executed and results
- Any remaining unresolved comments with explanation
"""

    with open('/tmp/opencode_pr_prompt.md', 'w') as f:
        f.write(prompt)

    print("prompt_path=/tmp/opencode_pr_prompt.md")

if __name__ == '__main__':
    main()