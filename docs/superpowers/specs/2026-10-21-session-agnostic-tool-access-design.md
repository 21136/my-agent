# Session-Agnostic Tool Access Design

**Status:** Proposed design for review. The user approved the direction to make tools available in ordinary and project sessions; implementation must wait for review of this spec.

## Problem

Tool availability and execution behavior currently depend on whether a conversation is project-bound or uses the project shell. Examples include hiding `deliverable_review` when `project_id` is absent, hiding `generate_image` on read-only turns, and rejecting `write_evolve` or `git_clone` to `evolve/tools` from a project shell. Active evolved tools are already session-wide; they are not filtered by topic.

This makes ordinary/project session type act like a tool permission boundary, even when the user explicitly requests an operation that could be safely performed with the tool's own target and policy checks.

## Goals

1. Ordinary and project sessions in `agent` (动手) mode receive the same registered builtin and proxy tool definitions.
2. Every evolved tool with `status=active` remains callable in both session types, independent of project binding or topic list.
3. Remove restrictions caused solely by session type or shell type, while retaining user-selected mode, tool-specific prerequisites, request-intent protections, confirmations, and path/host boundaries.
4. Let an unbound ordinary session invoke `deliverable_review` by explicitly naming a workspace project; project-bound sessions continue to default to their current project.
5. Update authoritative tool, project-mode, and deliverable-review documentation and add regression coverage for both session types.

## Non-goals

- `ask` / 只聊 keeps its current restricted-tool behavior: execution tools, image generation, and side-effecting flat proxies remain disabled; read-only builtins remain available where otherwise valid.
- A read-only or plan-only request does not gain permission to perform side effects. Tool definitions may be visible, but executor policy still rejects disallowed side effects.
- This does not activate draft, staged, suspect, or archived evolved tools.
- This does not relax credential handling, host-scope authorization, path validation, confirmation, confirmation previews, or tool-specific quotas.
- This does not permit a project-bound session to silently target another project.

## Proposed Behavior

### Tool definitions

- In `agent` mode, `build_llm_tools()` returns all builtin and flat-proxy definitions regardless of `project_id` and request-level `ordinary_work_intent`.
- In `ask` mode, preserve the existing filter: do not expose `run_evolved`, side-effecting proxies, or `generate_image`; keep read-only builtins available.
- Evolved dispatch continues to use the session allowlist of all `active` tools. Topic and project binding are not additional allowlist gates.

### Request and shell policies

- Read-only and plan-only intent continue to constrain execution. Exposing a function schema is not permission to run a side effect; executor validation remains authoritative.
- `generate_image` remains an explicitly requested, user-confirmed, potentially paid operation. Its visibility is no longer hidden merely because a turn is classified read-only; executor validation still requires an execution-eligible request and confirmation before the API call/reference upload.
- Remove project-shell-only rejection of `write_evolve` and `git_clone` targeting `evolve/tools`. Continue to enforce their existing target-root, manifest, clone, credential, and confirmation policies. In project sessions, every `write_evolve` call must receive its own visible confirmation and cannot be covered by the one-confirm-per-turn ordinary budget.
- Project-root write containment remains in force for project work. This change removes session-type denials, not target/path authorization.

### Project-specific review target

- `deliverable_review` gains an optional `project_id` argument.
- In a bound project session, omitted `project_id` means the current project. An explicitly different ID is rejected; the tool does not silently cross project boundaries.
- In an unbound ordinary session, `project_id` is required. Normalize it and resolve it only through the repository's existing workspace project resolver; do not accept arbitrary absolute roots or HostScope paths.
- Run the review using an ephemeral review-session context for the selected project. Do not mutate or persist the ordinary chat's `SessionMeta` project binding.
- `codebase_search` remains usable in an ordinary session with its current agent-root index scope; a bound project session keeps its current project-root scope.

## Security and Compatibility

- Preserve `ToolExecutor` validation, active evolved status checks, tool schemas, explicit confirmations, write policy, foreign-project write guard, host-scope read/write authorization, output validation, and image API credential resolution. Project-session `write_evolve` requires a fresh visible confirmation on every call.
- Keep read-only and plan-only executor blocks for side effects. Preserve request-mode checks: `ask` hides execution surfaces, and read-only/plan-only executor checks continue to reject side effects.
- For `deliverable_review` `scope=files`, accept paths relative to the selected project root or explicitly project-root-prefixed paths; resolve symlinks and reject anything whose canonical path escapes the selected project. For an unbound session, constrain the selected project to `workspace/<normalized-project-id>` and pass that logical root into the read-only review subagent.
- Existing project-bound tool calls without a `project_id` argument remain backward compatible and use the bound project.

## Documentation

Update these sources to state that session type does not hide registered tools, and separately document execution-mode and target prerequisites:

- `docs/TOOLS.md` — tool exposure, project-shell policy, confirmation and target boundaries.
- `docs/PROJECT-MODE.md` — ordinary/project session distinction and retained request-intent/path policies.
- `docs/DELIVERABLE-REVIEW.md` — bound-project default and explicit project targeting from an unbound session.
- `docs/MAP.md` — tool exposure and `deliverable_review` argument contract.
- `evolve/tool-catalog/INDEX.md` — concise session-wide tool availability and the `ask`/read-only distinction, within its 2,048-byte limit.
- Runtime guidance: `evolve/prompts/project-boundaries.md`, `evolve/prompts/coding.md`, `evolve/tool-catalog/buckets/evolve.md`, plus current operator guidance in `docs/TOOL-CATALOG.md`, `docs/WORKBENCH-UI.md`, and `docs/GIT-VENDOR.md`. Mark retained historical P6 decision tables as superseded.

## Acceptance Criteria

1. `build_llm_tools()` in `agent` mode includes the same complete builtin/proxy names for an ordinary session and a project-bound session, including `deliverable_review` and `generate_image`.
2. `build_llm_tools()` includes all evolved execution through `run_evolved`; `session_evolved_allowlist()` remains all and only `status=active` for both session types.
3. `ask` mode still omits its execution tools, and executor validation still rejects side effects for read-only/plan-only turns in both session types.
4. A project-bound `deliverable_review` defaults to the current project; an ordinary unbound review succeeds only with a valid workspace `project_id`; invalid, traversal, and mismatched project IDs fail closed. `scope=files` accepts project-root-relative paths and rejects symlink escapes.
5. `write_evolve` and `git_clone` to the evolve-tools target are no longer rejected solely because `active_shell == "project"`; project-session `write_evolve` always prompts on every call, and existing policy/confirmation tests remain green.
6. Runtime prompts, current operator docs, and `evolve/tool-catalog/INDEX.md` match the behavior; retained historical P6 text is explicitly superseded; INDEX remains at or below 2,048 bytes.
7. Focused ordinary/project-session tests and related project-mode, tool-proxy, image, deliverable-review, host-scope, and documentation tests pass.

## Review Risks

- Exposing schemas in read-only turns can lead the model to propose tools it cannot execute. The executor remains authoritative, and prompts must make mode restrictions explicit.
- Allowing ordinary review requires a target project identifier; silently choosing the whole workspace or an arbitrary root is intentionally prohibited.
- Allowing evolve-tool creation from project sessions changes the current grow/project separation. Existing per-tool confirmation and evolve-root validation must be verified before implementation.