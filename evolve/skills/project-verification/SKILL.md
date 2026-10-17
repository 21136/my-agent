---
name: project-verification
description: Run project tests, builds, and acceptance checks while preserving evidence and truthful claims.
triggers:
  - 验收
  - 测试
  - 构建
  - verify
  - test
  - build
allowed_tools:
  - run_command
  - run_tests
  - read_file
risk_level: medium
verification: exit code, captured output, and evidence freshness
version: 1.0.0
api_version: 1
status: stable
compatibility:
  models:
    - 0x567-flash
capabilities:
  - inspect_files
  - run_tests
---

# Project verification

1. Identify the configured test/build/acceptance command from the project facts.
2. Run the narrowest useful check first, then expand only when evidence requires it.
3. Preserve the distinction between pass, fail, blocked, skipped, and not run.
4. Report command, working directory, exit status, and relevant output. Never call a release or completion claim verified without evidence.
