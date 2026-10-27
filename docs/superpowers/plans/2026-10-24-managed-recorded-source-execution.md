# Managed Recorded Clean-Source Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Use tests first, bounded focused checks, and explicit sandbox cwd.

**Goal:** Persist the complete reviewed merge plan and scope confirmation before one restricted project-code Git merge, without making dirty real worktrees eligible.

**Architecture:** A clean-only inspector/adapter accepts only committed, fully source-allowlisted, attribute-free, linear-ahead worktrees with a shared Git common directory and verified ordinary paths. A separate exclusive canonical package stores the full review/plan/confirmation and drives the existing write-ahead coordinator. This is a trusted in-process engineering API, NOT native authority, namespace activation, or a solution for the current dirty/protected real sources. No CLI/IPC/native execution wiring this slice.

**Tech Stack:** Node built-ins, node:test, exact absolute Git executable, existing source transaction/journal.

**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md sections 4,6,8,10. This slice covers durable merge inputs and a conservative execution primitive only; other E1 gates remain open.

## Global constraints
- Only F:/my-agent-main/.worktrees/sandbox writes. No real source Git mutations, status/filter invocations, dependency installs, full tests/builds, legacy/private/provider reads or requests, cleanup, or E0 changes.
- All agents gpt-6-luna. No commits/staging/stash/merge except explicitly owned small synthetic Git fixtures, created under this milestone from module-derived sandbox paths after exact cwd checks.
- Keep prepared_empty namespace byte-identical. Package directories in this slice are caller-owned operation/evidence directories, not permission to populate that namespace. No recovery/replay/activation authority from disk records.
- Retain fixtures/evidence. Bound Git fixtures (target <=4 base repos per worker), use plain per-test directories otherwise; no dependency/runtime copies.
- Reject rather than repair dirty/staged/untracked/protected/unknown worktrees, attributes, hooks/config side-effect routes, non-source commit history, unsupported topology, active operation markers, aliases/links/special modes/index flags. Never infer task ownership from ancestry.
- File fsync before later side effects; no power-loss/hostile same-user guarantees. Native exact-scope confirmation and operational namespace integration remain separate gates.

## Shared interface
Adapter owner creates desktop/launcher/managed-clean-source-adapter.mjs and its focused test file.
Exports:
```js
inspectManagedCleanSource({stableRoot,sandboxRoot,gitExecutable}) // async -> immutable review; reject blocked cases
validateManagedCleanSourceReview(review) // pure strict validation + canonical digest -> cloned frozen review
createManagedCleanSourcePlan({review,installationId,namespaceDigest}) // existing strict source-merge plan
createManagedCleanSourceAdapter({review,gitExecutable}) // async -> exact {revalidate,merge,verify}; trusted host only
```
Review exact top-level keys: format=1, mode=managed_clean_source_review_v1, stable, sandbox, history, deliveryPaths, expectedMergedTree, preservationDigest, blockers=[], reviewDigest.
Each source exact keys: root,head,branch,indexDigest,gitDir,commonDir,tree,metadataDigest,files. files sorted by code-unit, exact {path,sha256,mode,oid}, all ordinary source files only. History is oldest-first exact {commit,parents,paths}; parents array of OIDs; paths sorted unique path metadata (no blob/textconv/rename); bind all ahead commits including reverted paths. deliveryPaths same exact {path,beforeSha256,afterSha256} as coordinator, sorted. expectedMergedTree=sandbox.tree. metadataDigest binds relevant safe configuration/operation/index/stash identities. preservationDigest binds the unchanged sandbox baseline, stable non-delivery manifest, stash and metadata expectations (document exact rule).
reviewDigest is SHA256 of canonical code-unit-key-sorted entire review body without reviewDigest. Strict JSON only, no accessors/prototypes/hidden values/cycles/nonfinite numbers, no non-source arbitrary payload. Store may import adapter validator/builder; adapter must NOT import package store.
Git executable is absolute verified regular executable from trusted caller, never shell command or renderer input. No ambient global/system Git configuration. Local config must be constrained before Git reads it; refuse include/filter/external hooks/attribute routes, worktree config, alternate/replace/graft/object routes unless explicitly proven safe. Disable hooks/signing/fsmonitor/rerere/autostash/gc/maintenance, rename similarity, prompts and optional locks. All preflight Git commands metadata-only; use bounded raw FD reads of allowed source and metadata, no status, cat-file or merge-tree write. Stable must be a proper ancestor of sandbox, nonempty delivery; no-ff actual merge via exact OID; verify parents/tree/path delta and actual raw resulting files, sandbox/index/stash and unchanged stable non-delivery state. Adapter must revalidate itself in merge, not trust caller, and reject arbitrary plans/tokens; outcome uncertainty never auto-retries/rolls back. Operation claim remains coordinator-owned.

## Task 1: Inspector + restricted real Git adapter
- [x] Write focused red tests: true shared-worktree clean merge, plan binding, dirty/staged/attribute/protected/history/index/config/identity refusal, drift after inspection, correct receipt and preservation. Reject before write.
- [x] Implement exports above. Strict clean-only means the current project cannot pass; no manufactured empty blocker list from existing review.
- [x] Run focused green tests and preserve exact red/green logs under artifacts/managed-execution-20261024. Do not edit existing modules.

## Task 2: Exclusive full package and recorded executor
Owner creates desktop/launcher/managed-source-package.mjs and desktop/tests/launcher/managed-source-package.test.mjs only.
Exports:
```js
writeManagedSourcePackage({directory,plan,review,confirmation}) // exclusive fixed managed-source-package.json; fsync then bounded reread; returns frozen package
readManagedSourcePackage({directory,installationId,namespaceDigest}) // inspection only, exact binding; returns frozen package
executeRecordedManagedSourceMerge({directory,installationId,namespaceDigest,gitExecutable}) // reads package, creates restricted adapter and uses existing coordinator
```
Package exact keys: format=1, mode=managed_source_package_v1, plan, review, confirmation, packageDigest. Canonical single JSON object + LF; digest excludes packageDigest. Max2MiB, bounded FD read, normalized absolute ordinary directories/ancestors; reject symlink/reparse/alias/hardlink/nonregular/replaced/truncated/oversized/noncanonical files. Exclusive fixed filename never replace/delete/take over existing files even after errors. Validate adapter review plus regenerated plan match byte-for-byte, confirmation exact {planDigest,reviewDigest,namespaceDigest}. Durable only means fsync succeeded in this execution; cold package is historical input, not proof of consent origin or successful execution.
The executor is trusted in-process only; reads/checks immutable package before adapter/merge; no renderer callbacks. Coordinator's single-use journal prevents replay. Malformed/incomplete package or any revalidation failure => no Git mutation. Returned readiness false. No namespace writes or automatic directory creation.
- [x] Write focused red tests for whole package bindings, missing/mismatched/truncated/oversized/aliased persistence, exclusive reuse refusal, accessors, and immutable clone.
- [x] Implement against shared adapter exports. If real adapter fixture needed, coordinate with parent; do not add more synthetic Git repos merely for store tests.
- [x] Run focused green checks. Preserve logs without cleanup.

## Task 3: Integration / independent review / protection
Parent owns docs/MANAGED-RECORDED-SOURCE-EXECUTION.md and evidence/ledger. Reviewer owns a separate review test file only.
- [x] One bounded true Git recorded-executor integration using Task1's fixture helper if possible; assert full package exists before source write, receipt parents/tree/path hashes, preserved sandbox and stash metadata, readiness false, second call refuses without Git mutation.
- [x] Independent 6luna review of both tasks (spec and quality), negative tests and bounded fixes.
- [x] Focused combined tests, source/hash/index/HEAD/namespace protection, evidence footprint snapshot (exFAT131072 allocation unit; exclude directory overhead from estimates), final report with exact open gates. No full-stable-worktree untouched claim because prior external fixture incident remains.

## Rulings / ledger
- Ruling: clean-only execution rejects dirty/protected real repositories rather than bypassing complete inventory/ownership gates; this delivers reusable actual Git code but not current stable adoption.
- Ruling: retain prepared_empty checks unchanged and persist test packages outside real namespace; operational namespace transition remains required before native wiring.
- Ruling: no commits or fixture cleanup despite generic skill defaults; explicit user boundaries prevail.

## Added review-fix task: inert Git metadata guards
Independent review found packed replacement refs, unchecked object-store indirection, and unsupported raw-index extensions. Package worker owns new `desktop/launcher/managed-clean-git-metadata.mjs` and its focused test file; adapter worker owns integration and packed-replace refusal. APIs: `assertManagedPlainIndex(bytes,{objectFormat})` accepts bounded checksummed v2/stage0 only, inert TREE extension only; `assertManagedGitObjectStore({commonDir,gitDir})` inspects bounded ordinary metadata paths before Git invocation, no external linked store. Replacements disabled in all Git invocations and loose/packed replacement metadata rejected. No extra Git fixture repositories for helper tests. This is a substantive review fix, not an accepted limitation.

Review fix: `createManagedCleanSourceAdapter({review,gitExecutable,assertWriteBoundary?})` may receive only a trusted synchronous invariant checker; recorded executor supplies its own FD/content assertion and no renderer input can populate this callback. It runs after all async revalidation and directly before the fixed Git writer; returning a Promise/thenable or throwing blocks. This is necessary because outer wrapper-before-call checks cannot fence the adapter's internal awaits. It does not add execution callbacks or native authority.

