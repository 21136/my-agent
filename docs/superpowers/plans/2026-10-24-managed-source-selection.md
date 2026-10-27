# Managed source selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Required delegated workers are already subagents; do not recursively delegate.

**Goal:** Let the existing native source-review workflow explicitly declare exact sandbox paths and ask the project to revalidate them into a digest-bound, non-executing scope proposal.

**Architecture:** A pure backend planner validates a host-generated review and a strictly bounded path-only selection request. A trusted main-frame IPC captures a fresh review from host-resolved roots, compares its digest, and invokes the planner. An adjacent paginated selection panel is populated only from the current report; all paths default unselected and all stable source/index state defaults preserve. No checkpoint/Git write/execution entry is added.

**Spec:** docs/superpowers/specs/2026-10-24-managed-adoption-e1-design.md sections 2,4,8,10. This implements part of the ownership/scope prerequisite, not full E1 or new architectural permission.

## Global constraints

- Writes only F:/my-agent-main/.worktrees/sandbox. Preserve stable, raw indices, refs/stash, namespace and unrelated sandbox changes.
- No Git mutation, model/credential/old-chat access, dependency install, full tests/builds, new repositories or fixture cleanup.
- All delegated workers gpt-6-luna; no recursive delegation. Separate ownership and tests-first.
- Request exact keys: {format:1,mode:'managed_source_selection_request_v1',reviewDigest:lowercaseSHA256,selectedPaths:string[]}. Max 2048 selected paths, max 512 UTF-8 bytes/path, 256 KiB total request. Reject extras, duplicates/case aliases, traversal, protected/out-of-scope paths, empty selection.
- Response {ok:true,proposal:Record<string,unknown>} or {ok:false,message:string}. Never accept renderer roots, review, blockers, hashes or confirmation.
- Proposal mode managed_source_selection_proposal_v1; proposal_digest canonical SHA256; bind full review digest and both baseline identity/HEAD/branch/index/inventory. Include exact selected sandbox entries, counterpart stable entries (or explicitly not_in_inventory), all stable entries as preservation requirements, excluded sandbox entries, inherited blockers unchanged, plus selection-specific blockers. A declared scope is not verified provenance/ownership or native consent. All execution/readyForMerge/readyForLaunch/checkpointCreated/nativeConsent booleans false.
- Block selected missing/unknown hashes/modes, stable local/index/missing/mode/raw changes intersecting selected paths (even raw equality), and stable absence not proved by inventory. No silently resolving collisions or treating omitted entries as filesystem absence.
- Preserve global blockers; never claim a plan/checkpoint/adoption is ready from path selection.

## Tasks

### Task 1 Backend planner (worker A)
- [ ] RED tests for exact canonical bindings; input/request/report digest validation; hostile paths/duplicates/size limits; empty/unknown selection; deterministic ordering; default preservation/exclusion; collision and missing metadata blockers; all existing blockers retained.
- [ ] Implement desktop/launcher/managed-source-selection.mjs, tests desktop/tests/launcher/managed-source-selection.test.mjs. Export validateManagedSelectionRequest(value) and createManagedSourceSelectionProposal({review,request}). Neither function reads disk/spawns Git/writes anything. A valid review must have valid self-digest, correct flags/mode/format, both inventory digests and baseline shape/entry constraints; only host-issued review consumed in production.
- [ ] Focused tests and self-review. No edits outside owned module/test files and the new managed-source-selection.d.mts declaration.

### Task 2 Interactive selection panel (worker B; parallel with Task 1)
- [ ] RED tests for initial none-selected, full path/hash/counterpart display, literal search/pagination25 retaining controls, changes invalidating proposal, async stale response suppression after selection/clear/refresh, web disabled, safe-text failure handling.
- [ ] Implement desktop/src/shells/unified/managed-source-selection.ts and desktop/tests/managed-source-selection.test.ts only. Export mountManagedSourceSelection(container,{isDesktop,propose?}) -> {show(report),clear()}. Request/response contract above. Keep selection per-path (no bulk ownership), no mutation APIs or localStorage. Proposal display full exact JSON and blockers, explicit non-authority language. Report must meet safe frontend structural checks; default no authority.
- [ ] Focused tests and self-review. No edits outside owned two files.

### Task 3 Trusted bridge and narrow existing wiring (parent)
- [ ] RED tests for trust/fixture/arity/strict bounded request validation before resolver, fresh digest/revalidation, drift/rejection sanitization, busy gate, trust recheck after awaits.
- [ ] New electron/managed-selection-bridge.ts factory gets isTrusted, fixture flag, validateRequest, resolveRoots, inspect, propose. Production validator/planner statically imported with a typed declaration so validation is synchronous before source resolution. Strict request envelope/size check occurs before async source work; final planner also validates. No renderer report/roots.
- [ ] Add one preload API proposeManagedSourceSelection(request) and new main handler with existing trusted-main-frame gate, fixed host root resolver and fresh review capture. Deny fixture; recheck sender after awaited work before returning. No invocation of old adopter or source execution package.
- [ ] Add selection container alongside readonly browser, wire both show/clear to existing review lifecycle; update API declarations and focused panel tests.

### Task 4 Verification and evidence (parent + independent 6luna reviewer)
- [ ] Run focused backend/bridge/UI/regression tests on frozen source, focused frontend and bridge noEmit checks (not full build). Check source integration statically and, when available, operate isolated actual component harness; never call synthetic/component tests native acceptance.
- [ ] Fresh readonly real-source capture -> small explicitly engineering-labelled sample proposal; never call it user confirmation or use it to execute. Revalidate report can be run twice without writes; tamper/drift rejection must be exercised.
- [ ] Compare baseline files except named owned edits, raw indices/namespace/refs; include scope hashes, bounded artifact storage accounting. No new repo/dependency copy.
- [ ] Independent review, resolve findings, rerun focused tests. Write docs/report/result with exact remaining gate: no checkpoint/native consent/real merge/activation/provider acceptance.

## Preflight interface review and rulings

|Tasks|Interface/ownership|Resolution|
|---|---|---|
|1/2|request/response|Exact fields/modes/caps above; browser treats proposal as data, never authority. Disjoint file ownership.|
|1/3|planner and validator|Host captures report afresh; request validator must be synchronous before resolver; planner revalidates canonical report and request.|
|2/3|panel lifecycle|show same report as readonly browser; clear on refresh/errors/web reset; no old proposal survives a new report.|
|1|provenance vs selection|Explicit declaration only, raw equality is never provenance; collisions and all historical blockers remain.|
|2|component vs native|Can submit scope via trusted host but does not provide native full AdoptionPlan consent.|
|3|native permission|Existing merge authority is not execution wiring. This request returns a readonly proposal only.|
|4|fixture storage|No synthetic repos this slice; pure fixtures in memory and reused IAB if necessary.|

Ruling: use the already approved E1 design and continued implementation authority, without another generic design/permission loop. Do not implement speculative checkpoint/Git plumbing in this slice: exact-scope input and stable preservation are prerequisites, not permission to bypass dirty-state blockers.

## Ledger

Baseline: artifacts/managed-source-selection-20261024/baseline.json captured 1836 allowed-source inventory paths; stable/sandbox HEAD unchanged. Four real blockers present. Tasks pending.

Task 3 RED: missing bridge and missing selection markup (2 failures); GREEN initial bridge/panel set16/16. Later added production-validator boundary integration test, pending worker A module. Parent bridge focused noEmit passes. Added static planner import with .d.mts owned by worker A; no async request-validator gap.

Task 3 initial parent check:22/22 backend+bridge+panel tests passed (before worker hardening additions). Independent task3 review found no blocking issue. Static-import prose reconciled. Readonly actual-source component harness prepared; it uses production bridge/planner over a localhost HTTP test transport, never native IPC or adoption. No synthetic repositories.

Tasks 1-3 implementation complete: backend13 focused cases, panel9 focused cases, bridge11 cases. Captured real-source initial UI showed excessive inline metadata; bounded repair collapses full metadata without removing either full SHA or per-entry details. Independent task3 review no blocker; integrated review pending. Final task4 results live in artifacts/managed-source-selection-20261024/report.md and result.json, not inferred from plan checkboxes. A pre-final focused combined run61/61 was before the new density regression; frozen parent rerun required.
