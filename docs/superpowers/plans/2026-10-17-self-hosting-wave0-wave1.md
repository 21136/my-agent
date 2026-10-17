# Self-hosting Wave 0/1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Establish a reliable main-project verification entrypoint and implement the first safe self-modification transaction primitives without touching remote deployment or automatic merge.

**Architecture:** Wave 0 uses an explicit main-project test runner rather than a global pytest configuration that could hijack independent projects under `workspace/`. Wave 1 adds a transaction state model and controlled local snapshot/branch/verification/rollback orchestration; it reuses existing Git tools and Executor confirmation instead of creating a second Git implementation.

**Tech Stack:** Python 3.14, `.venv\Scripts\python.exe`, pytest, existing `ToolRegistry`/`ToolExecutor`, existing Git evolved tools, JSON transaction records under Agent data.

**Spec:**
- `F:\my-agent-main\docs\SELF-HOSTING-ARCHITECTURE.md`
- `F:\my-agent-main\docs\SELF-MODIFICATION-TRANSACTION.md`
- `F:\my-agent-main\docs\TEST-SCOPE-AND-BASELINE.md`

## Global Constraints

- Preserve unrelated dirty/staged work; no reset, clean, checkout, broad staging, commit, push, or overwrite without explicit user approval.
- Use `F:\my-agent-main\.venv\Scripts\python.exe` for Python validation.
- Do not modify or delete `workspace/` example projects to hide collection errors.
- Do not add a root pytest config that causes nested standalone projects to inherit the wrong test path.
- Existing `git_snapshot`, `git_diff`, `git_branch`, `git_restore`, `git_commit`, and `git_push` remain the Git operation owners.
- Wave 1 must not auto-push, auto-merge, release, deploy, or replace the running Host Agent.
- Secrets must not enter transaction records, tool output, prompts, or logs.

---

### Task 1: Add an explicit main-project verification entrypoint

**Files:**
- Create: `F:\my-agent-main\tools\run_main_tests.py`
- Modify: `F:\my-agent-main\docs\TEST-SCOPE-AND-BASELINE.md`
- Modify: `F:\my-agent-main\docs\README.md`
- Test: `F:\my-agent-main\agent-core\tests\test_project_verify.py` or the nearest existing project-test command test

**Interfaces:**
- CLI: `python tools/run_main_tests.py --scope focused|core|collect`
- Default `--scope core` executes only `agent-core/tests`.
- Output is JSON with `scope`, `cwd`, `python`, `command`, `exit_code`, `passed`, `failed`, `skipped`, `collection_errors`, and `status`.

- [x] **Step 1: Write the failing runner contract test**

Create a temporary subprocess test that invokes the runner with `--scope collect` and asserts that its command contains `agent-core/tests` and does not include `workspace`.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_project_verify.py
```

Expected: FAIL because `tools/run_main_tests.py` does not exist or does not expose the JSON contract.

- [x] **Step 3: Implement the smallest explicit runner**

Use `subprocess.run` with:

```text
cwd = repository root
python = sys.executable
scope=core   -> python -m pytest -q agent-core/tests
scope=collect -> python -m pytest --collect-only -q agent-core/tests
scope=focused -> require explicit test paths after --; never discover workspace
```

Parse pytest summary conservatively. If parsing is ambiguous, return `status="completed_unknown"` rather than claiming pass.

- [x] **Step 4: Run the contract test and verify GREEN**

Expected: the runner uses the repository’s `.venv` interpreter, never recursively collects `workspace/`, and returns structured evidence.

- [x] **Step 5: Run the runner against the current tree**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe tools\run_main_tests.py --scope collect
```

Record the collection result separately from the known existing core-suite failures.

### Task 2: Add the self-change transaction state model

**Files:**
- Create: `F:\my-agent-main\agent-core\self_change.py`
- Test: `F:\my-agent-main\agent-core\tests\test_self_change.py`
- Modify: `F:\my-agent-main\docs\SELF-MODIFICATION-TRANSACTION.md`

**Interfaces:**

```python
class SelfChangeState(StrEnum):
    REQUESTED = "requested"
    SCOPED = "scoped"
    SNAPSHOTTED = "snapshotted"
    BRANCHED = "branched"
    EDITING = "editing"
    LOCAL_VERIFYING = "local_verifying"
    CANDIDATE_READY = "candidate_ready"
    ROLLED_BACK = "rolled_back"
    ABORTED = "aborted"
    BLOCKED = "blocked"

@dataclass(frozen=True)
class SelfChangeTransaction:
    transaction_id: str
    run_id: str
    source_root: str
    target_root: str
    base_ref: str
    work_branch: str
    owned_paths: tuple[str, ...]
    baseline_hashes: dict[str, str]
    changed_paths: tuple[str, ...]
    rollback_target: str
    state: SelfChangeState
    failure_reason: str = ""
```

- [x] **Step 1: Write RED tests for transitions and serialization**

Cover:

- valid `requested → scoped → snapshotted → branched → editing → local_verifying → candidate_ready`;
- invalid transition rejection;
- `rolled_back` requires a rollback target and failure reason;
- JSON round-trip preserves identity and paths;
- secrets-like values are rejected from metadata fields.

- [x] **Step 2: Run the focused test and verify RED**

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_self_change.py
```

- [x] **Step 3: Implement the pure state model**

Keep it side-effect free. Put filesystem/Git actions in later adapters. Use deterministic transition validation and JSON-safe values only.

- [x] **Step 4: Run GREEN and compile**

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_self_change.py
F:\my-agent-main\.venv\Scripts\python.exe -m py_compile agent-core/self_change.py
```

### Task 3: Add baseline capture and transaction persistence

**Files:**
- Modify: `F:\my-agent-main\agent-core\self_change.py`
- Modify: `F:\my-agent-main\agent-core\paths.py` only if a dedicated data path helper is required
- Test: `F:\my-agent-main\agent-core\tests\test_self_change.py`

**Interfaces:**

```python
def capture_baseline(
    *,
    root: Path,
    owned_paths: Iterable[str],
    base_ref: str = "HEAD",
) -> BaselineSnapshot: ...

def save_transaction(paths: AgentPaths, tx: SelfChangeTransaction) -> Path: ...
def load_transaction(paths: AgentPaths, transaction_id: str) -> SelfChangeTransaction: ...
```

- [x] **Step 1: Add RED tests for dirty-path classification**

Use a temporary Git repository and assert:

- owned tracked files receive hashes;
- unrelated dirty files are listed as pre-existing and not included in owned hashes;
- untracked files are reported, not deleted;
- transaction JSON is written under Agent data, not the target project.

- [x] **Step 2: Implement baseline capture and persistence**

Use read-only Git commands. Normalize paths to repository-relative POSIX paths. Never store file contents or secrets in the transaction record.

- [x] **Step 3: Run focused tests**

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_self_change.py
```

### Task 4: Add controlled branch/snapshot/rollback adapters

**Files:**
- Modify: `F:\my-agent-main\agent-core\self_change.py`
- Test: `F:\my-agent-main\agent-core\tests\test_self_change.py`
- Modify: `F:\my-agent-main\docs\SELF-MODIFICATION-TRANSACTION.md`

**Interfaces:**

```python
def begin_transaction(..., dry_run: bool = True) -> SelfChangeTransaction: ...
def prepare_branch(tx: SelfChangeTransaction, *, confirm: bool) -> SelfChangeTransaction: ...
def verify_owned_paths(tx: SelfChangeTransaction) -> tuple[str, ...]: ...
def rollback_transaction(tx: SelfChangeTransaction, *, confirm: bool) -> SelfChangeTransaction: ...
```

- [x] **Step 1: Write RED tests with a disposable Git repository**

Cover:

- dry-run branch preparation does not change Git state;
- confirmed branch creation uses a `codex/self-change/<slug>` name;
- a changed non-owned path blocks the transaction;
- expected-hash mismatch blocks rollback;
- confirmed rollback restores only owned paths;
- unrelated dirty files remain untouched.

- [x] **Step 2: Implement one adapter at a time**

Reuse the existing Git evolved tool implementations or shared safe helpers; do not invoke shell strings from the model. Every write operation must pass through confirmation and expected-hash checks.

- [x] **Step 3: Run the disposable-repository tests**

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_self_change.py
```

### Task 5: Add local verification and candidate health checks

**Files:**
- Modify: `F:\my-agent-main\agent-core\self_change.py`
- Test: `F:\my-agent-main\agent-core\tests\test_self_change.py`
- Modify: `F:\my-agent-main\docs\SELF-HOSTING-VERIFICATION.md`

**Interfaces:**

```python
def run_local_verification(tx: SelfChangeTransaction, *, commands: tuple[tuple[str, ...], ...]) -> VerificationSummary: ...
def check_candidate_health(*, command: tuple[str, ...], cwd: Path, timeout_sec: float) -> HealthCheck: ...
```

- [x] **Step 1: Write RED tests**

Cover:

- successful command records cwd, command, exit code and output summary;
- failed command enters `blocked`/`aborted`, not `candidate_ready`;
- health timeout is a failure;
- health output cannot be replaced by a model text claim;
- command output is truncated and secret-like values are redacted.

- [x] **Step 2: Implement local verification and health adapters**

Do not replace the currently running Host Agent in this task. Candidate launch is limited to a disposable command/process with explicit timeout and cleanup.

- [x] **Step 3: Run focused tests and compile**

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_self_change.py
F:\my-agent-main\.venv\Scripts\python.exe -m py_compile agent-core/self_change.py
```

### Task 6: Integrate documentation and run acceptance

**Files:**
- Modify: `F:\my-agent-main\docs\README.md`
- Modify: `F:\my-agent-main\docs\MAP.md`
- Modify: `F:\my-agent-main\docs\TASKS.md`
- Modify: `F:\my-agent-main\docs\SELF-HOSTING-ROADMAP.md`

- [ ] **Step 1: Add navigation and task references**

Link D0–D7 under a single self-hosting section. Add explicit Wave 0/Wave 1 task IDs without changing unrelated task status.

- [x] **Step 2: Run main-project collection**

```powershell
F:\my-agent-main\.venv\Scripts\python.exe tools\run_main_tests.py --scope collect
```

Expected: no `workspace/` collection errors.

- [x] **Step 3: Run focused self-change and Skill Provider tests**

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_self_change.py agent-core/tests/test_skill_registry.py agent-core/tests/test_skill_prompt_integration.py
```

- [x] **Step 4: Run the real 0x567-flash smoke acceptance**

```powershell
F:\my-agent-main\.venv\Scripts\python.exe tools\e2e_0x567_flash.py --maintenance-only --turn-timeout 120
```

Expected: temporary-root E2E only; report actual status without claiming full repository green if unrelated core failures remain.
