---
name: implementation-plan
description: When the user asks for a plan, produce a short TDD-style implementation plan tied to real files and verify commands; guidance, not a gate.
triggers:
  - 实施计划
  - 拆任务
  - 开始编码计划
  - implementation plan
  - plan the work
allowed_tools:
  - read_file
  - list_dir
  - grep
  - codebase_search
  - write_text
  - patch_file
risk_level: low
verification: every step names its files and a verify command with an expected exit code
version: 1.1.0
---

# Implementation plan

1. Read the governing spec, current code path, tests, and dirty-worktree constraints before writing the plan.
2. Split work by responsibility and file ownership; do not overwrite unrelated dirty changes.
3. For behavior changes use RED → expected failure → minimal implementation → focused GREEN → regression.
4. Each step names its exact files and a verify command with the expected exit code. Avoid "later", "appropriate", and untestable placeholders.
5. The plan is advice, not an approval step: no plan confirmation is needed before coding, and nothing is written to `TASKS.md`. Keep the plan in the chat (or a doc the user asked for).
