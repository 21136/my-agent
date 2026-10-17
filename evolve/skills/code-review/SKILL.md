---
name: code-review
description: Review changes for behavior, permissions, regressions, evidence truth, and documentation consistency.
triggers:
  - 审查代码
  - review
  - 检查 diff
  - 代码审查
  - review this change
allowed_tools:
  - read_file
  - grep
  - codebase_search
  - run_command
  - run_tests
risk_level: low
verification: findings are severity-ranked, reproducible, and tied to changed lines or missing tests
version: 1.0.0
---

# Code review

1. Inspect only the intended diff first; preserve unrelated dirty/staged work.
2. Check behavior, path/risk permissions, ordinary intent boundaries, automatic v1/v2 isolation, error recovery, and evidence claims.
3. Run focused tests and `git diff --check`; distinguish structural tests from real runtime/browser evidence.
4. Report findings before praise. A passing test suite does not prove live authenticated or external-system acceptance.
5. Never edit or auto-approve the change while reviewing unless the user separately requests implementation.
