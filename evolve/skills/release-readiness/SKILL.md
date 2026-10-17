---
name: release-readiness
description: Verify delivery evidence, acceptance, review blockers, release artifacts, and human publish approval before claiming completion.
triggers:
  - 发布前检查
  - 交付检查
  - 准备发布
  - release readiness
  - ready to ship
allowed_tools:
  - read_file
  - list_dir
  - run_command
  - run_tests
risk_level: high
verification: build/test/acceptance evidence, current RELEASE/VERIFY facts, review blockers clear, and human release approval
version: 1.0.0
---

# Release readiness

1. Confirm the actual project root, current task/artifact facts, and recent verification evidence.
2. Run the narrowest real build/test/acceptance commands and record exit status and working directory.
3. Check `VERIFY.md`, `RELEASE.md`, review blockers, current artifact revisions, and external/automatic-mode evidence when applicable.
4. Separate “tests pass”, “ready for human release review”, and “published”; never collapse them into one claim.
5. Release/publish/Git push remains a user-approved risk action; this Skill can prepare evidence but cannot approve or execute it silently.
