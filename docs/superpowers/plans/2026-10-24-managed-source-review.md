# Managed source review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Make a complete, digest-bound, read-only review of allowed source bytes available without asking Git status to read protected working material.
**Architecture:** Add a conservative raw-byte inventory beside the existing blocked inspector; do not relax its adoption gate. Compose history, merge-base tree metadata and both source inventories into a read-only review packet. Native IPC uses host-owned roots, no renderer-supplied paths/permission flags, and never calls the adopter.
**Tech Stack:** Existing Node ESM, Electron/TypeScript, node:test; no new dependency.
**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md sections 2, 4, 8, 10.

## Global Constraints
- All code edits and evidence inside F:/my-agent-main/.worktrees/sandbox only; stable HEAD/index/worktree preserved.
- Only gpt-6-luna subagents. Focused tests only; no installs, real Git writes, model requests or old-user-data reads.
- Existing fixture/private namespace modules and E0 adoption behavior remain unchanged.
- No git status, worktree diff, textconv, filters, hooks, source config reads or protected blob/working-byte reads in the new inventory.
- Index/tree names, modes and object IDs are metadata. Read only allowlisted regular source files; reject links, ambiguous paths, conflicts, unsupported modes and bounded-resource overflow.
- Raw Git-blob comparison is NOT Git clean-filter/EOL-normalized status. Report conservative raw divergence and require review; never infer task ownership.
- effectiveDelta means merge-base-to-sandbox-HEAD tree delta, NOT the prospective merged stable tree or an approved delivery list.
- All packets explicitly say execution=false, readyForMerge=false, readyForLaunch=false. Review digest is not an execution receipt.
- Preserve existing blocker semantics including incomplete full-worktree inventory. Out-of-scope material stays metadata-only/unknown.
- Do not commit this plan or implementation; no branch/worktree creation, broad staging, stash or cleanup.

## Task 1: Privacy-safe source inventory (worker inventory)
**Files:** Create desktop/launcher/managed-source-inventory.mjs and desktop/tests/launcher/managed-source-inventory.test.mjs.
**Interfaces:** captureManagedSourceInventory({root,head,indexDigest}) returns {format:1,mode:'managed_allowed_source_inventory_v1',root,head,indexDigest,entries,excluded,limitations,blockers,inventory_digest}. Each entry includes path, headOid/indexOid when applicable, workingSha256, workingOid, state, mode/size as applicable. Missing working files are explicit. Excluded paths have names/classification only, never contents or content hashes. captureManagedTreeDelta({root,base,head}) returns {basis:'merge_base_to_sandbox_head',base,head,paths}; paths include status, path, old/new object identities/modes; use metadata-only tree comparison with no rename similarity/content reads.
- [ ] Add tests in owned disposable repositories: protected tracked files + hostile clean filter execute zero times; metadata-only exclusions; staged/unstaged/untracked/deleted/CRLF raw changes; conflicts; rename represented delete/add; links and limits fail closed.
- [ ] Observe failing import/API assertions before implementation; save red evidence inside artifacts/managed-review-20261024.
- [ ] Implement policy, bounded traversal and deterministic canonical digest; verify expected HEAD/index before/after capture. Never traverse excluded directories; no real Git mutations.
- [ ] Run only the new test file and syntax check, save green log and list changed files.

## Task 2: Review packet + explicit CLI (worker review)
**Files:** Create desktop/launcher/managed-review.mjs, managed-review.d.mts, desktop/tests/launcher/managed-review.test.mjs; modify desktop/launcher/managed-cli.mjs and desktop/tests/launcher/managed-cli.test.mjs only.
**Interfaces:** inspectManagedReview({stableRoot,sandboxRoot}) returns {format:1,mode:'managed_source_review_v1',review_digest,inspectionDigest,stable,sandbox,history,effectiveDelta,blockers,execution:false,readyForMerge:false,readyForLaunch:false}. stable/sandbox include identity/head/branch/indexDigest and inventory. history is original committedRange; preserve original blockers. revalidateManagedReview(packet) recomputes the report, rejects malformed/tampered or drifted packets. No timestamp in bound packet.
- [ ] Test packet composition with allowed inventory despite protected tracked files, blocked execution, net delta versus history, raw-byte/index/untracked drift, digest tampering. Start red.
- [ ] Compose original inspector plus Task 1 APIs. Validate old inspection again after new capture; deterministic packet digest. Revalidation must verify packet's own canonical digest before recomputation.
- [ ] Add --review-project <stableRoot> <sandboxRoot> <UUID> to existing CLI, requiring original project namespace read/check before and after, binding namespace digest to returned review. Print full review JSON (not a truncated summary); no disk write/confirmation/launch. Return 3 for blocked packet; retain old command contracts.
- [ ] Run focused CLI and packet tests, save log. Do not modify source-plan/private/fixture modules.

## Task 3: Native read-only review entry (worker UI)
**Files:** New desktop/electron/managed-review-bridge.ts, desktop/src/shells/unified/shell-dev-review.ts, desktop/tests/managed-review-bridge.test.ts, desktop/tests/shell-dev-review.test.ts. Modify main.ts, preload.ts, src/api/ws.ts, src/shells/unified/index.ts, shell-dev-panel.ts, tests/shell-dev-panel.test.ts only.
**Interfaces:** New no-argument inspectManagedSourceReview() preload method invokes shell-dev:managed-source-review. Main supplies trusted roots resolved from fixed context or code root (.worktrees/sandbox -> owning stable root; stable -> .worktrees/sandbox), validates caller with existing trusted main-frame check. Bridge can be injected with read-only inspector for focused tests; does not import adopter. Return {ok:true,report} or sanitized {ok:false,message}.
- [ ] Test trust rejection before inspector call, reject any IPC arguments, fixed-fixture context not reinterpreted as production, root derivation (including rejecting foreign layout), inspector failures sanitized and busy state.
- [ ] Add read-only button and bounded-height, scrollable full report region in existing shell-dev page; use textContent, not innerHTML for report. No truncation of exact path set; no confirm/execute actions and no renderer-supplied roots.
- [ ] Explicitly distinguish current net tree delta / historical touched paths / raw-byte local differences and limitations; show review digest and blockers. Plain web preview reports desktop required, no fetch/Git fallback.
- [ ] Wire IPC/preload/view controller without changing old merge/fixture path. Run focused bridge/view/panel tests and TypeScript noEmit, not full build.

## Integration / parent verification
- [ ] Review worker changes and run selected inventory/review/CLI/native-view tests from sandbox root; retain separate red and green evidence.
- [ ] Independent security/spec review of new exact files; repair findings with focused tests.
- [ ] Run new real --review-project via API to save full report and concise proof; no launch/adopt. Recheck project marker and targeted stable source/index/HEAD bytes. This is engineering verification, not real UI acceptance.
- [ ] Update PROJECT-LOCAL-RUNTIME.md and evidence result with exact open gates: production context, reviewed merge transaction/receipt, actual postmerge build provenance, full fenced writes, activation/recovery still required.

## Rulings
- Continue the already authorized E1 engineering plan, not a new architectural approval cycle.
- The new packet improves read-only review while preserving the original conservative execution blocker; it is not a production AdoptionPlan.
- Use file evidence instead of a new shared .superpowers workspace, to keep all artifacts inside the existing explicit sandbox boundary and retain inspection receipts.

### Additional privacy ruling during execution
Metadata-only review also bypasses Git status on source-only repositories: a clean filter for an allowed source path may execute arbitrary code, including reading unrelated private paths. The original inspector gets an explicit strict metadataOnlyHistory option carried through digest/revalidation; the review uses it, status remains unknown, and history disables rename similarity. Default legacy inspection remains unchanged. This is not a relaxation of its complete-inventory blocker.

### Engineering progress before final evidence capture
- Task 1 implemented and focused green; explicit platform skips retained. Final resource-count preflight correction is a narrow review fix, not a new feature.
- Task 2 code/CLI implemented, metadata-only status/history preserved, packet/CLI tests green; original protected-filter calibration now has an explicit non-writing hash-object fallback if status does not hash on the platform.
- Task 3 implemented, reviewed count-label correction completed; actual native rendering remains unrun.
- Native read-only project CLI returned code 3 with a full report, namespace marker unchanged, 180 metadata Git calls with none of status/find-renames/cat-file/textconv. Final evidence is regenerated after source/doc freeze.
- Production adoption/activation implementation remains outside this bounded read-only milestone. No actual merge, model call or old-data access occurred.
