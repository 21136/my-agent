---
name: external-project-adoption
description: Adopt an unfinished external project in place through a read-only scan and confirmed junction.
triggers:
  - 导入项目
  - 外部项目
  - 原地接入
  - junction
  - import project
allowed_tools:
  - read_file
  - list_dir
  - run_command
risk_level: high
verification: scan facts, confirmed junction target, original-root write, and reopen recovery
version: 1.0.0
---

# External project adoption

1. Scan first without writing: root, Git state, docs/tasks, build markers, and missing TASKS.
2. Never read or persist secret contents; `.env` is a name-only marker.
3. Apply only after explicit confirmation. The junction must point to the original directory and never replace an existing logical project path.
4. If TASKS.md is absent, create only a clearly marked import-review draft; never fabricate confirmed or completed state.
5. Verify the original directory receives a test write and that a new session can reopen the adopted project.
