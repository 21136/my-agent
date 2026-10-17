---
name: ordinary-project-execution
description: Execute ordinary project work by request intent, path scope, risk approval, and real evidence.
triggers:
  - 实现
  - 修复
  - 写代码
  - 改代码
  - execute
  - implement
allowed_tools:
  - read_file
  - run_command
  - write_text
  - patch_file
  - run_tests
risk_level: medium
verification: report changed paths and actual test output
version: 1.0.0
---

# Ordinary project execution

1. Resolve this request as `read_only`, `plan_only`, or `execute`; do not turn a persisted stage into a permission mode.
2. Stay inside the bound project root and ask before expanding scope or performing risky/external actions.
3. Read the relevant files, make the smallest coherent change, and run the project’s real checks.
4. Report changed paths, commands, results, and any remaining uncertainty. Do not claim completion from a plan or stale status snapshot.
