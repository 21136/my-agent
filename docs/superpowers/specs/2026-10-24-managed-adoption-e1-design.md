# Managed Adoption E1: Production Namespace and First Release Activation

**Status:** Historical private installation strategy remains supported. On 2026-10-24 the user approved project-contained runtime as an explicit alternative; see section 10 and the project-local preparation plan. Production implementation and acceptance remain incomplete.  
**Parent:** `2026-10-22-stable-led-full-stack-evolution-design.md` §§4.3, 12; `2026-10-22-stable-led-full-stack-evolution-contracts.md` §G. E0 is the fixture-only fixed Launcher baseline.  
**Scope:** Specify the first real managed-installation/adoption boundary, ending in first activation against a new empty data store. This is not an implementation plan.

## 1. Decision summary

E1 keeps E0's verified fixed-release lifecycle and fixture-adoption lane intact. Production managed installations use a distinct namespace, schema, binding and context; no production root is inferred from or substituted into an E0 fixture. E1 supports a digest-bound, user-reviewed project merge followed by initial activation with an empty new data store and a single writer lease. It does not migrate or take over existing application data.

User-approved on 2026-10-24—private root:

`%LOCALAPPDATA%\my-agent\managed\<installation-UUID>\`

The user approved this path and empty-first-store strategy on 2026-10-24. Only a new owned installation namespace may be prepared; do not inspect legacy stores or claim runtime readiness from directory preparation. Verify the resolved location is on local NTFS with appropriate private access controls; otherwise fail closed rather than silently falling back to a repository, shared, removable, or fixture root.

## 2. E0 boundary and current inputs

E0's fixed 198-file release completed its recorded native fixture lifecycle and adoption. That proves the fixture path only. Keep `launcher_fixture_v1`, its bindings, fixtures and fixture gate unchanged. Do not repoint fixture roots at real worktrees or remove/relax their rejection gate to simulate production.

The real targets are distinct existing worktrees:

- Stable: `F:\my-agent-main`, branch `codex/self-hosting-e2e`, HEAD `ceb2aa8`.
- Experiment: `F:\my-agent-main\.worktrees\sandbox`, branch `experiment/sandbox`, HEAD `6c34432`.

Both have extensive dirty state. The effective merge-base-to-sandbox tree diff currently changes only a marker path, but the union of paths touched throughout the three ahead commits is broader. Neither fact identifies task ownership or authorizes whole-branch delivery. E1 must inventory the actual complete range, index, tracked/untracked state, and relevant worktree identity at plan time. Preserve dirty files, index, and stashes. Never infer ownership from branch ancestry or silently choose ours/theirs.

The bounded `session.paths` propagation repair from `Agent.create` to `LLMClient` is complete and independently reviewed: 6 focused tests and 3 additional payload regressions passed. This does not constitute provider-adapter acceptance. The private model adapter remains absent. The current registry fact is `0x567-flash`, with upstream model default `gpt-6-luna` via `OX567_MODEL_FLASH`, and credential reference `OX567_API_KEY`. This design does not read credentials or issue requests.

## 3. Namespace and identity separation

E1 introduces a new production-only identity kind (proposed name `managed_installation_v1`) rather than widening the fixture schema. The production manifest/binding/context must bind, by canonical digest:

- installation UUID; fixed release ID and manifest digest;
- separately resolved launcher state, Host state/profile/workspace and application-data roots;
- stable and sandbox Git common-dir/worktree identities, expected branch/HEAD baselines, and approved delivery scope;
- data-store identity, schema ID, revision and writer-lease identity;
- protocol/schema versions and the exact trusted control context.

Paths are normalized absolute paths and checked for aliases, links/reparse points, and unexpected substitution. The known source-source nesting is intentional: the stable worktree contains the sandbox worktree. Do not require source roots to be pairwise disjoint; validate their expected common Git repository/worktree identities and branch/HEAD baselines instead. Private launcher state, releases, Host state/profile/workspace, and data roots must be mutually non-overlapping and outside both source worktrees and candidate-writable mappings. E0 fixture identities cannot satisfy production validation; production identities cannot be consumed by fixture mode. A digest or path change invalidates the prepared plan and requires review of the changed scope.

Use fixed non-overlapping subdirectories under the approved root: `launcher/`, `state/`, `releases/<release_id>/`, `host/state/`, `host/profile/`, `host/workspace/`, and `data/store/`. Create them only after the single path/data-strategy decision is approved. Require local NTFS and private per-user access controls; if either check fails, stop without fallback. These are implementation constraints, not additional user design choices.

## 4. Adoption plan and consent

Prepare a canonical `AdoptionPlan` extending contracts §G. Its digest binds: installation/release identity; source snapshot and build fingerprint; stable and sandbox baseline identities; complete proposed delivery path set and per-path hashes; pre-existing/unrelated paths; data root/schema/revision; migration=`none`; and ordered steps/receipts. Execution rechecks the digest and all baselines before each irreversible boundary. Any mismatch expires the plan without mutating either worktree.

Before merge, the native trusted UI presents the **full exact range**: commits, changed paths, per-path diff/hash, pre-existing dirty/index/untracked state, conflicts, excluded paths, and resulting stable tree identity. The required scope-bound confirmation consumes the project-merge authority already granted for this task; it is not a request to grant that authority again. It covers only the displayed plan digest and range. If inspection finds the actual range exceeds that scope, stop for approval of that delta only. Do not auto-stage broadly, stash, clean, reset, or resolve conflicts with a blanket strategy. If task-owned changes cannot be separated safely, stop and retain both trees unchanged.

A successful, verified source-merge receipt is necessary but not sufficient for activation. Re-capture and rebuild from the **actual merged tree**, then verify the release provenance against that snapshot. Source merge alone never changes the active release or data writer.

## 5. E1 first activation and data lease

E1 is deliberately the empty-store case:

1. The private installation path and first use of an independent empty data store were approved on 2026-10-24. Preparation must still pass real local NTFS/private ACL checks and must never reuse legacy data. Existing native-start and project-merge authority remains in force within its stated scope; it is not solicited again here.
2. Persist the plan and operation identity before source mutation. A pre-merge candidate build/health check may run as an optional preview against disposable data; label it `premerge_preview`, never as post-merge acceptance or activation evidence.
3. Under the already-granted, exact-scope project-merge authority, execute the reviewed merge and persist its receipt. Then snapshot the resulting stable tree, build a new sealed release, and verify its provenance against that merged-tree snapshot. Any mismatch blocks activation.
4. Record `postmerge_build_verified`; start that exact release read-only against the new empty store and require its post-merge health check to pass. It cannot acquire a write lease during this check.
5. At activation, acquire the first exclusive lease for `(installation_id, data_store_id, release_id, operation_id)` at generation `1` and persist its receipt. Under that lease, initialize the release-declared schema in the empty store (revision `0` before initialization, `1` after the committed initialization record); only then permit ordinary application writes. Initialization has its own operation receipt; uncertainty leaves the installation in maintenance.
6. Atomically set `active` to the verified release and enter `observing`, retaining the prior recovery record and any LKG in this production namespace. On first install, production LKG is null until acceptance; never import an E0 fixture LKG. Promote LKG only after one clean native lifecycle acceptance: post-merge readiness, lease/schema/revision reconciliation, bounded smoke interaction, clean shutdown, and successful reopen with the same installation/store identity. A failure remains maintenance/recovery, not implicit rollback.

The lease is exclusive, installation- and store-scoped, durable, and fenced by a monotonically changing generation/token. Every write path must present the current lease generation; stale owners fail closed. Lease acquisition/release and activation records are journaled with idempotent operation IDs and verifiable receipts. A process lock or sidecar `ready` signal is not a data lease.

**No legacy takeover in E1.** Directly pointing the new Host at existing legacy data is rejected until reliable old-writer drain, data snapshot/revision checks, migration rehearsal, and safe rollback are implemented and separately approved. E1 does not copy, inspect, import, or modify existing chats, keys, or other user data. Data migration remains a later, independently reviewed AdoptionPlan with source/target schema, snapshot digest, validation, reverse strategy and explicit authorization.

## 6. Durable lifecycle and crash policy

Persist transitions and receipts before crossing their side-effect boundary:

`prepared → source_merge_verified → postmerge_build_verified → postmerge_candidate_read_only_verified → lease_acquired → data_initialized → active/observing → last_known_good`

An optional pre-merge preview is a separate non-activation record and is not in this transition chain.

`active` and `last_known_good` are separate pointers. LKG remains the prior approved production release throughout observation; on first install it remains null until acceptance, and fixture releases are never eligible. On a crash, ambiguous write, missing receipt, unknown lease owner/generation, mismatched schema/revision, or partial merge/activation, cold start enters `maintenance_required`/read-only recovery. It must not replay an uncertain merge, migration, lease transfer, or activation merely because the old release exists. Recovery verifies actual source, journal, lease and data identity; restoring an old program is not proof of data compatibility.

In E1's empty-store case, before any candidate write, a verified release-only recovery may be offered after proving the new store remains empty and unleased. After any possible new write, keep writes stopped until lease ownership and data state are reconciled; do not discard the new store or silently switch it to an old release.

## 7. Model boundary (not E1 acceptance)

The three-file path propagation repair may make project paths available to the client, but does not establish a private or correct provider adapter. A later independent provider-design/implementation slice must use an exact registry allowlist and pin provider, model and base URL; resolve only a private credential reference; reject ambient-key discovery and inline `apiKey` fallback; require request and cost budgets before any real request; and never retry an unknown-timeout request automatically. The already authorized native start and `0x567-flash`-only model test remain separate permissions, not evidence that E1 is complete. No model request is part of E1's activation acceptance.

## 8. Focused acceptance and evidence

Planned focused evidence (not a command-level implementation plan):

- Schema/identity tests prove fixture and production namespaces cannot cross-load, and link/alias/overlap/path substitution is rejected.
- Plan tests prove digest invalidation on any source, dirty/index, baseline, release, path, or data identity change; complete-range presentation never collapses to the three-commit ancestry summary; ambiguous ownership/conflict blocks without Git writes.
- Journal/lease tests inject interruption at each transition, duplicate operation IDs, stale fencing tokens, unknown receipts, and concurrent writers; uncertain outcomes stay maintenance/read-only and are never replayed as success.
- Focused merge/adoption tests use disposable repositories and verify exact approved paths, preservation of unrelated dirty/index/stash state, actual merge receipt, and release provenance matching the post-merge tree.
- Native acceptance within the already-granted start/merge scope verifies production namespace/context, the contract-required full-range scope confirmation, the post-merge release identity, first empty-store lease, active/observing/LKG transitions, and recovery behavior. Callback/fake-child tests are not native acceptance; native readiness is not model, migration, or data-safety acceptance.

Do not run the full suite merely for this slice. Keep focused test results, native runtime evidence, model evidence, and user-data migration acceptance as distinct claims. No implementation, credentials, live model request, real-data access, or stable-tree change is authorized by this draft.

## 9. Approved data decision and implementation boundary

**User decision recorded on 2026-10-24:** approved the private path `%LOCALAPPDATA%/my-agent/managed/<installation-UUID>` and first activation into a separate, initially empty data store. Existing chats, keys, and legacy data stay untouched; no import or migration. Preparation must fail closed on non-local/non-NTFS volumes, permissive ACLs, unknown existing installation roots or unsafe source overlap.

The full-range digest confirmation, source-merge receipt prerequisite, fenced single-writer lease, no-double-write rule, and maintenance-on-uncertainty behavior remain implementation requirements. Existing native-start, project-merge, and `0x567-flash`-only authority remains scoped as granted; real private model requests still need an implemented safe adapter and explicit budget.

The executable first milestone is described in `../plans/2026-10-24-managed-e1-preparation.md`: private namespace creation and complete real-source preflight only, not production launch/activation. Production context, complete write fencing, release activation and recovery remain unaccepted until actually implemented and verified.

## 10. Project-local alternative approved 2026-10-24

The user clarified that runtime files should be allowed inside the project. The earlier fixed AppData location is a constraint of the private-ACL mode, not a requirement of dual versions. This section supersedes any reading of sections 1/9 that makes AppData mandatory for every mode; private mode retains its original checks without fallback.

Initial project-local root: `<sandboxSourceRoot>/.managed-runtime/<installation-UUID>`, inside the user's project and current sandbox-only write boundary. Mode `managed_project_local_v1` explicitly declares `logical_separation_only` and forbids credentials. It accepts a verified local fixed NTFS or exFAT volume without claiming private NTFS ACLs, OS sandboxing, same-user adversary protection or production readiness. Other source/runtime overlaps remain prohibited. Source preflight treats `.managed-runtime` as protected name-only material and ignores it for source capture; runtime removal/relocation is never a side effect of source promotion or sync.

Preparation creates a small empty namespace and identity marker, not another checkout, dependency installation, old-chat copy or runtime binary set. Explicit project prepare/check/inspect commands bind expected source roots and UUID, reject private/fixture cross-load, foreign layout/reuse and populated data. There is no automatic mode detection or security downgrade.

Production source-scope review, project-executed merge receipts, post-merge release provenance, writer fencing, durable activation/recovery and safe provider integration remain unfinished. Project-local preparation does not turn them into user-experience acceptance. Existing old chats/keys stay untouched; only registered `0x567-flash` is authorized for a later separately budgeted safe adapter path, not this preparation.

Implementation and storage measurement plan: `../plans/2026-10-24-project-local-managed-preparation.md`. Historical ACL refusal and prior evidence remain valid for the private mode and are not rewritten as project-local failures.