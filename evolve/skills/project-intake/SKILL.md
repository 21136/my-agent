---
name: project-intake
description: Read-only reconnaissance for an existing or newly adopted project before any implementation decision.
triggers:
  - 接手项目
  - 了解项目
  - 扫描项目
  - 项目现状
  - project intake
  - inspect project
allowed_tools:
  - read_file
  - list_dir
  - grep
  - codebase_search
  - run_command
risk_level: low
verification: artifact inventory, git status, detected test/build commands, and explicit unknowns
version: 1.0.0
---

# Project intake

1. Establish the project root and current request intent. A read-only intake never writes code, creates a plan, or changes project state.
2. Inspect the real project facts: Git status, README/PROJECT, package or Python manifests, `TASKS.md`, `VERIFY.md`, tests, build commands, and recent evidence.
3. For an external project, use the external-adoption scan first; never copy or modify the original during reconnaissance.
4. Report what exists, what is missing, what is dirty/uncommitted, and what cannot be verified. Do not infer completion from a README or a stale status card.
5. End with a bounded next action: read-only explanation, requirements/spec, implementation plan, or explicit execute request.
