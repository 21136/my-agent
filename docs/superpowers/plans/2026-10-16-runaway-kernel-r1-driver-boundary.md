# Runaway Kernel R1 Driver-Boundary Closure Implementation Plan

> **For agentic workers:** Follow the approved `docs/RUNAWAY-ORCHESTRATION.md` R1 scope. Implement task-by-task with TDD; do not start R2 or alter v1 routing.

**Goal:** Make the v2 orchestration runtime use its injected `AgentDriver` for session preparation and executor-owned runtime context, without changing turn semantics.

**Architecture:** Keep project/session decisions in `RunawayRuntime` and `RunawayProjectSync`. Move agent/executor operations behind explicit `AgentDriver` methods; `AgentDriverAdapter` remains the production bridge. Preserve current `Agent.run_turn` and `RunawayController` compatibility entry points.

**Tech Stack:** Python 3.14, pytest, existing `AgentDriver` Protocol and adapter.

**Spec:** `docs/RUNAWAY-ORCHESTRATION.md` §4–7; Phase 62 T-6241 in `docs/TASKS.md`.

## Global Constraints

- Preserve existing user changes; do not reset, clean, broadly stage, or commit.
- Do not change product phase/checklist/acceptance/continuation semantics.
- Do not migrate v1 modules or mark T-6241/T-6240 complete without their full gates.
- Use `.venv\Scripts\python.exe` on Windows and verify `sys.executable` before Python validation.

---

### Task 1: Route session preparation through AgentDriver

**Files:**
- Modify: `agent-core/runaway_v2/kernel/ports.py`
- Modify: `agent-core/runaway_v2/kernel/driver_adapter.py`
- Modify: `agent-core/runaway_v2/kernel/runtime.py`
- Test: `agent-core/tests/test_runaway_v2_kernel.py`

**Interface:** Add `AgentDriver.prepare_session_for_turn() -> None`. The adapter ensures messages are loaded and invokes the existing `prepare_session_for_s4(session)` helper. Runtime takes its session from the injected driver and calls this port method. The default Agent-backed entry remains unchanged.

- [x] Add a test asserting an injected driver owns session preparation and supplies the runtime session.
- [x] Run the focused test and confirm it fails because the driver port is absent/not used.
- [x] Add the Protocol method and adapter implementation; route runtime preparation/session access through the driver.
- [x] Run focused kernel tests and confirm pass.

### Task 2: Hide executor session mutation behind driver methods

**Files:**
- Modify: `agent-core/runaway_v2/kernel/ports.py`
- Modify: `agent-core/runaway_v2/kernel/driver_adapter.py`
- Modify: `agent-core/runaway_v2/kernel/project_sync.py`
- Test: `agent-core/tests/test_runaway_v2_kernel.py`

**Interface:** Add `AgentDriver.apply_runaway_execution_context(*, write_scope: list[str] | None, forbid_plan_partner: bool, workflow_stage: str) -> None` and `clear_runaway_runtime_workflow_stage() -> None`. The adapter owns writes to `executor.session`; `RunawayProjectSync` calls only these methods and no longer requires an `executor` property on the protocol.

- [x] Add a fake-driver test that plan application and runtime mirroring call these port methods with expected values.
- [x] Run the focused test and confirm it fails because the methods are absent/not used.
- [x] Implement the Protocol methods and adapter behavior; remove direct executor access from project sync.
- [x] Run focused kernel and controller tests.

### Task 3: Validate behavior and report remaining gates

**Files:**
- Review/update: `docs/RUNAWAY-ORCHESTRATION.md` §4 and §6 only if the documented boundary status changed.

- [x] Verify the new code has no direct `prepare_session_for_s4` or `executor.session` access in `kernel/runtime.py` and `kernel/project_sync.py`.
- [x] Run all `agent-core/tests/test_runaway_v2_*.py` with the repository venv and report exact counts.
- [x] Leave v1 112-test compatibility verification and real transport Stop timing explicitly open if not proven.
