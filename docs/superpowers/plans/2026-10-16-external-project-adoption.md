# External Project Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Let users adopt an unfinished project from another tool in place, without copying source files, by binding a confirmed external directory through a workspace junction while preserving existing project permissions and session behavior.

**Architecture:** A read-only scanner produces an adoption report. A confirmed apply step validates the external root, creates `workspace/<id>` as a Windows directory junction, records the external-root mapping in agent state, and binds a normal project session without fabricating plan confirmation. Existing executor paths continue to use the logical workspace path; junction resolution sends actual reads/writes to the original directory.

**Tech Stack:** Python 3.14, pytest, Windows junctions, existing project CLI/WS/Desktop TypeScript.

**Spec:** `docs/superpowers/specs/2026-10-16-external-project-adoption-design.md`.

## Global Constraints

- Preserve all unrelated dirty/staged/untracked work; no reset, clean, broad staging, or commit.
- Never delete, move, overwrite, or recursively clean an external project root.
- Scanning is read-only; only explicit apply creates the junction or writes project metadata.
- Reject agent root, workspace root, data/session roots, missing paths, files, junction targets outside the accepted root, and occupied logical IDs.
- Do not read or persist secret contents; `.env` is reported by name only.
- Existing ordinary request intent/path/risk policy remains the permission source; automatic v1/v2 owner paths remain unchanged.

---

### Task 1: External root scanner and junction registry

**Files:**
- Create: `agent-core/external_project.py`
- Create: `agent-core/tests/test_external_project.py`

**Produces:** `scan_external_project(paths, raw_root, project_id=None) -> ExternalProjectScan`; `apply_external_project(paths, scan) -> ExternalProjectBinding`; `validate_external_binding(paths, project_id) -> ExternalProjectBinding | None`; `detach_external_project(paths, project_id) -> None`.

- [x] Add RED tests for missing/file roots, protected roots, ID collision, read-only scan, detection of Git/docs/tasks/build markers, and no secret-content capture.
- [x] Add RED tests for junction creation, target validation, re-open validation, and detach preserving the external directory.
- [x] Implement canonical path validation and stable project ID derivation.
- [x] Implement Windows directory junction creation with a safe fallback/error message; never replace an existing path.
- [x] Persist only project ID → external root + junction metadata in agent state.
- [x] Run the new tests with `.venv\Scripts\python.exe`.

### Task 2: CLI adoption flow

**Files:**
- Modify: `agent-core/project_cli.py`
- Modify: `agent-core/project_mode.py` only for external-aware project listing/status if required.
- Create/extend: `agent-core/tests/test_external_project_cli.py`

- [x] Add `项目 导入 <path> [id]` parsing and a scan/apply confirmation flow without overwriting the external root.
- [x] Show detected facts, Git dirty state, existing task/docs markers, and the logical workspace ID.
- [x] Apply confirmed adoption, bind the current session, and arm request-local execute without setting `project_plan_status=confirmed`.
- [x] Add a status/list marker for adopted projects and a fail-closed message when the junction target disappears.
- [x] Verify existing `项目 打开/切换` continues to resume adopted projects.

### Task 3: WebSocket/API adoption flow

**Files:**
- Modify: `agent-core/project_api.py`
- Modify: `agent-core/server.py` only if dispatch/allowed event registration requires it.
- Modify: `desktop/src/api/ws.ts`
- Create/extend: `agent-core/tests/test_external_project_api.py`

- [x] Add `project.import.scan`, `project.import.apply`, and `project.import.cancel` messages/events.
- [x] Keep scan response read-only and apply confirmation explicit.
- [x] Emit project state/session replacement events consistent with existing project switching.
- [x] Include external binding health in project list/state without exposing secret contents.
- [x] Run API/server focused tests.

### Task 4: Desktop import entry and failure state

**Files:**
- Modify: `desktop/src/shells/unified/project-panel.ts`
- Modify: `desktop/src/shells/unified/index.ts`
- Modify: `desktop/tests/ordinary-mode.test.ts` or create `desktop/tests/external-project.test.ts`

- [x] Add an optional “导入已有项目” entry from the project surface; keep normal project switching unchanged.
- [x] Render scan facts and an explicit confirmation action.
- [x] Render junction-missing/external-root-unavailable as a recoverable project state, not as a fake empty project.
- [x] Keep imported projects subject to ordinary execute/plan-only/read-only request intent.
- [x] Bundle/run focused Node tests and run `npm run build`.

### Task 5: Integration audit and verification

**Files:**
- Update: `docs/README.md`, `docs/DOC-GOVERNANCE.md`, `docs/CONTEXT-SWITCH.md`, `docs/PROJECT-MODE.md` with current import behavior.
- Update: `docs/superpowers/specs/2026-10-16-external-project-adoption-design.md` implementation status after verification.

- [x] Run external scanner, CLI/API, context-switch, ordinary permission, and project-session regression tests.
- [x] Run focused runaway v1/v2 suites to confirm external adoption does not alter automatic owner logic.
- [x] Audit all external-root writes for path containment and junction target validation.
- [x] Check changed Markdown links and task-owned `git diff --check`.
- [x] Leave all changes unstaged and uncommitted.
