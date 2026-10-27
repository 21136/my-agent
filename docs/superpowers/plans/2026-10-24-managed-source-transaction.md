# Managed Source Transaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Follow tests-first and focused checks only.

**Goal:** Implement the E1 source-merge write-ahead coordinator and verifiable receipts, with real Git integration exercised only in owned disposable repositories.

**Architecture:** A separate single-use first-adoption journal records plan binding, write intent, command exit and verified postconditions. A trusted in-process adapter performs revalidation, the one source merge and independent verification. There is deliberately no production adapter, CLI/IPC execution entry, or bridge from blocked review packets in this slice. The current real review remains blocked and cannot be promoted to execution. This is a write-coordination milestone, not activation or stable adoption.

**Tech Stack:** Node built-ins, node:test, Git only in owned synthetic roots.

**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md sections 4, 6, 8, 10.

## Global Constraints
- Write only in the existing sandbox, branch experiment/sandbox. No real Git writes, installs, full tests/builds, model requests, legacy/private reads, or E0 changes.
- All workers/reviewers use gpt-6-luna. Preserve all unrelated work. No automatic fixture deletion or retry of the earlier refused cleanup.
- Existing real namespace stays prepared_empty and byte-identical.
- File fsync is required before side effects. No cross-platform power-loss, OS sandbox, or adversarial same-user guarantees.
- A journal is permanently single-use; existing/incomplete/corrupt journals cannot be resumed or automatically overwritten. A later independently reviewed recovery/new-operation protocol is outside this milestone.
- Source review digests are not consent. Blockers cannot be cleared by this implementation. Trusted adapter is code, never renderer-supplied callbacks or a ready=true assertion.

## Interfaces and task ownership

### Task 1: Single-use write-ahead journal
Files: create desktop/launcher/managed-source-journal.mjs; desktop/tests/launcher/managed-source-journal.test.mjs.

Exports:
```js
createManagedSourceJournal({directory, identity, operation})
readManagedSourceJournal({directory, identity})
```
Identity exact keys: installationId (UUID), namespaceDigest (64 lowercase hex).
Operation exact keys: operationId (UUID), planDigest and reviewDigest (64 lowercase hex).
The directory already exists and is caller-owned. Use an exclusively created fixed `managed-source-transaction.jsonl` as both durable first-operation claim and append-only hash-chained bounded journal. Walk/check root ancestors; reject aliases/links and nonregular/hardlinked journal files; use bounded FD reads. A handle owns its descriptor; no lock deletion/takeover. Append and fsync every event before returning. Failed persistence poisons that handle. Reopen is inspection only.

Handle methods:
```js
beforeWrite({step:'source_merge'}) // -> {operationId,writeId}; fsynced intent
// exactly one write; no following write/retry, even after failure
afterWrite({writeId,exitCode}) // integer exit; nonzero is maintenance
finish({receipt}) // only after known exit 0; strict bounded JSON receipt below
fail({code}) // safe enum only; no arbitrary error messages, argv, secrets, stdout
close() // descriptor release only; never removes the journal
```
Receipt exact keys: mergeCommit, mergedTree, stableParent, sandboxParent (40 or64 lowercase hex OIDs), deliveryPaths (sorted unique relative strings), preservationDigest (64 lowercase hex).
Failure codes: revalidation_failed, write_failed, verification_failed, interrupted, persistence_failed.
Read output includes identity, operation, events, status, receipt (null unless verified). Status is source_merge_verified only for a fully validated hash chain ending in a successful finish. Every valid unfinished journal is maintenance_required on inspection; missing/corrupt/identity-swapped input fails closed. No auto-replay, compensating Git write, fixture identity or activation state.

- [x] Write failing tests for exclusive claim, intent on disk before callback opportunity, strict receipt ordering, duplicate step/receipt, wrong token, nonzero exit, reopen and corruption/identity/link cases.
- [x] Capture expected red output, implement minimal bounded state machine, run focused green tests.

### Task 2: Exact-plan transaction coordinator
Files: create desktop/launcher/managed-source-transaction.mjs; desktop/tests/launcher/managed-source-transaction.test.mjs.
Exports:
```js
createManagedSourceMergePlan(body) // validates then adds canonical planDigest
executeManagedSourceMerge({directory,plan,confirmation,adapter})
```
Plan exact keys before digest: format=1, mode=managed_source_merge_plan_v1, installationId, namespaceDigest, reviewDigest, stable, sandbox, deliveryPaths, expectedMergedTree, preservationDigest, blockers.
Stable/sandbox each exact keys root (absolute normalized), head (OID), branch (nonempty; sandbox experiment/sandbox), indexDigest (SHA256). Require distinct source roots, shared sources are adapter responsibility. Paths exact keys path, beforeSha256, afterSha256 (hash or null; not both null), nonempty sorted unique; reject aliases/protected/runtime/data/secrets/links indirectly via trusted revalidator. Bound counts and JSON size; conservative source prefixes only desktop/, agent-core/, docs/, protocol/, scripts/. No deletion of unrelated state. Blockers must be empty; raw review packets/proposals/fixture modes are not plans.
Confirmation exact keys planDigest, reviewDigest, namespaceDigest, all exact plan matches. This API only consumes already scoped trusted confirmation; it does not implement native authority.
Adapter exact callable methods revalidate(plan), merge(plan,{operationId,writeId}), verify(plan). Absence fails before journal creation. Adapter is a trusted in-process dependency, NOT an untrusted plugin or production implementation.
Revalidation returns exact keys planDigest, reviewDigest, namespaceDigest, blockers; compare all bindings and empty blockers. Call once before claim and again after claim but before persisted write intent. Clone/deep-freeze plan before any await. Never let adapter mutate an approved plan. On initial invalidity no journal or Git write. After journal claim, any drift/error/unknown exit/verification mismatch goes maintenance with no replay/rollback. Persist exit before verify, persist verified receipt before returning.
Merge returns exact {exitCode}; verified receipt exact journal schema. Compare expected tree, parent heads, exact delivered path list and preservation digest. Exit0 alone is never success. Return {status,operationId,planDigest,receipt}; readyForLaunch remains false. No CLI/IPC/main/E0 edits.

- [x] Write failing tests for blocked/tampered/wrong confirmation, absent adapter, midflight mutation, pre/post-claim drift, unknown/failed write, wrong tree/parents/path scope/preservation, no retry after reopen.
- [x] Capture red, implement, run green.
- [x] Use a small owned disposable Git repository under artifacts/managed-transaction-20261024/fixtures; create a real branch commit with one reviewed source file and execute genuine git merge via the test adapter. Verify two parents, merged tree, exact changed paths and unrelated raw/untracked bytes preserved. Do not call this production adoption. Never perform Git writes on actual roots or delete fixtures.

### Task 3: Integration, independent review, protection evidence
Owner: parent for report/docs/evidence; independent 6luna reviewer for adversarial tests in a separate review test file.
- [x] Run only the two new suites plus independent scoped regressions; include actual logs and scoped hashes.
- [x] Independently inspect implementation trust boundaries, journal interruption/corruption/concurrency and forged receipts. Fix findings via owning workers.
- [x] Compare real stable/sandbox HEAD, branches, raw indexes, stash ref metadata, namespace marker and targeted source hashes to baseline. No broad real status, clean filters or protected content reads.
- [x] Document precisely: implementation/real synthetic Git evidence versus missing production adapter, ownership review, native scope consent, actual merged stable release, lease, activation/recovery and model gates. No claim only user acceptance remains.
- [x] No staging/commit/merge/cleanup; leave source and evidence sandbox-only.

