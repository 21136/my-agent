# Replaceable Skill Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a replaceable, version-aware Skill source/resolution seam while preserving the current built-in Skill Registry API and keeping Executor as the real permission owner.

**Architecture:** Keep `SkillRegistry` as the compatibility facade. Add source/identity/selection abstractions behind it, with `DirectorySkillSource` as the only implementation in this change. Pin a `SkillSelection` on the transient session for one Agent turn, inject the pinned package into the prompt loader, and expose identity data for audit/testing without persisting it to `SessionMeta`.

**Tech Stack:** Python 3.14 in `F:\my-agent-main\.venv`, dataclasses, `typing.Protocol`, SHA-256 content hashes, unittest/pytest.

**Spec:** `F:\my-agent-main\docs\superpowers\specs\2026-10-17-replaceable-skill-provider-design.md`

## Global Constraints

- Preserve unrelated dirty/staged work; do not reset, clean, checkout, bulk-stage, commit, or overwrite unrelated files.
- On Windows use `F:\my-agent-main\.venv\Scripts\python.exe` for Python validation.
- First implementation provides only `DirectorySkillSource`; do not add remote downloads, marketplace behavior, or script execution.
- Skill updates take effect on the next Agent turn; do not hot-reload a Skill within an active tool loop.
- `allowed_tools` remains advisory in this change; Executor/Session remains the real permission owner.
- Existing `SkillRegistry.discover`, `skills`, `select`, `load`, `format_index`, and `format_context` behavior remains compatible.
- Tests must use temporary directories and must not read or write production `data/` or `data_clean/`.

---

### Task 1: Add source and identity abstractions behind SkillRegistry

**Files:**
- Modify: `F:\my-agent-main\agent-core\skill_registry.py`
- Test: `F:\my-agent-main\agent-core\tests\test_skill_registry.py`

**Interfaces:**
- Produces `SkillIdentity`, `SkillSelection`, and `SkillSource` protocol types.
- Produces `DirectorySkillSource(root, max_body_bytes=...)` with `source_id`, discovery diagnostics, package loading, and SHA-256 identities.
- Preserves `SkillRegistry.discover(root, max_body_bytes=...)` as the compatibility entry point.

- [x] **Step 1: Write failing tests for source identity and package selection**

Add tests that create a temporary Skill directory and assert:

```python
def test_directory_source_exposes_stable_identity(self):
    source = DirectorySkillSource(self.root)
    packages, diagnostics = source.discover()
    assert diagnostics == ()
    assert packages[0].descriptor.source_id == "builtin-directory"
    assert len(packages[0].descriptor.content_hash) == 64


def test_selection_contains_package_identity(self):
    registry = SkillRegistry.discover(self.root)
    selected = registry.select_packages("请跑验收", limit=1)
    assert selected[0].descriptor.name == "project-verification"
    assert selected[0].identity.version == "1.0.0"
```

Also add a test proving the hash changes when the Skill body changes and a test proving the existing `select()` descriptor API is unchanged.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_skill_registry.py
```

Expected: FAIL because `DirectorySkillSource`, `SkillIdentity`, and `select_packages` do not yet exist.

- [x] **Step 3: Implement the minimal source/identity layer**

In `F:\my-agent-main\agent-core\skill_registry.py`:

1. Add immutable `SkillIdentity` with `skill_id`, `version`, `source_id`, and `content_hash`.
2. Add immutable `SkillSelection` with `descriptor`, `package`, and `score`, plus an `identity` property.
3. Add a `SkillSource` `Protocol` with `source_id`, `discover()`, and `load()`.
4. Move directory scanning/parsing into `DirectorySkillSource` without changing existing diagnostics semantics.
5. Compute `content_hash = sha256(raw_utf8_skill_md).hexdigest()` after reading the file.
6. Extend `SkillDescriptor` with defaulted `source_id="builtin-directory"` and `content_hash=""` fields so older construction sites remain valid.
7. Make `SkillRegistry.discover()` construct `DirectorySkillSource` and retain its compatibility API.
8. Add `SkillRegistry.from_sources()` with deterministic first-source-wins behavior for duplicate Skill IDs; do not expose remote sources yet.
9. Add `select_packages()` using the existing trigger score and sorting rules; keep `select()` as a descriptor projection.
10. Add `format_selection_context(selection)` and keep `format_context(name)` as a compatibility wrapper.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_skill_registry.py
```

Expected: all existing and new registry tests pass.

- [x] **Step 5: Run syntax and diff checks for the owned files**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m py_compile agent-core/skill_registry.py agent-core/tests/test_skill_registry.py
git diff --check -- agent-core/skill_registry.py agent-core/tests/test_skill_registry.py
```

Expected: exit code 0 for both commands.

### Task 2: Pin Skill selection for one Agent turn and inject the selected package

**Files:**
- Modify: `F:\my-agent-main\agent-core\session.py`
- Modify: `F:\my-agent-main\agent-core\agent.py`
- Modify: `F:\my-agent-main\agent-core\runaway_v2\kernel\runtime.py`
- Modify: `F:\my-agent-main\agent-core\loader.py`
- Test: `F:\my-agent-main\agent-core\tests\test_skill_prompt_integration.py`
- Test: `F:\my-agent-main\agent-core\tests\test_system_prompt_cache_split.py`

**Interfaces:**
- Adds a transient, non-persisted `Session._turn_skill_selection: SkillSelection | None`.
- Adds `Agent._begin_skill_turn(user_text)` and `Agent._clear_skill_turn()` helpers used by normal and v2 turn entry points.
- `build_system_prompt()` consumes the pinned selection when present and otherwise preserves existing direct-call behavior.

- [x] **Step 1: Write failing tests for turn pinning and dynamic prompt sections**

Add tests that:

```python
def test_prompt_uses_pinned_skill_package_after_file_changes(self):
    session = create_new(paths, conversation_id="skill-pin")
    session.append_message({"role": "user", "content": "请跑验收"})
    selection = SkillRegistry.discover(paths.evolve / "skills").select_packages("请跑验收")[0]
    session._turn_skill_selection = selection
    original = build_system_prompt(session, paths=paths)
    rewrite_skill_body_on_disk(paths, "project-verification", "CHANGED BODY")
    again = build_system_prompt(session, paths=paths)
    assert "CHANGED BODY" not in again.prompt
    assert selection.identity.content_hash in again.prompt
```

Also assert `skill_index` and `skill_context` are dynamic sections, and assert clearing the transient selection makes the next turn observe the new file content.

- [x] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_skill_prompt_integration.py agent-core/tests/test_system_prompt_cache_split.py
```

Expected: FAIL because prompt construction currently re-discovers the Skill on every call and does not classify Skill sections as dynamic.

- [x] **Step 3: Implement transient turn pinning**

1. Add `compare=False, repr=False` transient storage to `Session`; do not serialize it in `SessionMeta` or session JSON.
2. Add loader helpers that resolve a `SkillSelection` from the current user text only when no pinned selection exists.
3. Update `build_system_prompt()` to use `format_selection_context()` for the pinned package and retain the existing fallback path for standalone callers/tests.
4. Add `skill_index` and `skill_context` to `_DYNAMIC_SYSTEM_SECTIONS`, because Skill content is request-scoped and must not enter the static provider cache block.
5. At the start of normal `Agent.run_turn`, after the user text is known and before the first LLM prompt is built, resolve and pin the Skill.
6. At the end of every normal turn, clear the transient selection in a `finally`-equivalent cleanup path.
7. At the start/end of `RunawayRuntime.run_turn`, apply the same begin/clear lifecycle so v2 and normal Agent paths have identical semantics.
8. Treat recursive v2 continuation calls as separate request lines: each continuation resolves its own selection, but no selection changes inside one parent tool loop.

- [x] **Step 4: Run the focused tests and verify GREEN**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_skill_prompt_integration.py agent-core/tests/test_system_prompt_cache_split.py
```

Expected: all focused prompt and cache-split tests pass.

- [x] **Step 5: Run the broader Skill/loader regression set**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_skill_registry.py agent-core/tests/test_skill_prompt_integration.py agent-core/tests/test_system_prompt_cache_split.py agent-core/tests/test_llm_client_gpt6.py agent-core/tests/test_llm_models.py agent-core/tests/test_reasoning_effort.py
```

Expected: 0 failures.

### Task 3: Add optional package metadata and compatibility audit fields

**Files:**
- Modify: `F:\my-agent-main\agent-core\skill_registry.py`
- Modify: `F:\my-agent-main\evolve\skills\project-verification\SKILL.md`
- Test: `F:\my-agent-main\agent-core\tests\test_skill_registry.py`
- Modify: `F:\my-agent-main\docs\superpowers\specs\2026-10-17-replaceable-skill-provider-design.md`

**Interfaces:**
- `SkillDescriptor` exposes optional `api_version`, `status`, `compatibility_models`, `runtime_min_version`, and `capabilities` with backward-compatible defaults.
- Existing Skills without the new fields remain `api_version="1"`, `status="stable"`, and empty compatibility/capability tuples.

- [x] **Step 1: Write failing metadata parsing tests**

Add a temporary Skill fixture containing:

```yaml
api_version: 1
status: stable
compatibility:
  runtime: ">=0.2.0"
  models:
    - 0x567-flash
capabilities:
  - inspect_files
  - run_tests
```

Assert that the descriptor exposes the parsed values and that malformed optional metadata becomes a diagnostic without preventing other Skills from loading.

- [x] **Step 2: Run the focused test and verify RED**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_skill_registry.py
```

Expected: FAIL because optional metadata is not yet represented.

- [x] **Step 3: Implement backward-compatible optional metadata parsing**

Extend the existing constrained frontmatter parser only for the documented nested `compatibility` and `capabilities` fields. Do not introduce a general YAML dependency. Preserve the existing malformed-Skill isolation behavior and do not execute any metadata-defined script.

Add the optional metadata to `format_index()` only as compact version/status text; keep full compatibility details in the loaded context and identity/audit object.

- [x] **Step 4: Add a real built-in compatibility declaration**

Add only the minimal metadata to `F:\my-agent-main\evolve\skills\project-verification\SKILL.md`:

```yaml
api_version: 1
status: stable
compatibility:
  models:
    - 0x567-flash
capabilities:
  - inspect_files
  - run_tests
```

Do not add model-gating behavior in this task; the field is audit metadata until a later resolver policy is approved.

- [x] **Step 5: Run registry tests and inspect the exact diff**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_skill_registry.py
git diff --check -- agent-core/skill_registry.py agent-core/tests/test_skill_registry.py evolve/skills/project-verification/SKILL.md
```

Expected: all tests pass and the owned diff has no whitespace errors.

### Task 4: Add acceptance evidence and documentation wiring

**Files:**
- Modify: `F:\my-agent-main\docs\LAYERS.md`
- Modify: `F:\my-agent-main\docs\superpowers\specs\2026-10-17-replaceable-skill-provider-design.md`
- Test/command: `F:\my-agent-main\tools\e2e_0x567_flash.py` (read-only execution only; no source change required unless a stable identity assertion is needed)

**Interfaces:**
- Documentation records the implemented local Provider seam and the deliberate advisory permission boundary.
- Live acceptance records selected Skill identity without writing production data.

- [x] **Step 1: Update documentation status after code passes**

Change the design document status from `draft · 待用户审阅` to `implemented · local directory source`, and update `F:\my-agent-main\docs\LAYERS.md` with the source/resolver/runtime boundary.

- [x] **Step 2: Run the focused suite and module compilation**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe -m pytest -q agent-core/tests/test_skill_registry.py agent-core/tests/test_skill_prompt_integration.py agent-core/tests/test_system_prompt_cache_split.py
F:\my-agent-main\.venv\Scripts\python.exe -m compileall -q agent-core
```

Expected: 0 failures and exit code 0.

- [x] **Step 3: Run real 0x567-flash acceptance in an isolated temporary root**

Run:

```powershell
F:\my-agent-main\.venv\Scripts\python.exe tools\e2e_0x567_flash.py --maintenance-only --turn-timeout 120
```

Expected: `E2E_STATUS=PASS`; report the actual model response identity, Skill selection evidence, tool calls, and whether any files outside the temporary root changed.

- [x] **Step 4: Verify repository boundaries before reporting completion**

Run:

```powershell
git status --short --untracked-files=no
git diff --check -- agent-core/skill_registry.py agent-core/loader.py agent-core/agent.py agent-core/runaway_v2/kernel/runtime.py agent-core/session.py agent-core/tests/test_skill_registry.py agent-core/tests/test_skill_prompt_integration.py agent-core/tests/test_system_prompt_cache_split.py evolve/skills/project-verification/SKILL.md docs/LAYERS.md docs/superpowers/specs/2026-10-17-replaceable-skill-provider-design.md
```

Expected: only owned files are newly changed by this implementation; unrelated pre-existing dirty files remain untouched. Do not stage or commit unless explicitly requested.
