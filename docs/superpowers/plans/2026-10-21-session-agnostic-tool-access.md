# Session-Agnostic Tool Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Make every registered builtin, flat proxy, and active evolved tool available across ordinary and project sessions without weakening per-tool target, confirmation, request-intent, or host-scope controls.

**Architecture:** Remove tool-schema filtering based only on project binding or ordinary/project shell. Apply request-intent checks independently of shell type, and let project-specific review target a validated workspace project explicitly when the caller is unbound. Preserve ask-mode restrictions and all tool-specific confirmation and path policies.

**Tech Stack:** Python 3.12+, unittest, my-agent `ToolExecutor` / `ToolRegistry`, Markdown documentation.

**Spec:** `docs/superpowers/specs/2026-10-21-session-agnostic-tool-access-design.md`

## Global Constraints

- Run Python validation with `.venv\Scripts\python.exe`; verify `sys.executable` and imports first.
- Do not overwrite, revert, stage, or clean the existing dirty files in this checkout.
- In `agent` mode, ordinary and project sessions expose the same complete builtin/proxy list; evolved execution remains all and only `status=active`.
- Respect the current repository `AGENTS.md`: prefer focused regression tests; do not repeat a costly full suite when the targeted suite passes.
- Preserve ask-mode restrictions, read-only/plan-only side-effect checks, confirmation policy, project-root checks, HostScope authorization, and API-key secrecy. Project-session `write_evolve` must receive a fresh visible confirmation on every call.
- An unbound `deliverable_review` may target only a normalized project under `workspace/`; a bound session may not silently review another project.
- Keep `evolve/tool-catalog/INDEX.md` at or below 2,048 bytes.

---

### Task 1: Expose the same tool definitions in ordinary and project sessions

**Files:**
- Modify: `agent-core/agent.py::build_llm_tools`
- Create: `agent-core/tests/test_session_agnostic_tool_access.py`
- Modify: `agent-core/tests/test_generate_image_tool_integration.py`

**Interfaces:**
- `build_llm_tools(session, registry=...) -> list[dict[str, Any]]` stays unchanged.
- `session_evolved_allowlist(session, registry=...) -> frozenset[str]` stays unchanged; the test locks in active-only, session-independent behavior.

- [x] **Step 1: Write the failing ordinary/project exposure test.** Use temporary agent paths, a project directory `workspace/demo`, one unbound session, and one session with `active_shell="project"`, `project_id="demo"`, and `project_root="workspace/demo"`.

```python
expected = {tool.name for tool in registry.builtins()} | set(PROXY_EVOLVED_TOOL_NAMES)
for intent in ("execute", "read_only", "plan_only"):
    ordinary._ordinary_work_intent = intent
    project._ordinary_work_intent = intent
    ordinary_names = {item["function"]["name"] for item in build_llm_tools(ordinary, registry=registry)}
    project_names = {item["function"]["name"] for item in build_llm_tools(project, registry=registry)}
    self.assertEqual(ordinary_names, expected)
    self.assertEqual(project_names, expected)
    self.assertEqual(ordinary_names, project_names)
self.assertIn("deliverable_review", ordinary_names)
self.assertIn("generate_image", ordinary_names)
```

- [x] **Step 2: Run the new test and verify RED.**

Run: `\.venv\Scripts\python.exe -m unittest discover -s agent-core/tests -p "test_session_agnostic_tool_access.py"`
Expected: FAIL because `build_llm_tools()` currently removes `deliverable_review` for an unbound session and removes `generate_image` for read-only intent.

- [x] **Step 3: Add an ask-mode compatibility test.** In the same test module, set `session.meta.turn_mode = "ask"`; assert `run_evolved`, `generate_image`, and every name in `PROXY_EVOLVED_TOOL_NAMES` are absent, while `read_file` remains present.

- [x] **Step 4: Implement the smallest exposure change.** In `build_llm_tools()`, keep the `turn_mode == "ask"` branch; remove the later `project_id` / `ordinary_work_intent` block that filters `deliverable_review` and `generate_image`. Do not change builtin definitions or evolved allowlist computation.

```python
tools = build_builtin_tools(registry=registry)
tools.extend(build_proxy_tool_definitions())
if session.meta.turn_mode == "ask":
    blocked = {"run_evolved", "generate_image", *PROXY_EVOLVED_TOOL_NAMES}
    tools = [item for item in tools if item["function"]["name"] not in blocked]
return tools
```

- [x] **Step 5: Update the existing image integration assertion.** In `test_generate_image_tool_integration.py`, assert `generate_image` remains in the schema for a read-only `agent`-mode turn, then retain the executor assertion that an actual paid call is rejected with `permission_denied` in read-only mode.

- [x] **Step 6: Run focused tests and verify GREEN.**

Run: `\.venv\Scripts\python.exe -m unittest discover -s agent-core/tests -p "test_session_agnostic_tool_access.py"`
Expected: PASS for execute/read-only/plan-only schema parity and ask-mode compatibility.

---

### Task 2: Remove shell-only evolved-tool denials and keep intent checks consistent

**Files:**
- Modify: `agent-core/project_mode.py::project_mode_block_reason`, `_ordinary_action_block_reason`
- Modify: `agent-core/tests/test_project_documentation_stage.py`
- Modify: `agent-core/tests/test_ordinary_work_intent.py`
- Modify: `agent-core/tests/test_write_evolve_pipeline.py` if a confirmation regression assertion belongs there

**Interfaces:**
- Keep `project_mode_block_reason(...) -> str | None` signature unchanged.
- `active_shell` continues to select path/root rules; it must no longer by itself forbid the confirmed `write_evolve` or `git_clone(target="evolve_tools")` operation.

- [x] **Step 1: Write failing project-shell policy tests.** Add cases asserting `project_mode_block_reason()` returns `None` for a bound project session invoking `run_evolved → write_evolve` and `run_evolved → git_clone(target="evolve_tools")`. Also add an unbound project-pane case for these explicit evolve-root operations. Retain assertions that an unbound project pane blocks `run_command` and `git_clone(target="workspace")` because those lack a bound workspace target. In a bound project, `git_clone(target="workspace")` destinations must stay under the current project root.

```python
reason = project_mode_block_reason(
    active_shell="project",
    project_root="workspace/demo",
    tool_name="run_evolved",
    arguments={"tool_name": "git_clone", "arguments": {"target": "evolve_tools", "dest": "common/example"}},
)
self.assertIsNone(reason)
```

- [x] **Step 2: Run those policy tests and verify RED.**

Run: `\.venv\Scripts\python.exe -m unittest discover -s agent-core/tests -p "test_project_documentation_stage.py"`
Expected: newly added evolve-root cases fail with the current project-shell rejection.

- [x] **Step 3: Extend request-intent tests across shell types.** In `test_ordinary_work_intent.py`, run the existing read-only write/command assertions with both `active_shell="project"` and `active_shell="grow"`. Add read-only denials for `write_evolve` and `git_clone`, and preserve read-only builtin allowance. Add plan-only tests showing source writes/commands remain blocked while planning-document writes continue to follow the existing plan-domain rule.

- [x] **Step 4: Apply request-intent checks independently of shell type.** In `project_mode_block_reason()`, call `_ordinary_action_block_reason()` for both ordinary and project shells. Keep project-root containment checks conditional on a bound project. Add `write_evolve` and `git_clone` to the side-effect classification used by `_ordinary_action_block_reason()` so read-only/plan-only policy is not bypassed through those evolved tools.

- [x] **Step 5: Remove only the project-shell special denials.** Delete the unconditional project-shell block for `write_evolve` and the block for `git_clone` when `target == "evolve_tools"`. In the no-project-root branch, allow those two operations only when their explicit destination is the evolve-tools root; keep workspace writes and commands blocked until a project root exists.

- [x] **Step 6: Verify confirmation and target guards still run.** Add or extend a `ToolExecutor` test that calls `write_evolve` twice in one bound project turn, with an earlier safe operation consuming the ordinary confirmation budget. Assert both `write_evolve` calls still each request visible confirmation; also assert `git_clone(target="evolve_tools")` requests confirmation. Keep all calls in dry-run/test fixtures and do not perform a network clone.

- [x] **Step 7: Run focused policy tests and verify GREEN.**

Run: `\.venv\Scripts\python.exe -m unittest agent-core.tests.test_ordinary_work_intent agent-core.tests.test_project_documentation_stage agent-core.tests.test_project_build_guards agent-core.tests.test_write_evolve_pipeline`
Expected: PASS; ask-mode and existing project-root/path checks remain unchanged.

---

### Task 3: Allow ordinary-session deliverable review with an explicit workspace project

**Files:**
- Modify: `agent-core/agent.py::_BUILTIN_PARAMETERS["deliverable_review"]`
- Modify: `agent-core/tools/executor.py::_run_deliverable_review`
- Modify: `agent-core/tests/test_delivery_profile.py` or create `agent-core/tests/test_session_agnostic_deliverable_review.py`

**Interfaces:**
- Add optional `project_id: string` to the `deliverable_review` input schema; keep `required` as `["task"]`.
- Bound sessions omit `project_id` to use the current project. Unbound sessions must provide a valid workspace project ID. `scope=files` accepts project-root-relative paths and rejects symlink escapes after canonical resolution.
- Build a non-persisted review `Session` copy with copied `SessionMeta`; never set the live ordinary session's project fields.

- [x] **Step 1: Write failing schema and ordinary-review tests.** Create a project with `PROJECT.md` under `workspace/demo`; create an unbound session; patch `SubagentRunner.run_deliverable_review` to return a deterministic `SubagentResult`; invoke `ToolExecutor.run("deliverable_review", {"task": "review", "project_id": "demo"})`.

```python
self.assertTrue(result.ok, result.error)
review_session = runner_mock.call_args.kwargs["session"]
self.assertEqual(review_session.meta.project_id, "demo")
self.assertEqual(review_session.meta.project_root, "workspace/demo")
self.assertEqual(source_session.meta.project_id, "")
self.assertEqual(source_session.meta.project_root, "")
```

- [x] **Step 2: Add target validation tests.** Assert an ordinary session without `project_id` fails, malformed/traversal IDs fail closed, and a bound session rejects a different `project_id`. Assert a bound session without the argument continues to review its current project.

- [x] **Step 3: Run the new tests and verify RED.**

Run: `\.venv\Scripts\python.exe -m unittest discover -s agent-core/tests -p "test_session_agnostic_deliverable_review.py"`
Expected: FAIL because the schema does not accept `project_id` and the executor currently requires a bound `project_id`.

- [x] **Step 4: Implement target resolution.** Add a private resolver in `executor.py` that normalizes `project_id`, confirms an unbound target resolves under `paths.workspace`, requires the directory to be an actual workspace project, and rejects arbitrary roots/HostScope paths. For a bound session, derive the current project from its binding and reject a different supplied ID.

- [x] **Step 5: Pass an ephemeral project-context session to the review runner.** Shallow-copy the source `Session`, deep-copy its `SessionMeta`, set only the copy's `project_id` and `project_root`, then pass it to `SubagentRunner.run_deliverable_review()`. Preserve the original conversation, executor state, and persisted `meta.json` unchanged.

- [x] **Step 6: Run focused tests and verify GREEN.**

Run: `\.venv\Scripts\python.exe -m unittest discover -s agent-core/tests -p "test_session_agnostic_deliverable_review.py"`
Expected: PASS for bound defaults, explicit ordinary targets, mismatch rejection, traversal rejection, source-session immutability, relative file paths, and symlink-escape rejection.

---

### Task 4: Update authoritative documentation and complete verification

**Files:**
- Modify: `docs/TOOLS.md`
- Modify: `docs/PROJECT-MODE.md`
- Modify: `docs/DELIVERABLE-REVIEW.md`
- Modify: `docs/MAP.md`
- Modify: `evolve/tool-catalog/INDEX.md`
- Modify: `evolve/prompts/project-boundaries.md`
- Modify: `evolve/prompts/coding.md`
- Modify: `evolve/tool-catalog/buckets/evolve.md`
- Modify: `docs/TOOL-CATALOG.md`
- Modify: `docs/WORKBENCH-UI.md`
- Modify: `docs/GIT-VENDOR.md`
- Modify: `agent-core/tests/test_generate_image_tool_catalog.py` or the new session-agnostic test for documentation assertions

**Interfaces:** No new public interface beyond the `deliverable_review.project_id` parameter from Task 3. Runtime prompts must describe the same access policy as the executor; do not leave a contradictory project-shell denial in active prompt text.

- [x] **Step 1: Write documentation regression assertions.** Assert `docs/TOOLS.md` describes session-independent agent-mode availability and ask/read-only distinctions; assert `docs/DELIVERABLE-REVIEW.md` and `docs/MAP.md` document the optional `project_id` argument and its workspace-only behavior; assert the runtime index mentions session-wide tool availability.

- [x] **Step 2: Run doc assertions and verify RED.**

Run: `\.venv\Scripts\python.exe -m unittest discover -s agent-core/tests -p "test_session_agnostic_tool_access.py"`
Expected: the new documentation assertions fail before the docs are updated.

- [x] **Step 3: Update docs to match runtime behavior.** State that project binding does not hide tools in `agent` mode; distinguish visibility from request-intent execution checks; document that `ask` remains restricted; document project-root and confirmation prerequisites; describe `deliverable_review.project_id` for ordinary sessions and current-project default for bound sessions.

- [x] **Step 4: Keep the runtime index compact.** Measure `evolve/tool-catalog/INDEX.md` in bytes and remove redundant phrasing if needed; do not exceed 2,048 bytes.

- [x] **Step 5: Verify the Python environment before tests.** Run `\.venv\Scripts\python.exe -c "import sys, httpx; print(sys.executable); print(httpx.__version__)"`; confirm it prints the repo `.venv` interpreter and imports succeed.

- [x] **Step 6: Run the focused regression set.**

Run: `\.venv\Scripts\python.exe -m unittest agent-core.tests.test_session_agnostic_tool_access agent-core.tests.test_session_agnostic_deliverable_review agent-core.tests.test_tool_proxies agent-core.tests.test_generate_image_tool_integration agent-core.tests.test_delivery_profile.ReviewSubagentTests agent-core.tests.test_project_documentation_stage agent-core.tests.test_ordinary_work_intent agent-core.tests.test_project_build_guards agent-core.tests.test_write_evolve_pipeline agent-core.tests.test_tool_catalog_m4 agent-core.tests.test_generate_image_tool_catalog agent-core.tests.test_host_scope_prompt agent-core.tests.test_host_scope_tool_paths`
Expected: PASS; existing HostScope and image-reference confirmations remain intact.

- [x] **Step 7: Run scoped whitespace checks; use the focused regression set as the required test gate.**

Run the Task 4 focused regression command from Step 6 and `git diff --check -- <changed feature files>`.
Expected: focused tests pass and scoped whitespace check exits 0. A full-suite run is optional under the repository's AGENTS.md preference for targeted tests; if attempted and interrupted, report that state without inventing final counts or attributing partial failures to a baseline.

- [x] **Step 8: Review the final diff.** Confirm only planned implementation/docs/tests changed, preserve the pre-existing dirty HostScope files, and do not commit or push as part of this plan.