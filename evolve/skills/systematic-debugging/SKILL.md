---
name: systematic-debugging
description: Reproduce a failure, trace its root cause, apply one minimal fix, and verify regression coverage.
triggers:
  - 报错
  - 崩溃
  - 不工作
  - 调试
  - bug
  - traceback
allowed_tools:
  - read_file
  - grep
  - codebase_search
  - run_command
  - patch_file
  - run_tests
risk_level: medium
verification: reproducible failure before fix and passing regression after fix
version: 1.0.0
---

# Systematic debugging

1. Read the complete error and reproduce it consistently.
2. Trace the bad value or failure backward to its source; do not patch only the symptom.
3. Add or identify a focused failing test, make one minimal fix, and rerun the regression.
4. If repeated fixes reveal a coupling problem, stop and question the architecture instead of stacking patches.
