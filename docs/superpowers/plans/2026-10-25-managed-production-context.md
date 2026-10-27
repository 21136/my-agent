# Managed production context and first-store fencing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Work only in the existing sandbox; no commits or source promotion.

**Goal:** Bind a verified V2 postmerge payload to a production namespace and implement a working, durable empty-store writer fence without enabling an unfenced application.

**Architecture:** Production records use their own schemas and reject E0 fixture identities. A host-only context preparation verifies the existing empty namespace and sealed source/build, writes intent before copying the bounded release, then persists a digest-bound read-only binding. A standard-library Python store controller implements exclusive process locking, durable monotonic lease generations, initialization, bounded fenced writes and maintenance on uncertainty. The actual application's writer integration is audited separately; unavailable runtime registration or writer coverage never becomes launch readiness.

**Tech Stack:** Node ESM, Python stdlib SQLite and OS file locks, existing namespace/release validators. No dependency copies or installation.

**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md sections 3–6 and 10.

## Global constraints

- Writes only under F:/my-agent-main/.worktrees/sandbox. Stable dirty/index/Git state is preserved.
- All coding workers are gpt-6-luna. Focused tests only.
- Real prepared-empty namespace and old data/credentials remain untouched in this engineering run; use a new owned isolated namespace for live component proof.
- No application launch, source merge, model request, or activation. Compiler/stdlib component probes are separate evidence.
- Project-local mode remains logical_separation_only and credentials forbidden. Preserve private-mode requirements and E0 rejection gates.
- Unknown/partial operations retain their evidence and block reuse; no automatic recovery, cleanup, overwrite, lease takeover or migration.

## Task 1 — Production binding (6luna worker)

Files: new desktop/launcher/managed-production-context.mjs and .d.mts; new desktop/tests/launcher/managed-production-context.test.mjs.

Public API:
`prepareManagedProductionContext({stableRoot,sandboxRoot,installationId,operationId,releaseId}) -> Promise<descriptor>` and `readManagedProductionContext(sameInput) -> Promise<{descriptor,binding,manifest}>`.

Fixed project-local namespace and fixed context record paths, no caller-supplied output path, dependency callback or raw manifest. Before writing, call the real project namespace reader, the real V2 release verifier and materialized source reader. Namespace source roots must equal verified snapshot roots. Binding fields include namespace digest/installation, source plan/receipt/snapshot/merged commit/tree/worktree identities, exact release/build/toolchain identities, source operation, fixed Host paths, data store UUID/schema/revision 0, access read_only, runtimeRegistration null, writerLease null and readyForLaunch false. Never create a fake runtime binding.

Exclusive state/production-context-intent.json precedes any payload copy; state/production-binding.json and state/production-context-receipt.json publish only after exact inventory/hash checks. Copy release/<releaseId> into namespace releases/<releaseId> with exclusive file writes and bounded 64 MiB payload/512 MiB rounded budget. Known initial layout plus these exact additions only; reject foreign/extra/partial records, aliases, links and populated Host/data paths before reading contents. Recheck input namespace marker and source release around copy. Existing prepared marker remains immutable historical identity; old prepared-only readers stay strict and unchanged. Repeated prepare refuses before new writes. Read verifies the complete installed payload directly against the bound manifest, durable receipt and original V2 provenance. It performs no writes and never calls the prepared-only reader after records exist.

Test first: E0/substituted source/partial/extra/tampered records and exact initialized layout refusal; synthetic tests are labeled. Parent provides actual isolated Git/build release integration.

## Task 2 — Empty store controller (6luna worker)

Files: new agent-core/managed_store.py; new agent-core/tests/test_managed_store.py.

Public API: `ManagedStore(root, *, installation_id, data_store_id, release_id, binding_digest)`; `.acquire(operation_id) -> Lease`; `.initialize(lease, operation_id) -> receipt`; `.write_bytes(lease, operation_id, relative_path, data) -> receipt`; `.release(lease, operation_id) -> receipt`; `.inspect() -> metadata`. API takes no arbitrary metadata paths. Root is one registered production namespace with fixed state/ and data/store/ layout; reject fixture mode and mismatched identity. Lease tokens are ephemeral capability objects; never place real tokens in stdout/receipts.

Use an OS-held nonblocking one-byte process lock plus SQLite FULL-synchronous journal under state/managed-store-control.sqlite3. Persist acquire generation/identity/token hash and init/write intent before side effects. Generation increases monotonically; a second writer, stale token, wrong release/binding/store, unknown op or duplicated op with changed input is refused. A crashed active owner is maintenance, even if the OS lock is now free. No PID probing or takeover.

Initialize only a genuinely empty data/store/ under the lease; exclusive create the SQLite schema file, transactionally bind managed_empty_store_v1, revision 1, identity and initialization receipt. Pending intent or partial initialized files mean maintenance. Fenced bounded new application file writes use canonical safe relative paths excluding internal metadata/secret/link paths, exclusive writes, byte/hash verification and durable before/after receipts/revision. No general callback or arbitrary SQL. Duplicate completed operation may return its original receipt only if all input identities/content and current state reconcile; never replay an uncertain write. Release checks current owner/generation and no pending operation before clearing ownership. Ordinary existing app writers are not claimed covered until their adapters use the controller.

Focused tests use marked owned temp directories: actual separate processes contend; initialization and write persist across reopen; stale/wrong identities and traversal refuse without writes; crash leaves maintenance; interruption points retain intent; only metadata emitted. Verify stable .venv Python and required stdlib imports first. No 600-file exFAT fixtures or dependency installs.

## Task 3 — Parent integration and verification

- Parent owns managed-cli.mjs, docs, and artifacts/managed-production-context-20261025/.
- Add explicit prepare/check production-context CLI commands with canonical roots and IDs, metadata-only output, no launch/activation option. Extend known error codes without raw exceptions.
- Use the prior isolated actual Git/V2 release and a new owned prepared namespace to exercise actual preparation, cold read and tamper/partial refusal. Do not populate the real user's namespace. Distinguish this from native application acceptance.
- Run only new focused tests plus affected CLI/E0 context regressions and Electron typecheck as relevant. Obtain independent 6luna review, verify stable/source/Git protection and record storage allocation.
- Record exact writer-map findings and unintegrated app paths as agent implementation work, not a request for user action. Full activation remains blocked until registered runtime bootstrap, all write adapters, read-only app health and native lifecycle exist.

## Execution record — 2026-10-25

- [x] Task 1: production binding, declaration and focused refusal tests; UUID and four-field root-schema corrections verified against the actual isolated release.
- [x] Task 2: real SQLite/OS-lock controller, canonical data identity, mutation-boundary binding rechecks, process contention/crash and interruption tests.
- [x] Task 3: explicit prepare/check CLI; real isolated production-format namespace; cold CLI read; binding/payload/extra-entry/repeat/selected-source refusal; real initialization/write/release and separate cold Python inspection.
- [x] Focused checks: Node 7, existing CLI 11 and Python 8 passed; Electron TypeScript passed. Full suite not run.
- [x] Stable preservation: 836 baseline source files and Git metadata unchanged; fresh stable-source additions/removals/changes all zero.

Binding digest: `7634e54f1a7c06092bcffc8d96572217072f8c8bf5e3fc85eedcfc292cb711df`. The owned store reached revision 2 and reopened initialized with no active lease or pending operation. Evidence lives in `artifacts/managed-production-context-20261025/`.

This plan's component scope is complete. Registered runtime bootstrap, ordinary Python/Electron writer adapters, actual read-only application health and native activation/shutdown/reopen remain unfinished E1 work. No active/LKG pointer, application health or complete production acceptance is claimed.
