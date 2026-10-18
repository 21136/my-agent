---
name: plan-and-verify
description: Lightweight plan-then-verify method for coding work; guidance only, never a gate or required checklist.
triggers:
  - 计划并验证
  - 先想清楚再做
  - 怎么验证
  - 验证命令
  - plan and verify
  - how do I verify
allowed_tools:
  - read_file
  - list_dir
  - grep
  - codebase_search
  - run_command
  - run_tests
risk_level: low
verification: a concrete verify command was run and its exit code reported
version: 1.0.0
---

# Plan and verify

This skill is advice, not a gate. Nothing here blocks writing code, and there is no task list to maintain.

1. Before a non-trivial change, say in 1–3 sentences what you will change and which files are involved. Skip this for small, obvious edits.
2. Pick the verify command up front: the project's tests, build, lint, or a one-off script. Prefer the narrowest command that would fail if the change were wrong.
3. Make the change, then run the verify command. **The exit code is the truth**: done means the command exited with the expected code, not that the work "looks finished".
4. On failure, read the command's last ~20 output lines, fix the cause, and run again. Do not mark anything done or claim success without a passing run.
5. Report the command, its exit code, and anything still not verified. Do not create or update `TASKS.md`.
