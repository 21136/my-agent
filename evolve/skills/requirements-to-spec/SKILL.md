---
name: requirements-to-spec
description: Convert a project request into bounded requirements, acceptance criteria, scope, and implementation constraints.
triggers:
  - 需求
  - 范围
  - 验收标准
  - 设计方案
  - requirements
  - acceptance criteria
allowed_tools:
  - read_file
  - list_dir
  - grep
  - codebase_search
  - write_text
  - patch_file
risk_level: medium
verification: requirements, non-goals, acceptance commands, affected files, and unresolved decisions are explicit
version: 1.0.0
---

# Requirements to spec

1. Keep source claims, user requirements, hypotheses, and temporary answer plans separate.
2. Bind the request to the project’s actual artifacts: `PROJECT.md`, `SCOPE.md`, `DESIGN.md`, `TECH-DESIGN.md`, `VERIFY.md`, and `RELEASE.md` when present (a legacy `TASKS.md` is history only).
3. State non-goals, risk boundaries, affected paths, acceptance criteria, and the exact command that can verify each criterion.
4. A plan or requirements document does not itself authorize source writes; only the current request intent and Executor policy do.
5. If the request is ambiguous between planning and implementation, ask one focused question instead of inferring permission from a legacy stage.
