# Ordinary Intent and Action Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Use TDD for every behavior change.

> 状态：**current · implementation complete for ordinary backend/Desktop slices**（2026-10-16）；Task 7 文档治理与最终审计随后补齐。

**Goal:** Remove ordinary project phase/plan gates from code execution while retaining request scope, project-root containment, dangerous-action approval, verification truth, and runaway v1/v2 ownership.

**Architecture:** Resolve a request-scoped `read_only | plan_only | execute` intent and optional task-scope hint. Apply ordinary tool policy by intent, project binding/path, and existing risk approvals; do not use persisted project stage/plan/entry to authorize writes. Reuse `ExecutionLifecycle` and keep request intent out of `SessionMeta` serialization.

**Tech Stack:** Python 3.14, pytest, TypeScript, Node.js 24 built-in test runner, Electron/Vite desktop.

**Spec:** `docs/superpowers/specs/2026-10-16-ordinary-project-state-machine-design.md`.

## Global Constraints

- Preserve all unrelated dirty/staged/untracked changes; no reset, clean, broad staging, or commit.
- Work only in ordinary sessions (`project_runaway_enabled == false`); preserve runaway v1/v2 stage and permission branches.
- Intent/task scope is request-local and nonserialized. Explicit read-only/plan-only user intent takes precedence over stale session metadata; a pending UI override is consumed once and discarded if the new message is explicitly read-only/plan-only. Plan-only is natural-language explicit in this cut; do not add a sticky mode toggle.
- Keep project-root/path protection and existing risky/external action approvals. A project root is not blanket approval for unrelated scope.
- `draft`, `plan_dirty`, stale/missing planning artifacts, templates, and legacy stages must not independently deny ordinary in-root execution.
- Verify Python with `.venv\Scripts\python.exe`; print `sys.executable` and import `pytest` before Python validation.
- Do not stage or commit. Stay in this dirty checkout; a new worktree would omit relevant uncommitted context.

---

### Task 1: Request intent resolver (RED → GREEN)

**Files:** Create `agent-core/tests/test_ordinary_work_intent.py`; modify `agent-core/turn_intent.py`.

**Interface:** `OrdinaryWorkIntent = Literal["read_only", "plan_only", "execute"]`; `resolve_ordinary_work_intent(user_text, classified_intent, explicit_override=None) -> OrdinaryWorkIntent`. Only explicit Plan-only selection/wording produces `plan_only`; clear implementation intent wins even if the request mentions planning/docs; QA/recall/status remains read-only.

- [ ] Add failing tests for: execute request containing plan words; explicit “只给方案，不改代码”; QA/read-only; current intent `plan` plus an imperative implementation request; one-shot override precedence.
- [ ] Run `& '.venv\Scripts\python.exe' -m pytest agent-core/tests/test_ordinary_work_intent.py -q`; confirm expected assertion/import failure, not collection failure.
- [ ] Implement the minimal resolver in `turn_intent.py`.
- [ ] Re-run the new tests and `agent-core/tests/test_turn_intent_questions.py`.

Expected behavior example:

```python
def test_plan_words_do_not_block_an_implementation_request():
    assert resolve_ordinary_work_intent(
        "先整理方案，再实现登录修复", "plan"
    ) == "execute"
```

### Task 2: Carry intent only for the current request (RED → GREEN)

**Files:** Modify `agent-core/session.py`, `agent-core/agent.py`, `agent-core/tools/executor.py`, `agent-core/project_api.py`; test `test_ordinary_work_intent.py` and relevant API tests.

**Interface:** Add nonserialized `Session.next_work_intent` and `Session.next_task_scope`; add runtime-only `ExecutorSession.ordinary_work_intent` and `ordinary_task_scope`. `Agent.run_turn` consumes and clears one-shot overrides, resolves text intent, and syncs it to the executor before tools run. `project.direct_implement`, `project.task.start`, and plan-response “规划后开工” may arm one-shot execute/task scope, but must not persist permission mode in `SessionMeta`.

- [ ] Add failing tests proving UI/API overrides are consumed once, are not present in `SessionMeta.to_dict()`, and a following read-only request is not forced into execute.
- [ ] Run those tests and verify the intended failures.
- [ ] Implement ephemeral fields and turn-start consume/clear/sync logic.
- [ ] Re-run `test_ordinary_work_intent.py`, `test_ordinary_direct_implement_gates.py`, and `test_turn_intent_questions.py`.

### Task 3: Ordinary action policy replaces stage/plan gates (RED → GREEN)

**Files:** Modify `agent-core/project_mode.py` and `agent-core/tools/executor.py`; create/extend `agent-core/tests/test_ordinary_work_intent.py` and `test_project_documentation_stage.py`.

**Interface:** Extend `project_mode_block_reason(..., ordinary_work_intent="execute", ordinary_task_scope=None)`. In `_validate_project_mode_call`, pass current request intent/scope. The ordinary branch checks binding, resolved path, intent and action risk; the runaway branch keeps existing behavior.

- [ ] Add failing matrix tests: all six legacy stages × draft/confirmed/plan_dirty × light/standard return the same result for the same safe execute action; `plan_only` blocks source writes but permits planning artifacts; `read_only` denies writes/commands; unbound/out-of-root writes stay blocked; stale `project_active_task_id` does not narrow a new request.
- [ ] Run the tests and confirm failures are specifically stage/plan denials.
- [ ] Remove ordinary stage, plan-status, template, L2-stale, and plan-domain-only write denials for in-scope execute/plan-only requests; preserve project-root checks, plan-only source restrictions, PlanAgent proposal policy, and existing action-risk approval. Do not modify shared v1 dirty/adoption functions.
- [ ] Keep the runaway branch unchanged and run `test_runaway_startup.py`, `test_runaway_reliability.py`, and `test_runaway_v2_controller.py` targeted selections.

### Task 4: Remove stage gates from agent tools and prompt (RED → GREEN)

**Files:** Modify `agent-core/project_mode.py`, `agent-core/agent.py`; tests in `test_ordinary_work_intent.py` plus existing prompt/tool-filter tests.

- [ ] Add failing tests that ordinary overlays contain no documentation/design/plan hard-gate instructions, selected task scope is request-local, and Plan tools are optional rather than suppressed by sticky `project_entry`.
- [ ] Run tests to confirm failure.
- [ ] Update ordinary `format_project_overlay`, `build_llm_tools`, and direct-intent helpers to use the request intent; preserve all runaway overlays/suppression logic.
- [ ] Run the new tests and existing agent tool-filter/prompt tests.

### Task 5: Project commands and progress API become non-gating (RED → GREEN)

**Files:** Modify `agent-core/project_cli.py`, `agent-core/project_api.py`, `agent-core/context_switch.py`, `agent-core/project_mode.py`; tests in project CLI/API/context-switch, `test_ordinary_light_template.py`, and `test_project_stage.py`.

- [ ] Add failing tests proving organize/confirm-design/start-task/direct/plan-confirm do not advance ordinary `project_workflow_stage`; direct execution does not fake `project_plan_status=confirmed` or confirmation timestamps; explicit plan confirmation may still record approval.
- [ ] Add failing tests that ordinary progress snapshots are not used as permission and that verification-pending still allows code edits while publish/completion claims retain evidence/approval requirements.
- [ ] Run these tests and confirm the old stage writes/gates cause the failure.
- [ ] Update CLI/API behavior: stage fields are legacy/read-only in ordinary; plan confirmation is optional metadata; `needs_plan_confirm`, `needs_design_confirm`, and `needs_documentation` are nonblocking compatibility values; context-switch failure must not claim success by writing `confirmed`.
- [ ] Ensure project progress is presented from task/artifact/test facts; keep runaway snapshot/phase ownership intact. `execution_stage` may remain as a compatibility snapshot, but neither its blockers nor stage label may drive ordinary permissions or primary UI CTA.
- [ ] Re-run focused API/context-switch and project-stage tests.

### Task 6: Remove stage-based UI blockers (RED → GREEN)

**Files:** Modify `desktop/src/shells/unified/project-panel.ts`, `desktop/src/shells/unified/index.ts`, `desktop/src/api/ws.ts`; modify `desktop/tests/ordinary-mode.test.ts` and relevant project-panel tests.

- [ ] Add failing UI tests: draft/plan_dirty/design/documentation no longer produce blocking stage/plan cards; a pending verification does not hide ordinary implementation; task/test/evidence facts remain; direct/plan intent actions are request-scoped.
- [ ] From `desktop`, bundle `tests/ordinary-mode.test.ts` with `node_modules/.bin/esbuild` (`--bundle --platform=node --format=esm`) to a unique `%TEMP%` `.mjs`, then run `node --test <temp-file>` and confirm failures.
- [ ] Remove mandatory stage cards and plan-confirm blockers; Plan-only remains available by clear natural-language request (no sticky mode toggle); keep existing verification/release evidence and publish approval.
- [ ] Re-run the same esbuild-bundled Node test from `desktop`, then `npm run build`.

### Task 7: Docs, full regression, and audit

**Files:** Update `docs/PROJECT-MODE.md`, `docs/ORDINARY-FLOW-REDUCTION.md`, `docs/INTERACTION-REDESIGN.md`, and the approved spec’s implementation status only after verification.

- [ ] Update docs to make planning optional, stage labels non-authoritative, and action/path/risk policy the ordinary permission source.
- [ ] Run the complete focused Python selection: intent, direct gates, docs, light template, verification wrap-up, project stage, API/context switch, execution lifecycle.
- [ ] Run focused v1/v2 regression suites and confirm their old phase gates still work.
- [ ] Search ordinary callsites for `project_workflow_stage`, `project_plan_status`, `project_entry`, `needs_*_confirm`, stage/plan prompt gates, and UI blockers; classify every remaining read/write as legacy/display or runaway-only.
- [ ] Run `git diff --check`; review only task-owned hunks; leave all changes unstaged and uncommitted.

## Execution Notes

- Existing working tree has unrelated runaway, UI-copy, docs, and test changes. Preserve them; inspect hunks before editing shared files such as `agent.py` and `project-panel.ts`.
- The optional plan §9 item “light-bootstrap failure must not falsely write confirmed” is included only insofar as it is required to avoid a false success state; do not alter successful bootstrap behavior.
- No broad staging, commit, or worktree creation is authorized.