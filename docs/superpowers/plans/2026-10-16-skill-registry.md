# Skills Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a trusted, request-scoped Skill registry and prompt integration without bypassing existing tool permissions.

**Architecture:** `agent-core/skill_registry.py` discovers `evolve/skills/*/SKILL.md`, parses bounded metadata, selects candidates deterministically from the current user request, and formats a short index plus at most one full skill context. `loader.build_system_prompt` injects these only into dynamic overlay; Executor remains the permission authority.

**Tech Stack:** Python 3.14, pytest, Markdown frontmatter, existing loader.

**Spec:** `docs/superpowers/specs/2026-10-16-skill-registry-design.md`.

## Global Constraints

- Preserve the dirty worktree; no reset, clean, staging, or commit.
- Do not execute Skill-provided scripts automatically.
- Keep selection request-scoped and nonserialized.
- Use `.venv\Scripts\python.exe` for Python validation.

---

### Task 1: Registry and built-in Skill packages

**Files:**
- Create: `agent-core/skill_registry.py`
- Create: `evolve/skills/*/SKILL.md`
- Create: `agent-core/tests/test_skill_registry.py`

- [x] Add RED tests for parsing, invalid package isolation, trigger matching, bounded body loading, and path traversal rejection.
- [x] Implement registry descriptors, safe loader, deterministic selection, and prompt formatting.
- [x] Add the five first-party Skill packages from the spec.
- [x] Run registry tests.

### Task 2: Loader integration

**Files:**
- Modify: `agent-core/session.py` only if a runtime-only skill hint is needed.
- Modify: `agent-core/agent.py`
- Modify: `agent-core/loader.py`
- Create/extend: `agent-core/tests/test_skill_prompt_integration.py`

- [x] Add failing tests for overlay-only skill index/context and request-scoped selection.
- [x] Set runtime-only skill candidates at turn start; do not serialize them.
- [x] Inject short index and selected skill context into dynamic system prompt.
- [x] Preserve automatic v1/v2 behavior and existing prompt section order outside the new sections.
- [x] Run prompt and ordinary regression tests.

### Task 3: Documentation and audit

**Files:**
- Modify: `docs/README.md`, `docs/DOC-GOVERNANCE.md`, `docs/LAYERS.md`, `docs/EVOLVE.md`, `docs/TASKS.md` only where the old “never do skills” wording is now stale.
- Update: `docs/superpowers/specs/2026-10-16-skill-registry-design.md` status after verification.

- [x] Classify old skill references as historical or current.
- [x] Add the Skill registry reading path and security boundary.
- [x] Run focused tests, build if prompt/runtime files changed, and `git diff --check`.
