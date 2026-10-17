# Documentation Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Establish a small, explicit documentation governance layer so current specifications, implementation status, historical proposals, and generated snapshots cannot be mistaken for one another.

**Architecture:** Add one governance document and make `docs/README.md` the navigation/index authority. Add compact metadata and current-implementation notices to the ordinary/project interaction documents without moving files or rewriting historical sections. Validate links, metadata presence, and stale contradictory wording with repository-local scripts/grep.

**Tech Stack:** Markdown, PowerShell/Python repository checks, existing docs index.

**Spec:** `docs/superpowers/specs/2026-10-16-ordinary-project-state-machine-design.md` and the current ordinary implementation in `agent-core/` / `desktop/`.

## Global Constraints

- Preserve unrelated dirty/staged/untracked work; do not reset, clean, stage, commit, or move the existing documentation tree.
- Keep existing relative links stable; this pass adds governance and notices rather than physically moving historical documents.
- Do not call a document authoritative merely because it exists; implementation claims require code/test evidence.
- Automatic v1/v2 documents retain their own stage/checkpoint semantics; ordinary-mode governance must not rewrite them.

---

### Task 1: Governance contract and index

**Files:**
- Create: `docs/DOC-GOVERNANCE.md`
- Modify: `docs/README.md`

- [x] Define document statuses (`current`, `reference`, `proposal`, `historical`, `generated`) and authority levels (`authoritative`, `supporting`, `snapshot`).
- [x] Define required metadata fields and source hierarchy.
- [x] Add ordinary-mode and automatic-mode reading paths to `docs/README.md`.
- [x] Add the governance document to the root documentation index.

### Task 2: Mark current ordinary/project documents

**Files:**
- Modify: `docs/PROJECT-MODE.md`
- Modify: `docs/ORDINARY-FLOW-REDUCTION.md`
- Modify: `docs/INTERACTION-REDESIGN.md`
- Modify: `docs/superpowers/specs/2026-10-16-ordinary-project-state-machine-design.md`
- Modify: `docs/superpowers/plans/2026-10-16-ordinary-intent-policy.md`

- [x] Add compact metadata headers or explicit current-implementation notices.
- [x] Ensure old proposal sections remain readable but cannot be mistaken for current behavior.
- [x] Link the current spec/plan and implementation evidence from the index.

### Task 3: Repository audit and verification

**Files:**
- Modify: `docs/README.md` only if audit reveals stale navigation.

- [x] Check all local Markdown links from the changed docs.
- [x] Search ordinary docs for contradictory current claims (`阶段墙`, `计划确认门`, `直接实现自动确认`) and classify each remaining hit as current, historical, or automatic-only.
- [x] Run focused ordinary backend/UI tests already defined by the implementation plan.
- [x] Run `git diff --check` on task-owned files and leave changes unstaged/uncommitted.
