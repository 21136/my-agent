# Managed E1 Private Namespace and Source Preflight Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Complete focused RED/GREEN and independent review. No Git write/commit.

**Goal:** Deliver an executable project-owned preparation/preflight path for the approved private, initially empty managed installation, without pretending it activates a production Host.
**Architecture:** New managed-only modules; fixture validators and runtime remain unchanged. Prepare a private local NTFS namespace, bind its source roots, then inventory the complete real Git range and dirty/index state with digest invalidation. This milestone ends at exact-source-scope review, before merge, release activation, lease acquisition or any user-data write.
**Tech Stack:** Existing Node ESM, node:test and Windows PowerShell/.NET ACL/volume APIs; no dependencies.
**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md (user approved proposed private path and empty-first-store on 2026-10-24).

## Global Constraints
- Experimental edits only F:/my-agent-main/.worktrees/sandbox. Preserve all existing dirty files, index and stashes in both trees.
- All subagents gpt-6-luna. No whole-suite tests, installation of dependencies or assistant-run Git writes.
- Old chats/keys untouched: never enumerate/read/copy legacy stores, real keys or config. No model call in this milestone; subsequent requests only registered 0x567-flash and require private adapter/budget.
- Private target is exactly LOCALAPPDATA/my-agent/managed/<installation UUID>, local NTFS and private ACL; no repo/removable/network fallback. Source-source nesting is allowed; private roots cannot overlap either source.
- An empty directory is not an initialized data store or writer lease. No Host start, fixture-gate widening, active/LKG pointer, or migration in this preparation path.
- Tests use owned sandbox artifacts and synthetic dependencies for OS capability/ACL failures; real OS checks stay mandatory in CLI.
- Never claim a synthetic check as native launch, source merge or production activation acceptance.

## Task 1: Private managed namespace preparation
Files: create desktop/launcher/managed-namespace.mjs and desktop/tests/launcher/managed-namespace.test.mjs. Do not change fixture modules.
Interfaces:
- managedNamespacePath(localAppData, installationId): absolute approved path.
- prepareManagedNamespace({localAppData,installationId,stableSourceRoot,sandboxSourceRoot}, dependencies?): returns immutable JSON record after successful platform/ACL/layout verification.
- readManagedNamespace(root, dependencies?): validates and returns the record; default localAppData is current user's LOCALAPPDATA.
- managedNamespaceDigest(record): canonical SHA256.
Record format=1, mode=managed_installation_v1, stage=prepared_empty, data_policy=empty_no_migration, installation_id, root, source_roots={stable,sandbox}, paths={launcher,state,releases,host_state,host_profile,host_workspace,data}. Fixed subpaths: launcher/, state/, releases/, host/state/, host/profile/, host/workspace/, data/store/.
- [x] RED: invalid UUID/root, fixture cross-load, source overlap, non-NTFS/network volume, permissive ACL, pre-existing installation, altered marker/layout, symlink/reparse, nonempty data all fail without changing source or legacy data.
- [x] Implement fail-closed Windows volume/current-user ACL probes using constant PowerShell scripts and JSON stdin (no string shell paths), sequential checked directory creation, exclusive final marker publication and no recursive deletion/reuse. Verify installation tree has no links and initialized data is still empty. Do not chmod existing unrelated parent directories.
- [x] GREEN: successful synthetic OS checks create exactly the allowed owned layout; native OS probes tested separately at actual authorized preparation. Save focused logs.

## Task 2: Complete read-only source inspection and scope plan
Files: create desktop/launcher/managed-source-plan.mjs and desktop/tests/launcher/managed-source-plan.test.mjs.
Interfaces:
- inspectManagedSources({stableRoot,sandboxRoot}): JSON report, format=1, mode=managed_source_inspection_v1; stable/sandbox identity+HEAD+branch+index digest+dirty entries; complete committed range/path set; blockers; snapshot_digest.
- revalidateManagedInspection(inspection): recompute and reject any digest/baseline drift.
- deriveManagedScopePlan({installationId,namespaceDigest,inspection,deliveryPaths}): canonical digest-bound scope proposal; no Git write or execution.
- [x] RED with disposable sandbox Git repositories: changed file/index/HEAD/branch/untracked bytes invalidates digest; full committed range cannot be hidden by a short log or excluded path; common-dir mismatch and detached/wrong sandbox branch reject; real source nesting works; protected paths are reported by name only, never opened.
- [x] Implement bounded literal read-only Git subprocesses with sanitized environment (no hooks, external diff/filter invocation), regular-file hashing only for source allowlisted paths. Respect E0 source policy: exclude data, workspace/profile, .env, key/cert, .git/.venv/node_modules/artifacts/dist caches. Rename source/destination and staged/index identity must be represented. Any sensitive/unsupported path, ambiguous unowned dirty state, conflict or unfinished Git operation blocks rather than auto-stage/stash/merge.
- [x] GREEN: selected scope must account for all committed merge paths; unselected dirty paths remain explicitly excluded/blocked, never implicitly owned. Plan contains exact per-path hashes and complete baseline. Tests must not read real repo state or credentials.

## Task 3: Project-owned preparation/preflight CLI
Files: create desktop/launcher/managed-cli.mjs and desktop/tests/launcher/managed-cli.test.mjs; update only the approved E1 spec status and write an evidence report after verification.
Interfaces: runManagedCli(args, {write, ...trustedTestDependencies}={}): Promise<{code,...}>; entrypoint dispatch only when main.
Commands: --prepare <stableRoot> <sandboxRoot> <installationUUID>; --check <installationRoot>; --inspect <installationRoot>. No generic launch/adopt command. Explicitly reject launch/activation with reason, never call fixture lifecycle as a fallback.
- [x] RED: malformed CLI/no roots/identity mismatch/OS error cannot write unrelated paths; check is read-only; inspect is strictly read-only, returns the full report through its API and a bounded summary with ready_for_launch=false and source scope blockers.
- [x] Implement composition using Task 1/2 exports; live CLI has no platform capability bypass flags or provider/env dumps. Prepare first validates source identity, then creates new private namespace. Inspect never writes the private namespace; the caller may save evidence only in its already-owned sandbox artifacts.
- [x] GREEN: integration test synthetic capability adapter only, no native launch/legacy reads. Run existing fixture integrity/preparation targeted tests unchanged to prove no gate weakening.

## Task 4: Independent verification and authorized real preparation
- [ ] Review each new module against approved spec and this milestone. Fix important findings before native preparation. Re-run focused tests and syntax/diff checks, not full suite.
- [ ] Capture stable HEAD/branch/status and tracked-file hashes before and after; preserve original bytes of any existing sandbox file modified.
- [ ] Generate one installation UUID, persist it only in owned sandbox evidence, then run project managed-cli --prepare via current Node with real Windows probes. Never reuse an unknown installation, bypass failing NTFS/ACL checks, or inspect legacy data.
- [ ] Run project --check and --inspect for that exact returned installation. Save receipts, actual source scope blockers and explicit not-done list. No broad Git action to clear a blocker.

## Milestone limits and next engineering
This is a working preparation/preflight subsystem, not a mock launcher or completion of all E1. Source-scope ownership must be resolved through the project's trusted review; afterward implement production release provenance/context, app-wide fenced writes and durable activation/recovery, then native lifecycle acceptance. Private model adapter remains a separately bounded integration. No partial primitive grants permission to launch/write using legacy or fixture state.

## Review ledger

- Ruling: inspection is strictly read-only; remove private report publication rather than trying to fix a check/use directory-replacement race with another path check. Reason: this milestone performs no adoption or activation and needs no durable side-effect receipt. Cost if wrong: a subsequent adoption command must implement a separately reviewed durable-plan publication boundary before any Git write; the preflight report itself is not such a receipt.
- Independent fixture regression: 36 passed, 0 failed, 1 skipped (exFAT junction unsupported); no native/production claim.

- Ruling: preparation is trusted current-user directory provisioning, not an OS sandbox against malicious concurrent same-account processes. No candidate is started and no secrets or legacy data enter the namespace. Marker publication still uses path-based filesystem operations; this residual limitation is disclosed and blocks treating preparation as completed production isolation/write fencing. Cost if wrong: a same-account malicious process could redirect a concurrent preparation write; production activation must get a separately reviewed handle-bound write boundary before running candidates.

## Verified result

- Tasks 1–3: new namespace/inspection/CLI modules and tests implemented; independent final review has no actionable P1/P2 within disclosed preparation-only threat model. New focused tests 23 passed/0 failed; existing fixture regression 36 passed/0 failed/1 exFAT junction skip. Initial synthetic integration failures were repaired and final targeted runs pass.
- Real project command --prepare attempted for installation a891c344-5b4f-4293-a430-8ab27afa33b8; refused ACL_UNSAFE before UUID directory creation. No alternate location or existing ACL change. Real parent ACL has a non-allowlisted effective FullControl ACE; the failure is not merely read-only inheritance.
- Real source inspection saved to artifacts/fixed-control-e1-20261024/managed-preparation/real-source-inspection.json. Stable 23 dirty paths, sandbox 164 at inspection; historical range union is not the net merge delta. No ownership inferred and no Git writes executed.
- Namespace --check/--inspect blocked because no installation was created. Production context, fenced writes, activation/recovery and private model adapter remain unimplemented/unaccepted.
- Incident: an early namespace-test path error created three empty synthetic fixture trees under .worktrees/artifacts outside sandbox. No files were present on parent verification. Attempted preservation/move was rejected by tool policy; no bypass/retry or deletion performed. See misplaced-fixture-incident.json.

## Subsequent location clarification (2026-10-24)

The private-mode ACL refusal above remains correct for that implementation. The user subsequently approved an explicit project-local alternative. See `2026-10-24-project-local-managed-preparation.md`: separate namespace mode under sandbox/.managed-runtime with logical separation only; original private NTFS/ACL validators remain unchanged. This milestone did not and does not complete production activation, real stable adoption or model acceptance.