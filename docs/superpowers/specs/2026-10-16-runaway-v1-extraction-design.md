# Runaway v1 Extraction and v2 Routing Design

**Status:** Approved for implementation (review edits incorporated)  
**Date:** 2026-10-16  
**Parent design:** `docs/RUNAWAY-ORCHESTRATION.md` §6 (T-6242)  
**Scope:** R2 only; this document does not authorize R3/R4/R5 or v1 package completion without the stated regression gate.

## Goal

Move v1-only runaway orchestration implementations out of `agent.py` into a lazily imported `runaway_v1/` package, while preserving the existing `Agent` method surface and keeping v2 continuation/checkpoint ownership in `RunawayRuntime`.

## Context and constraints

- `Agent` is the state/context object used by the existing v1 methods; those methods call one another through `self` and read/write Agent, Session, and executor state.
- Existing v1 tests frequently patch methods on an `Agent` instance with `patch.object`. Moving methods onto a separate controller object would bypass some of those overrides and introduce behavioral risk.
- The v2 product entry returns to `RunawayRuntime`. The legacy `_run_execute_segments` checkpoint/repair/natural-stop chain is called only by the v1 `Agent.run_turn` path, which v2 returns before. Actual v2 leaks through the shared parent tool loop's review-result router and proposal reconciliation.
- `AgentDriverAdapter` uses several Agent hooks in both versions. `_pause_runaway`, proposal adoption/flush/query, turn-mode synchronization, event emission, and the shared parent loop remain on `Agent` in this task unless an explicit later task moves their contract.
- Proposal adoption currently calls `_reconcile_runaway_tasks_after_plan`, which may advance a v1 checkpoint. The v1 path must retain this behavior; the v2 path must keep artifact reconciliation but skip that legacy advancement and let the kernel re-derive state from disk.
- Preserve the user’s uncommitted work. Do not reset, clean, broadly stage, or commit.

## Decision

Use **module-level implementations that receive the existing Agent instance**, with **thin, signature-preserving Agent wrappers** that import the implementation locally. This keeps `self` identity and instance-level test patches intact while making v1 implementation imports lazy. Do not use a mixin (eager import) or a controller proxy (changes method lookup and patch behavior).

Keep `runaway_v1/__init__.py` lightweight. Import implementation modules only from the v1 wrapper/dispatch path; importing `agent` or exercising a v2 turn/review/proposal path must not import `runaway_v1`. Use one predicate, `_runaway_v1_orchestration_active(session) := project runaway enabled and not runaway_v2_enabled(session)`, for shared-loop routing decisions; do not scatter slightly different V1/V2 conditions across call sites.

## Package and method ownership

Create `agent-core/runaway_v1/` and group implementations by workflow responsibility:

- `verification.py`: v1 verification harness entry/exit, hard verification, repair-lane orchestration, checkpoint reconciliation helpers, and v1 review-to-repair decision logic.
- `continuation.py`: v1 natural-stop continuation, v1 server-chain decision/text, task-progress/checkpoint advancement, early-project startup preparation, and the V1 execute-segment repair → advance → natural-stop ordering.

`agent.py` retains wrappers with the current method names and signatures. Each wrapper delegates with the original Agent instance as the first argument. Internal calls in migrated functions continue to call `self.<method>()`, so existing overrides and `patch.object(agent, ...)` remain observable.

Keep these shared hooks in `Agent` for R2: `_pause_runaway`, `_sync_turn_mode`, `_sync_allowed_evolved`, `_emit_turn_event`, `_run_parent_tool_loop`, proposal adoption/flush/query methods, and `runaway_v2` routing. Also keep `_apply_runaway_pause_message` and `_runaway_harness_segment_summary` in Agent: the shared loop uses them for final user-facing output in either version. `_record_runaway_review_result` remains a router: v1 delegates review-to-repair decisions to `runaway_v1`; v2 only persists the advisory result and leaves hard acceptance to `RunawayRuntime`. The v2 branch must not call `_run_runaway_hard_verification`, `_transition_runaway_checkpoint`, `_runaway_repair_pending`, or `_enter_runaway_repair_checkpoint`. `_adopt_runaway_proposals` remains shared; split post-adoption work into (a) artifact reconciliation, retained for both versions, and (b) legacy checkpoint advancement, v1-only. The v2 path skips only (b) and lets the kernel re-derive phase from disk. If a method is used by both v1 and v2, retain it in Agent or explicitly split its shared portion; do not put an unconditional lazy import in a v2-used wrapper.

## Call-site inventory

| Call site | V1 behavior | V2 behavior | Migration rule |
|---|---|---|---|
| `Agent.run_turn` | Enters the legacy turn path after the existing v2 early return | Returns to `RunawayRuntime` before legacy turn setup | V1-only helpers may be lazily delegated from this path |
| `Agent._run_execute_segments` | Runs checkpoint advance, repair-pending, and natural-stop continuation | Not reached; v2 `Agent.run_turn` returns to `RunawayRuntime` first | Delegate the V1-only ordered decision to `runaway_v1.continuation.should_continue_v1_execute_segment`; preserve order and budget semantics |
| Shared `Agent._run_parent_tool_loop` | Calls review router and proposal adoption | Reached through `AgentDriverAdapter.run_parent_tool_loop` | Keep shared execution; make review routing and proposal checkpoint side effects version-aware |
| `deliverable_review` result | Persist review, then legacy hard verify/repair/checkpoint behavior | Persist review metadata only; no legacy repair/checkpoint calls | Router remains in Agent; legacy implementation is lazy V1 |
| Proposal adoption | Reconcile TASKS and advance legacy checkpoint | Keep artifact reconciliation; skip legacy checkpoint advance | Split reconciliation from advancement; V2 kernel re-derives phase |
| Final pause/empty-text presentation | Existing message behavior | Shared-loop presentation remains compatible | Keep `_apply_runaway_pause_message` and `_runaway_harness_segment_summary` in Agent |
| Server chain decision/text | Legacy decision/message | Existing V2 continuation-based branch | Keep public Agent methods as routers; only V1 fallback imports package |

**Routing regression cases:** `test_v2_review_result_records_advisory_without_v1_repair`, `test_v2_proposal_reconcile_skips_legacy_checkpoint_advance`, v1 `_run_execute_segments` behavior regressions, and a fresh-process `test_v2_entry_does_not_import_runaway_v1`.

## Method ownership inventory

| Current Agent method(s) | Target | Agent surface |
|---|---|---|
| `_transition_runaway_checkpoint`, `_matrix_repair_fingerprint`, `_enter_runaway_repair_checkpoint`, `_run_runaway_hard_verification`, `_spawn_runaway_bug_fix`, `_rerun_runaway_harness_verification`, `_bootstrap_runaway_verification_artifacts`, `_runaway_formal_queue_done`, `_sync_runaway_harness_truth`, `_promote_runaway_to_verifying_if_harness_green`, `_run_runaway_repair_lane`, `_maybe_run_runaway_repair_lane_at_turn_start`, `_maybe_finish_runaway_verification_exit_turn`, `_maybe_short_circuit_runaway_verification_harness_turn`, `_runaway_repair_pending`, `_queue_runaway_documentation_repair` | `runaway_v1.verification` | Same-name thin wrappers retained |
| `_continue_runaway_after_natural_stop`, `_adopt_runaway_plan`, v1 implementation branches for `should_chain_runaway_after_turn`, `runaway_chain_skip_reason`, `runaway_chain_user_line`, `_runaway_task_line_is_done`, `_runaway_active_task_is_done`, `_runaway_active_task_has_advance_evidence`, `_runaway_implementation_progressed`, `_advance_runaway_checkpoint`, `_maybe_run_runaway_startup_prep`, `_prepare_runaway_project_start`, V1 execute-segment continuation order | `runaway_v1.continuation` | Same-name thin wrappers or V1-only implementation functions; V2 branches remain in Agent |
| `_record_runaway_review_result`, `_adopt_runaway_proposals`, `_reconcile_runaway_tasks_after_plan` | Split router/shared method | Keep Agent router/shared artifact reconciliation; delegate only V1 decisions and guard checkpoint advancement |
| `_pause_runaway`, `_sync_turn_mode`, `_sync_allowed_evolved`, `_emit_turn_event`, proposal flush/query/adoption, `_apply_runaway_pause_message`, `_runaway_harness_segment_summary` | Agent shared boundary | Remain implemented in Agent for R2 |

---

## v2 routing rule

When `runaway_v2_enabled(session)` is true:

1. V1 `_run_execute_segments` is not entered; its repair-pending, checkpoint-advance, and natural-stop continuation implementations can be migrated without adding v2 guards to that v1-only loop.
2. `deliverable_review` metadata may still be recorded, but v2 must not route that review into the v1 repair/checkpoint controller. The post-loop v2 acceptance path remains authoritative.
3. In the shared proposal-adoption path, task-artifact reconciliation remains available to both versions; v1 retains checkpoint advancement, while v2 skips that legacy side effect and relies on the kernel's existing post-loop disk reload/phase derivation.
4. Shared-loop presentation helpers (`_apply_runaway_pause_message` and `_runaway_harness_segment_summary`) stay in Agent and remain behavior-compatible; they are not v1 control owners.
5. Server chain decision/text methods keep their existing public Agent API; their v2 branches use v2 continuation/state and do not import `runaway_v1`.

When v2 is disabled, preserve current v1 behavior and continue to lazy-load the corresponding `runaway_v1` implementation.

## Compatibility and non-goals

- No changes to v1 checkpoint schema, persisted session fields, project artifact formats, or tool schemas. Existing v1 user-facing text stays unchanged; v2 stops emitting v1 review/repair control messages and relies on the v2 kernel acceptance path.
- No R2 move of shared Agent/executor primitives used by `AgentDriverAdapter`.
- No v1/v2 state migration, new runtime/plugin API, or policy change to product phases/checklists.
- Do not mark T-6242 done based only on structural tests; the documented v1 112-test regression remains a completion gate.
- Real transport Stop acceptance is tracked separately under T-6245.

## Verification and acceptance

1. Add focused tests before each extraction slice. Verify them failing before implementation.
2. Existing `test_runaway_flow.py`, `test_runaway_verification_orchestrator.py`, `test_runaway_startup.py`, `test_project_artifacts.py`, and v1-specific tests retain behavior and instance-patch semantics.
3. Add a fresh-process import-isolation test: importing Agent and exercising the v2 entry, review result path, and shared proposal-adoption path does not load `runaway_v1`; invoking a v1-only path loads only the needed implementation module.
4. Add v2 shared-parent-loop regressions proving review metadata and proposal artifact reconciliation remain available without entering v1 repair/checkpoint advancement; separately run V1 execute-segment characterization tests to preserve legacy continuation behavior.
5. Run all v2 tests and `tools/runaway_v1_compat_regression.py` with the repository `.venv`. T-6242 can be accepted only after the v1 regression yields 112/112 and the R0 architecture gate is satisfied; both conditions were verified on 2026-10-16.

## Risks and mitigations

- **Missed v1 callers:** retain Agent wrappers and use repository-wide symbol search plus the v1 regression.
- **v2 still enters legacy control:** guard the shared review and proposal-adoption call sites; use a fresh-process import test plus behavior tests. Do not add guards to the V1-only `_run_execute_segments` path.
- **Test patch semantics change:** keep methods on Agent as wrappers and pass the original Agent to module functions.
- **Circular imports:** v1 modules use local imports for heavyweight collaborators and `TYPE_CHECKING` for Agent annotations; `runaway_v1/__init__.py` performs no eager imports.

## Alternatives considered

- **Controller proxy:** clearer object ownership, but changes method lookup, state proxying, and monkeypatch behavior; rejected for this migration.
- **Mixin:** smallest extraction diff, but imports v1 implementation whenever `Agent` is imported; rejected because it defeats lazy isolation.
- **Full explicit driver/context rewrite:** cleanest long-term boundary, but far larger than R2 and increases risk to frozen v1 behavior; deferred.
