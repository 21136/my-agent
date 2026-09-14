# Execution Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement each task with a failing regression test first.

**Goal:** Give each in-process Agent execution one authoritative, observable, idempotently stoppable lifecycle.

**Architecture:** Add a small lock-protected `ExecutionLifecycle` state machine. `WsBridge` owns it and translates lifecycle transitions into `execution.state` events; `_run_line` and runaway continuation paths use the bridge as their sole execution coordinator while preserving existing cancellation and finish-reason behavior. Desktop and Terminal consume the event without replacing their existing turn protocol.

**Tech Stack:** Python 3.14 virtualenv, `unittest`, existing WebSocket bridge, TypeScript/Vite desktop shell, Ink terminal reducer.

**Spec:** `docs/superpowers/specs/2026-09-11-execution-lifecycle-design.md`

## Global Constraints

- Preserve unrelated dirty-worktree changes.
- Use `.venv\\Scripts\\python.exe` for Python validation.
- Do not add a third-party dependency.
- `turn.start` and `turn.end` remain backward-compatible.
- Stop requests must be idempotent and must not create a second Agent cancellation callback.
- A queued runaway continuation must not activate after cancellation.

---

### Task 1: Pure lifecycle contract

**Files:**
- Create: `agent-core/execution_lifecycle.py`
- Create: `agent-core/tests/test_execution_lifecycle.py`

**Interfaces:**
- `ExecutionLifecycle.start_or_activate(mode: str) -> ExecutionSnapshot`
- `ExecutionLifecycle.enqueue(mode: str) -> ExecutionSnapshot`
- `ExecutionLifecycle.request_stop(reason: str = "user") -> tuple[ExecutionSnapshot | None, bool]`
- `ExecutionLifecycle.finish(finish_reason: str, ok: bool, final_state: str | None = None) -> tuple[ExecutionSnapshot | None, bool]`
- `ExecutionLifecycle.snapshot() -> ExecutionSnapshot | None`

- [ ] Write tests for running start, queued activation, stop idempotency, finish idempotency, and cancelled queued activation.
- [ ] Run the focused test file and observe the expected import/API failures.
- [ ] Implement the minimal lock-protected state machine and immutable snapshot serialization.
- [ ] Run the focused test file and verify all lifecycle cases pass.

### Task 2: Bridge ownership and exactly-once finalization

**Files:**
- Modify: `agent-core/server.py`
- Modify: `agent-core/tests/test_turn_cancel.py`

**Interfaces:**
- `WsBridge.begin_turn(runaway: bool = False) -> bool`
- `WsBridge.begin_continuation(mode: str = "runaway") -> bool`
- `WsBridge.finish_execution(finish_reason: str, ok: bool) -> bool`
- `WsBridge.emit_turn_event(event: dict[str, Any]) -> None`

- [ ] Add failing tests for `execution.state`, exactly-once finish, duplicate stop callback suppression, and queued continuation cancellation.
- [ ] Run those tests and verify they fail before bridge integration.
- [ ] Route `_run_line`, `_chain_runaway_after_turn`, `_resume_runaway`, and the project resume path through lifecycle begin/queue/finalize methods.
- [ ] Make `turn.start`/`turn.end` carry the active `run_id` when the bridge owns the event.
- [ ] Run cancellation and runaway regression tests.

### Task 3: Desktop and Terminal consumers

**Files:**
- Modify: `desktop/src/api/ws.ts`
- Modify: `desktop/src/shells/chat-state.ts`
- Modify: `desktop/src/shells/unified/index.ts`
- Modify: `terminal-ui/src/reduce/events.ts`
- Modify: `terminal-ui/src/types.ts`
- Modify: `terminal-ui/tests/reduce-events.test.ts`

- [ ] Add reducer tests proving queued and stopping states remain working and terminal states clear the active execution.
- [ ] Extend the event types and reducers with sequence/run identity checks.
- [ ] Show lifecycle state through existing status/working surfaces without adding a second stop button.
- [ ] Run terminal UI tests and desktop build.

### Task 4: End-to-end verification and docs

**Files:**
- Modify: `docs/INTERACTIVE-TERMINAL-UI.md`
- Modify: `docs/RUNAWAY-V2-CONTINUATION.md`
- Modify: `docs/TASKS.md`

- [ ] Document `execution.state`, ownership, and the cancellation acceptance matrix.
- [ ] Run focused Python tests with the repository venv.
- [ ] Run the broader cancellation, runaway, terminal, and harness regressions.
- [ ] Run the Desktop and Terminal builds/tests.
- [ ] Inspect the final worktree and report residual gaps without claiming service-restart recovery is solved.
