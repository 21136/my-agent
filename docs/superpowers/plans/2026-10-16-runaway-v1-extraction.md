# Runaway v1 Extraction (T-6242) Implementation Plan

> **For agentic workers:** Implement this approved plan task-by-task with TDD. Do not start R3/R4/R5 or commit changes.

**Goal:** Move v1-only runaway orchestration implementations from `agent.py` into lazy `runaway_v1` modules, preserve Agent method compatibility, and prevent v2 shared-loop hooks from entering v1 repair/checkpoint ownership.

**Architecture:** Keep Agent as the state/context object and preserve its existing method names/signatures as thin local-import wrappers. Split v1 implementation into verification and continuation modules. Shared parent-loop review/adoption stays in Agent, but v2 stores review metadata only and skips legacy checkpoint advancement after artifact reconciliation; v2 acceptance/continuation remains in `RunawayRuntime`.

**Tech Stack:** Python 3.14, pytest, existing Agent/Session/ToolExecutor and runaway v1/v2 modules.

**Spec:** `docs/superpowers/specs/2026-10-16-runaway-v1-extraction-design.md`.

## Global Constraints

- Preserve all existing dirty/staged/untracked work; do not reset, clean, broadly stage, or commit.
- Preserve v1 state format, user-visible v1 copy, and `patch.object(agent, ...)` behavior.
- Keep shared Agent/driver hooks and v2 product behavior out of `runaway_v1` imports.
- Use `_runaway_v1_orchestration_active(session)` for shared version-routing decisions.
- T-6242 acceptance requires v1 compatibility 112/112 plus R0 architecture closure; both gates were verified on 2026-10-16 (see RUNAWAY-ORCHESTRATION.md §6–8).
- On Windows, use `.venv\Scripts\python.exe`; print `sys.executable` before Python test runs.

---

### Task 1: Guard v2 review and proposal-adoption routes

**Files:**
- Modify: `agent-core/agent.py`
- Create: `agent-core/tests/test_runaway_v1_routing.py`

**Interfaces:** Add module helper `_runaway_v1_orchestration_active(session: object) -> bool`, true only when runaway is enabled and the v2 runtime is not active. `Agent._record_runaway_review_result` remains the public router: v1 delegates to the legacy implementation; v2 persists only advisory review fields. `_reconcile_runaway_tasks_after_plan` always reconciles TASKS artifacts; checkpoint advancement occurs only in v1 mode.

- [x] Add `test_v2_review_result_records_advisory_without_v1_repair`: call the real Agent method with v2 enabled and a successful review; assert review metadata is saved and hard verification, checkpoint transition, repair-pending, and repair-entry hooks are not called.
- [x] Run that test; confirm the current code enters legacy review/repair behavior and fails the new assertion.
- [x] Add `test_v2_proposal_reconcile_skips_legacy_checkpoint_advance`: patch artifact reconciliation to return unchanged, make checkpoint advancement observable, run shared reconciliation with v2 enabled, and assert artifact reconciliation runs but legacy advancement does not.
- [x] Run that test and confirm it fails on the current unconditional advancement.
- [x] Implement the single routing predicate and the v2 advisory/reconciliation branches; preserve the existing v1 branches.
- [x] Run the new routing tests and the complete v2 test selection.
- [x] Run the existing v1 review/proposal tests; included in the complete 112/112 compatibility selection.

### Task 2: Extract v1 verification and repair implementations

**Files:**
- Create: `agent-core/runaway_v1/__init__.py` (lightweight; no eager imports)
- Create: `agent-core/runaway_v1/verification.py`
- Modify: `agent-core/agent.py`
- Test: `agent-core/tests/test_runaway_v1_routing.py`, `test_runaway_flow.py`, `test_runaway_verification_orchestrator.py`, `test_project_artifacts.py`

**Interface:** Move `_transition_runaway_checkpoint`, `_matrix_repair_fingerprint`, `_enter_runaway_repair_checkpoint`, `_run_runaway_hard_verification`, `_spawn_runaway_bug_fix`, `_rerun_runaway_harness_verification`, `_bootstrap_runaway_verification_artifacts`, `_runaway_formal_queue_done`, `_sync_runaway_harness_truth`, `_promote_runaway_to_verifying_if_harness_green`, `_run_runaway_repair_lane`, `_maybe_run_runaway_repair_lane_at_turn_start`, `_maybe_finish_runaway_verification_exit_turn`, `_maybe_short_circuit_runaway_verification_harness_turn`, `_runaway_repair_pending`, and `_queue_runaway_documentation_repair` into module functions receiving the original Agent as their first parameter. Retain same-name Agent wrappers with original signatures; each wrapper imports its function locally. Keep `_pause_runaway`, shared presentation helpers, `_record_runaway_review_result` router, and v2 routing in Agent.

- [x] Add a lazy-import test proving a v1-only wrapper loads `runaway_v1.verification` only when called.
- [x] Run it and verify it fails before the package/wrapper exists.
- [x] Move the checkpoint/repair/hard-verification group (`_transition_runaway_checkpoint` through `_run_runaway_repair_lane`) into `runaway_v1.verification`; add wrappers with copied signatures and keep internal calls on `self`.
- [x] Run `test_runaway_verification_orchestrator.py`; compatibility selection passed, including instance `patch.object` behavior.
- [x] Move startup/verification-turn entry helpers (`_maybe_run_runaway_repair_lane_at_turn_start`, `_maybe_finish_runaway_verification_exit_turn`, `_maybe_short_circuit_runaway_verification_harness_turn`) into the same module with wrappers.
- [x] Move repair-state/doc helpers (`_runaway_repair_pending`, `_queue_runaway_documentation_repair`) into the same module.
- [x] Run `test_runaway_flow.py` and `test_project_artifacts.py` in the complete compatibility selection.

### Task 3: Extract v1 continuation, checkpoint, and startup implementations

**Files:**
- Create: `agent-core/runaway_v1/continuation.py`
- Modify: `agent-core/agent.py`
- Test: `agent-core/tests/test_runaway_v1_routing.py`, `test_runaway_flow.py`, `test_runaway_startup.py`

**Interface:** Move `_continue_runaway_after_natural_stop`, `_adopt_runaway_plan`, v1 implementation branches for `should_chain_runaway_after_turn`, `runaway_chain_skip_reason`, and `runaway_chain_user_line`, `_runaway_task_line_is_done`, `_runaway_active_task_is_done`, `_runaway_active_task_has_advance_evidence`, `_runaway_implementation_progressed`, `_advance_runaway_checkpoint`, `_maybe_run_runaway_startup_prep`, and `_prepare_runaway_project_start` into module functions. Add `should_continue_v1_execute_segment(agent, *, final_text, finish_reason, total_tool_rounds, total_max) -> bool` to preserve the current repair-pending → checkpoint-advance → natural-stop order and round-cap side effects. Keep public Agent routers and v2 branches in Agent. `_run_execute_segments` remains V1-only and calls this helper; v2 does not enter that method.

- [x] Add repair-order and round-cap tests for `should_continue_v1_execute_segment`; observe expected RED before implementation and GREEN after.
- [x] Review existing characterization coverage for natural-stop/checkpoint/server-chain/startup; existing `test_runaway_flow.py` and `test_runaway_startup.py` cases are included in the compatibility selection.
- [x] Run the existing v1 natural-stop/checkpoint/server-chain/startup characterization coverage after extraction; complete compatibility selection passed.
- [x] Move each method group behind lazy wrappers, preserving Agent method signatures and instance-patch behavior.
- [x] Run the new V1 chain-router tests and all V2 routing/kernel tests.
- [x] Run existing v1 continuation/startup characterization coverage via the complete 112/112 selection.

### Task 4: Prove package isolation and report gates

**Files:**
- Modify only if evidence requires: `docs/RUNAWAY-ORCHESTRATION.md`, `docs/TASKS.md`, `docs/MAP.md`

- [x] Add a fresh-process test that imports Agent and exercises the v2 entry/review/proposal path without loading `runaway_v1`; separately verify a v1-only wrapper lazily loads only the required module.
- [x] Verify v2 shared-loop review and proposal reconciliation never invoke v1 repair/checkpoint advancement; verify v1 paths still do.
- [x] Run new focused routing tests and all `agent-core/tests/test_runaway_v2_*.py` with `.venv`.
- [x] Run `tools/runaway_v1_compat_regression.py` to complete exit via a detached process: 112 passed, 14 subtests passed in 51.98s; exit code 0.
- [x] Report exact outputs; v1 112/112, R1 kernel 14/14, routing + all v2 79/79, compileall exit 0; T-6242 acceptance gates passed.
- [x] Review current documentation delta; preserve other dirty files; do not commit.
