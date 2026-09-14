# Runaway Production Blockers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development to implement each task with a failing regression test first.

**Goal:** Make runaway v2 converge or pause deterministically, preserve actionable pause state, and remove the tool and project-mode dead ends observed in the 0x567-flash production flow.

**Architecture:** Keep `runaway_v2/continuation.py` as the single continuation decision point. Persist a disk-derived project fingerprint plus no-progress and automatic-turn counters in `SessionMeta`; controller records one observation per model turn and server reads the recorded decision without double-counting it. Pause through the existing checkpoint state machine and expose the reason through v2 state payloads.

**Tech Stack:** Python 3.12, `unittest`, existing Agent/ToolExecutor runtime, TOML tool manifests, Windows PowerShell validation.

**Spec:** `docs/RUNAWAY-V2-CONTINUATION.md` and `docs/RUNAWAY-V2.md`.

## Global Constraints

- Preserve unrelated user changes in the dirty worktree.
- Use `.venv\\Scripts\\python.exe` when present for Python validation.
- `MY_AGENT_RUNAWAY_V2=0` keeps the legacy path unchanged.
- Every production behavior change gets a regression test that was observed failing before implementation.
- A paused runaway session remains visible as paused until an explicit resume clears the guard counters.

---

### Task 1: Persisted continuation guard

**Files:**
- Modify: `agent-core/runaway_v2/config.py`
- Modify: `agent-core/runaway_v2/continuation.py`
- Modify: `agent-core/session.py`
- Modify: `agent-core/tests/test_runaway_v2_continuation.py`
- Modify: `agent-core/tests/test_runaway_v2_state.py`

- [ ] Add tests for a stable project fingerprint, repeated unchanged state pausing, automatic-turn budget pausing, and reset on resume.
- [ ] Run the focused tests and confirm they fail because no persisted guard exists.
- [ ] Implement bounded environment-configured thresholds, deterministic project fingerprinting, one-turn observation, and pause through the existing checkpoint state machine.
- [ ] Run the focused tests and confirm they pass.

### Task 2: v2 controller and prompts

**Files:**
- Modify: `agent-core/runaway_v2/plan.py`
- Modify: `agent-core/runaway_v2/controller.py`
- Modify: `agent-core/agent.py`
- Modify: `agent-core/project_api.py`
- Modify: `agent-core/server.py`
- Modify: `agent-core/tests/test_runaway_v2_controller.py`
- Modify: `agent-core/tests/test_runaway_v2_state.py`

- [ ] Add tests proving implementation can write `VERIFY.md`, receives explicit test/evidence/`report_progress` instructions, and that server/controller checks do not double-count one turn.
- [ ] Run the focused tests and confirm they fail.
- [ ] Add the implementation scope and prompt contract, record continuation only in the controller, make the server read-only for that decision, and preserve paused reasons in state payloads.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Tool and project-mode dead ends

**Files:**
- Modify: `evolve/tools/common/run_command/main.py`
- Modify: `evolve/tools/common/run_command/tool.toml`
- Modify: `agent-core/project_mode.py`
- Modify: `agent-core/tests/test_run_command.py`
- Modify: `agent-core/tests/test_project_build_guards.py`

- [ ] Add tests for a matching expected non-zero exit code and v2 coding on an L2-stale manifest.
- [ ] Run the focused tests and confirm they fail.
- [ ] Implement `expected_exit_code` as a first-class command contract and exempt v2's active project lane from the contradictory stale-plan block while retaining the stale diagnostic.
- [ ] Run the focused tests and confirm they pass.

### Task 4: Verification

**Files:**
- Modify: `docs/RUNAWAY-V2-CONTINUATION.md`
- Modify: `docs/RUNAWAY-V2.md` only if the continuation contract needs a matching summary.

- [ ] Run the focused runaway/tool tests with the repository venv and inspect failures.
- [ ] Run the complete Python test suite.
- [ ] Run `tools/e2e_0x567_flash.py` against the real 0x567-flash gateway, including the maintenance/bug-fix flow.
- [ ] Record actual results and remaining environmental limitations; do not claim completion from unit tests alone.
