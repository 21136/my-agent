# Chat Image Generation Tool Implementation Plan

> **For agentic workers:** Execute inline in this worktree; no subagents. Steps use checkbox syntax for tracking.

**Goal:** Add a paid, explicitly confirmed first-party image-generation tool that can resolve current or prior local chat attachments by conversation context.

**Architecture:** Add a read-only on-demand attachment resolver over local session JSONL and a built-in `generate_image` runner that calls the 0x567 image API directly. The executor resolves references before confirmation, displays the exact source in its confirm preview, and only then invokes the API; no MCP, persistent image index, or background scan is added.

**Tech Stack:** Python 3.14 system interpreter (no worktree `.venv`), pytest, httpx, existing AgentPaths/ToolExecutor/LLM secret APIs.

**Spec:** `docs/superpowers/specs/2026-10-21-chat-image-generation-tool-design.md`

## Global Constraints

- Use only `LLM_0x567_KEY`; never log, return, stage, or expose its value to the model.
- Search only user image attachments referenced by local `data/sessions/*/messages.jsonl` records across local harnesses; resolve host URIs only through an existing read-authorized HostScope root, never by scanning host folders.
- Revalidate a selected reference ID and its agent-root path immediately before confirmation; do not expose absolute source paths to the model.
- Never send image bytes or call the image endpoint before affirmative ToolExecutor confirmation.
- Never overwrite an output PNG; automated tests must not make live/paid HTTP calls.
- Preserve changes in the original checkout; implement only in this isolated worktree and do not commit unless separately requested.

---

### Task 1: Resolve current and historical image references from dialogue context

**Files:**
- Create: `agent-core/image_references.py`
- Create: `agent-core/tests/test_image_references.py`
- Reuse: `agent-core/session.py::list_session_ids`; read per-session `meta.json` and `goal.md` for candidate labels, including terminal-harness sessions.
- Reuse: `agent-core/paths.py::AgentPaths.resolve_under_agent`

**Interfaces:**
- `find_image_references(query: str, *, paths: AgentPaths, current_session_dir: Path | None, reference_id: str | None = None) -> ImageReferenceSearch`
- `ImageReferenceSearch` returns either one resolved candidate, a bounded list of public candidates for disambiguation, or a no-match status. Candidate IDs are stable hashes of session/message/attachment identity; internal paths are never serialized.

- [x] Write tests using temporary agent roots and hand-authored `messages.jsonl` fixtures: a context query finds the attached PNG in another session; current-attachment scope only returns current-session files; two equally plausible matches return two distinct IDs; choosing an ID resolves exactly that record; missing/out-of-root/non-image references are not usable; a read-authorized `host:` attachment resolves only when its session record explicitly references it; terminal-harness records remain searchable too.
- [x] Run `python -m pytest agent-core/tests/test_image_references.py -q` and confirm expected failures from missing resolver behavior.
- [x] Implement the parser, session scan, path validation, nearby-message context extraction, deterministic score ordering, and ambiguity result.
- [x] Re-run the resolver test file and confirm all cases pass.

### Task 2: Implement a direct, non-overwriting 0x567 image API runner

**Files:**
- Create: `agent-core/tools/builtin/generate_image.py`
- Create: `agent-core/tests/test_generate_image.py`
- Reuse: `agent-core/tools/schema.py`, `agent-core/llm_secrets.py`

**Interfaces:**
- `run(arguments: dict[str, Any], *, paths: AgentPaths, prepared_reference: ImageReference | None = None, project_root: str = "", conversation_id: str = "") -> ToolResult`
- Accept text-to-image and reference-image edit requests; return path/model/bytes/reference-used metadata.

- [x] Write tests for prompt/model/size/quality validation, exact JSON-vs-multipart request shape, missing-key error without key leakage, valid PNG decode, malformed payload rejection, and exclusive file creation.
- [x] Run `python -m pytest agent-core/tests/test_generate_image.py -q` and confirm expected failures.
- [x] Implement API request/response handling with `httpx`, bounded local reference reads, signature validation, timeout/error mapping, and exclusive PNG output.
- [x] Re-run the test file and confirm all cases pass; no live provider request.

### Task 3: Register the built-in tool and gate history resolution/paid execution

**Files:**
- Modify: `agent-core/tools/registry.py`
- Modify: `agent-core/tools/executor.py`
- Modify: `agent-core/agent.py`
- Create: `agent-core/tests/test_generate_image_tool_integration.py`
- Extend: `agent-core/tests/test_confirm_pipeline.py`

**Interfaces:**
- Tool schema: `prompt` required; `reference_context`, `reference_id`, `model`, `size`, `quality` optional.
- The tool resolver runs before confirmation. If reference search is ambiguous, return candidate IDs and context without confirmation or HTTP. If unique/selected, pass an in-memory `ImageReference` to the runner and add source title/date/filename/context to confirmation preview only.

- [x] Write tests that fail unless `generate_image` is in the builtin registry and LLM schema, is omitted in ask mode, and requires confirmation.
- [x] Add integration tests proving ambiguity returns candidates without calling HTTP, a rejected confirmation makes zero provider calls, and accepted confirmation includes the exact attachment source in the preview and calls the runner once.
- [x] Run `python -m pytest agent-core/tests/test_generate_image_tool_integration.py agent-core/tests/test_confirm_pipeline.py -q` and confirm expected failures.
- [x] Implement registry/schema wiring, ask-mode and executor validation gates, pre-confirm reference resolution, safe preview context, and runner dispatch with session/project scope.
- [x] Re-run both test files and confirm all cases pass.

### Task 4: Add the image key to existing local key settings

**Files:**
- Modify: `agent-core/llm_secrets.py`
- Modify: `agent-core/llm_models_api.py`
- Create or extend: `agent-core/tests/test_image_generation_key_settings.py`

- [x] Write tests that show `LLM_0x567_KEY` appears as a key slot, is accepted by `llm_keys.set`, is masked in settings state, and is preferred from the environment without returning the raw value.
- [x] Run `python -m pytest agent-core/tests/test_image_generation_key_settings.py -q` and confirm expected failures.
- [x] Add the env name to the allowed key set and include a labeled image-generation slot in the key-state event; preserve environment-over-local-secret precedence.
- [x] Re-run the key settings tests and confirm all cases pass.

### Task 5: Run focused regression and full relevant validation

**Files:**
- Verify: all files above; no additional scope.

- [x] Run the focused resolver/generator/integration/key/session/confirm regression set; observed 58 passed (includes the required plan files plus tool proxy and agent loop regressions).
- [x] Run `python -m pytest agent-core/tests -q`; final result: 1,100 passed, 3 skipped, 24 failed, 156 subtests passed. The suite includes existing project/desktop contract mismatches and an unavailable optional `pywinpty` dependency; no failing test is in the new image-tool test files. Baseline comparison was not run, so these are not claimed as pre-existing.
- [x] `git diff --check` is clean; reviewed changed paths/status and scanned credential references. Only fake test keys are present; no generated image bytes or original-checkout files entered this worktree.
- [x] No live image generation was run; this feature remains uncommitted and unpushed.
