# Managed final application integration

> Use superpowers:subagent-driven-development. Execute continuously with gpt-6-luna workers and focused checks.

**Goal:** Finish the remaining E1 application wiring and exercise a complete sealed application, isolated native Host, fenced application persistence, shutdown/reopen and fail-closed recovery.

**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md, especially sections 4–6, 8 and 10. Existing runtime/read-only results are inputs, not unfinished tasks to repeat.

**Architecture:** Keep the preactivation binding immutable and E0 unchanged. A distinct managed Electron entry loads verified production identity before profile selection and starts only the managed Python entry. Read-only health stays read-only. A separate explicit managed application mode owns a durable fenced lease and exposes a closed set of application session/document operations; unconverted models/tools/settings writers refuse. Lifecycle records reconcile the real store and preserve maintenance on uncertainty. Build/package the complete application through the existing configless compiler and immutable source boundary.

## Constraints and rulings

- All writes stay under F:/my-agent-main/.worktrees/sandbox. Preserve existing stable/index/Git state and evidence. No dependency installation, real-source commit/merge/promotion, legacy data/credential reads or model calls.
- Existing native-start authority permits isolated owned-namespace native probes; no stable application or user store is used. A disposable Git fixture may contain actual source copies and real commits/merges, as prior probes did. Its evidence is isolated real-component acceptance, not promotion of the user's dirty checkout.
- Separate runtime bytes (reference existing installations) from source/build artifacts; no dependency copies. F: is exFAT with 128 KiB allocation units. Bound inventories and retain owned fixtures.
- Python: F:/my-agent-main/.venv/Scripts/python.exe, -I -B -S, explicit registered venv imports; check interpreter/imports before tests. Node: C:/Program Files/nodejs/node.exe. No full suites.
- Profiles may write only to the explicit owned Host profile/control-output roots. Application writes require current lease capability. Generic legacy write surfaces fail closed in managed mode.
- Do not relax immutable source provenance or call a source-checkout bundle a postmerge production release. Expose any real-source promotion gap explicitly after all executable engineering work is done.

## Task 1 — Python managed application and lifecycle

Owner: new agent-core/managed_application.py and tests/test_managed_application.py; managed_host_entry.py, managed_readonly.py (reuse/factor strict validators only), managed_store.py, server.py and paths.py as necessary. Do not edit Electron/Node files.

- [x] Add meaningful RED checks, then a separate strict managed application bootstrap. Reuse sealed-app/runtime verification; no arbitrary code/data/lease path overrides or fixture fallback.
- [x] Add a closed application request handler using the existing ManagedStore fenced controller: session create/list/read, bounded append/document persistence and lifecycle metadata. Block unconverted model/tool/config writes before dispatch. Every reachable write must revalidate the lease; stale/released/parallel owners refuse.
- [x] Durable initialization/observing/clean-shutdown/reopen/LKG and maintenance on unknown/pending/crashed state. Do not auto-take over a lease or fabricate health.
- [x] Provide exact protocol/API and focused results to the parent/Electron worker early. Include actual subprocess/WebSocket persistence/reopen checks in owned namespaces. Freeze sources after checks.

## Task 2 — Electron managed Host entry

Owner: new desktop/electron/managed-host-entry.ts, managed-host-context.ts (or launcher .mjs/.d.mts helper), desktop/electron/entry.ts; focused new desktop/tests/managed-host*.test.*; minimal production-context helper changes if needed. Do not edit Python/build compiler files.

- [x] Explicit private stdin production bootstrap, canonical fixed identities and registered Electron executable; reject E0/development/ambient overrides and unknown arguments.
- [x] Set profile/session/temp outputs to owned host/profile before readiness. Load the sealed existing renderer with sandboxed preload/closed IPC and the managed Python entry only. Never fall back to main.ts/user data in managed mode.
- [x] Integrate readonly health and the Python application's separate explicit mode. Use bounded authenticated stdin control for clean shutdown and metadata-only receipts. No generic renderer control/root override.
- [x] Wrapper entry dispatches managed flags to this Host and normal launches to existing main.ts. Give parent exact esbuild entry and protocol contract early; targeted tests, no-emit type check and freeze.

## Task 3 — Parent build, actual chain, review and evidence

Owner: compiler/sealing policy, docs, artifacts/managed-final-integration-20261025/.

- [x] Update fixed compiler input/entry and full-app seal requirements without breaking existing inert release fixtures or weakening source/toolchain checks. Build from complete allowlisted actual source in a new isolated real Git/postmerge fixture; do not mutate existing fixture releases.
- [x] Prepare fresh production namespace, register existing runtimes, verify sealed read-only Python/Electron startup, then exercise fenced application persistence, clean shutdown/reopen and uncertain-owner refusal. Record actual process and rendered/native evidence separately from unit tests.
- [x] Independent code review of final worker changes and integration. Fix material findings only, with focused re-review.
- [x] Final stable/Git and source inventory protection, hashes, storage, exact completed/open acceptance gates. All authorized coding/build/audit work must be completed before handoff.
