---
name: implementation-plan
description: Produce a bite-sized TDD implementation plan tied to real files, tests, evidence, and recovery boundaries.
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
verification: every task has files, RED/GREEN verification, ownership, and a concrete completion gate
version: 1.0.0
---

# Implementation plan

1. Read the governing spec, current code path, tests, and dirty-worktree constraints before writing the plan.
2. Split work by responsibility and file ownership; do not overwrite unrelated dirty changes.
3. For behavior changes use RED → expected failure → minimal implementation → focused GREEN → regression.
4. Each task must name its exact files, command, expected evidence, and stop/recovery condition. Avoid “later”, “appropriate”, and untestable placeholders.
5. The plan is a guide, not permission to change code; implementation begins only when the user’s current request is execute.
