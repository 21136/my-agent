# Project Workflow Skills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second, project-specific batch of Skills for taking over, planning, implementing, reviewing, resuming, and releasing real workspace projects.

**Architecture:** Keep Skill selection request-scoped and permission-neutral. Each Skill describes the repository’s actual artifact chain (`PROJECT/SCOPE/DESIGN/TASKS/VERIFY/RELEASE`), `.venv` Python rule, ordinary intent policy, and automatic v1/v2 boundary. The registry remains the loader; Executor remains the hard permission owner.

**Spec:** `docs/superpowers/specs/2026-10-16-skill-registry-design.md` and current project governance in `docs/DOC-GOVERNANCE.md`.

## Global Constraints

- Preserve the dirty worktree; no reset, clean, stage, or commit.
- Do not invent project facts, test results, completion, or release approval.
- Use `.venv\Scripts\python.exe` for Python validation when the project has `.venv`.
- Keep ordinary `read_only/plan_only/execute` request semantics and automatic v1/v2 ownership unchanged.

---

### Task 1: Project intake and requirements Skills

**Files:** Create `evolve/skills/project-intake/SKILL.md`, `evolve/skills/requirements-to-spec/SKILL.md`.

- [ ] Encode read-only project reconnaissance, external-project adoption facts, Git dirty state, artifact inventory, and explicit scope confirmation.
- [ ] Encode the repository’s four/core artifact chain and the rule that requirements/plan language does not authorize writes.
- [ ] Include exact evidence and stop conditions.

### Task 2: Planning, review, and resume Skills

**Files:** Create `evolve/skills/implementation-plan/SKILL.md`, `evolve/skills/code-review/SKILL.md`, `evolve/skills/resume-incomplete-work/SKILL.md`.

- [ ] Encode TDD-sized implementation plans and file ownership.
- [ ] Encode diff/test/permission/documentation review without treating docs or status snapshots as proof.
- [ ] Encode unfinished-work recovery from files, TASKS, git diff, session evidence, and last verification; never auto-confirm or auto-publish.

### Task 3: Release readiness Skill and registry tests

**Files:** Create `evolve/skills/release-readiness/SKILL.md`; extend `agent-core/tests/test_skill_registry.py`.

- [ ] Encode evidence-first build/test/acceptance/release checks and human release confirmation.
- [ ] Add matching tests for all new workflow Skills and verify the registry can discover them.
- [ ] Run Skill registry, prompt integration, ordinary backend, external-project, and Desktop build checks.
- [ ] Update Skill documentation/task index and leave all changes unstaged/uncommitted.
