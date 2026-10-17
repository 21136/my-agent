---
name: documentation-governance
description: Classify project docs by status and authority, update the index, and audit links and stale claims.
triggers:
  - 文档治理
  - 整理文档
  - 文档过时
  - 文档混乱
  - documentation governance
allowed_tools:
  - read_file
  - grep
  - write_text
  - patch_file
  - run_command
risk_level: low
verification: local-link check, contradictory-claim classification, and diff check
version: 1.0.0
---

# Documentation governance

1. Classify documents as current, reference, proposal, historical, or generated; separate authoritative specs from snapshots.
2. Preserve history but mark superseded rules so they cannot be mistaken for current behavior.
3. Keep `docs/README.md` as the reading entry point and link current specs, plans, and evidence.
4. Verify local links, code anchors, test commands, and stale contradictory wording before claiming cleanup.
