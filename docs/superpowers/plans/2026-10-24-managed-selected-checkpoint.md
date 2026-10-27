# Managed Selected Source Checkpoint Implementation Plan

> **For agentic workers:** Use subagent-driven development with 6luna only. Workers are already delegated; do not recursively delegate.

**Goal:** Advance the existing selected-source workflow to an exact, bounded, project-written content checkpoint without mutating Git or authorizing adoption.
**Architecture:** A canonical single-file content pack embeds the fresh readonly review and exact selection request plus selected raw bytes/modes. The trusted Electron host regenerates the proposal, captures the pack, presents native checkpoint-only confirmation, recaptures the entire review after the dialog, and writes exclusively to a fixed sandbox engineering-artifact directory. This is a content checkpoint, not a Git commit/checkpoint, source merge, or activation.
**Tech Stack:** Existing Node builtins, TypeScript, Electron; no dependency installs.
**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md sections 2,4,8,10; existing MANAGED-SOURCE-SELECTION.md.

## Global Constraints
- Write only in F:/my-agent-main/.worktrees/sandbox. Preserve stable, both real HEADs/raw indices/branches/stashes, unrelated sandbox files, prepared_empty namespace and retained fixtures.
- No assistant add/commit/stash/merge/reset/clean/push; no actual project model calls or legacy/private data reads.
- All subagents gpt-6-luna, no recursive delegation. Focused tests only; no full build/suite/install and no new Git repositories.
- Keep every original selection/history/preservation/absence/protected/ownership blocker. Checkpointing bytes never clears blockers or proves ownership/dependency completeness.
- Capture accepts tracked or untracked ordinary working files with complete working bytes/mode/hash; untracked HEAD/index missing metadata remains a merge blocker. Missing/deleted selected files are unsupported and fail closed.
- Prepared runtime namespace is not operational: use sandbox/artifacts/managed-source-checkpoints only, not .managed-runtime, fixture roots or stable.
- Logical separation only, no same-user adversary or private-ACL guarantees. Canonical hashes bind content, never authenticate origin/consent.

## Task 1: Canonical bounded content checkpoint (worker A)
Files: create desktop/launcher/managed-source-checkpoint.mjs, .d.mts; desktop/tests/launcher/managed-source-checkpoint.test.mjs.

Interfaces:
- validateManagedCheckpointRequest(unknown) -> detached request; exactly {format:1,mode:'managed_source_checkpoint_request_v1',reviewDigest,proposalDigest,selectedPaths}. Both digests lowercase full SHA256; reuse selection request validator; checkpoint limit 128 paths (proposal still allows 2048).
- captureManagedSourceCheckpoint({review,request,stableRoot,sandboxRoot}) -> detached frozen pack. Synchronous, no writes/spawn/Git/network. Regenerate existing createManagedSourceSelectionProposal from request and compare exact proposalDigest. Bind review roots to exact supplied normalized canonical absolute roots; safe ancestors; preserve all review/blockers. Capture only requested sandbox files, at most 4MiB/file, 8MiB aggregate raw. Ordinary single-link files only; no link/reparse/case alias/unsafe parent; bounded FD reads with before/after descriptor/path/ancestor identity checks; re-read selected files at end. Check raw SHA256, Git blob OID (algorithm from OID length; must agree with baseline HEAD format), inventory size and executable-bit-derived mode (same as inventory). Reject drift/missing/invalid metadata, not merely a wrong digest. No source normalization/filter application.
- validateManagedSourceCheckpoint(value) -> detached frozen validated pack, pure. Strict finite acyclic dense JSON/no accessors/prototype/symbol/hidden fields; canonical digest, exact top-level and file keys, size bounds, sorted exact path/file set, canonical base64, raw hash/blob/size/mode equality with regenerated proposal selected entries. Store review + exact checkpoint request and files, rawBytes and false execution/readyForMerge/readyForLaunch/nativeConsent; mode managed_selected_source_checkpoint_v1, format1, checkpointDigest. Do not duplicate full proposal in pack; regenerate to validate.
- persistManagedSourceCheckpoint({sandboxRoot,checkpoint}) -> receipt. Synchronous exclusive no-overwrite one canonical JSON + LF file at fixed sandboxRoot/artifacts/managed-source-checkpoints/<checkpointDigest>.json. Verify current selected file bytes again against pack; validate safe exact ancestors; fixed directory creation only (no recursive loose mkdir), output single-link FD/path identity, bounded progress writes, fsync and reread same descriptor/path bytes and canonical validation. Failure leaves any partial artifact retained, no success or retry/cleanup claim. Existing file fails; do not trust it as earlier success. Receipt {format:1,mode:'managed_source_checkpoint_receipt_v1',reviewDigest,proposalDigest,checkpointDigest,selectedPaths,rawBytes,storedBytes,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false}. No bytes or absolute output paths returned to renderer.
- readManagedSourceCheckpoint({sandboxRoot,checkpointDigest}) -> validated historical pack from fixed location with safe FD/path checks; no live/consent claim.

Tests first red then green; real file fixtures under the milestone artifact directory only, no temp OS directories/repositories/source/dependency copies. One reusable tiny root per test group preferred; preserve fixtures. Test tampered/resigned mismatches, exact keys/request bounds/protected paths, untracked retention, binary/empty bytes, file/raw limits, source drift/missing, safe ancestor/files, canonical reread, no-overwrite, partial/corrupt artifact rejection, raw index/HEAD unaffected (structural no Git imports sufficient; no real repo init). Platform-skips explicit for unsupported link features. Keep artifacts bounded.
Report: artifacts/managed-selected-checkpoint-20261024/task-1-report.md, include commands/red-green counts, files, concerns, storage. No commits.

## Task 2: Trusted native checkpoint path and selection-panel action (worker B after Task1)
Files: new desktop/electron/managed-checkpoint-bridge.ts and focused test; modify main.ts/preload.ts/ws.ts/unified index/selection panel and its test. Do not modify worker A files.
- Strict caller/fixture/arity/request validation before root resolution. One host busy gate across requests; errors sanitized.
- Host roots resolved with existing trusted layout inspector. Fresh full review and regenerated exact proposal check; capture pack with Task1 API. No renderer roots/review/bytes/filename/confirmation accepted.
- Native dialog owned by trusted main window: cancel/default cancel; exact paths, full hashes/modes, raw byte count, review/proposal/checkpoint digests, all blocker codes/counts, checkpoint-only/not merge/not ownership/native adoption consent warning. Confirmation is host-native boolean tied to this captured pack; renderer cannot supply it.
- Recheck caller trust after every await; after confirmation re-inspect full review and recapture same pack, compare digests; no write if review/selection/source/trust changes. No async gap between final check and synchronous persistence.
- Wire IPC shell-dev:managed-source-checkpoint with static validator and host-only dynamic backend import; fixed output location, no old adopter/merge APIs. Do not trigger actual native dialog during this engineering turn.
- Panel optional createCheckpoint API; enable only on same valid proposal and desktop. Button label '保存选定源码检查点（不合入）'. Keep editing enabled; edits/show/clear invalidate pending receipt and old proposal. Validate returned receipt binds exact review/proposal/path set, lowercase checkpoint digest, true checkpointCreated and all false authority flags; never render bytes; safe text only. Stale responses ignored. Native confirmation may still result in a historical checkpoint of the old selection if user edits renderer while dialog open; explain this honestly, never pretend renderer edits roll back host writes.
- No web fallback, auto-selection, automatic persistence or Git calls. Saving does not invoke merge/activation.
- Focused bridge tests cover trust at awaits, stale full review/proposal/capture, cancel, busy/double request, mutation detachment, safe errors and writer ordering. Panel tests cover disabled/stale/malformed/success/cancel. Structural tests for actual host/preload wiring.
Report artifacts/managed-selected-checkpoint-20261024/task-2-report.md.

## Task 3: Controller verification and documentation
- Read changed implementation; independent 6luna review with scoped files/report package, address real findings via original worker.
- Frozen focused Node tests covering new modules plus selection/review/panel regression; Electron noEmit; frontend focused checks and compare pre-existing whole-frontend diagnostics if needed (no full build/suite).
- Fresh actual readonly review and engineering sample capture only if useful; explicitly not user's selected scope/consent. No native dialog/real checkpoint writes or Git changes for user sources this turn.
- Verify baseline stable files, non-owned sandbox files, metadata refs/raw indices/marker unchanged. Count new artifacts and file-cluster footprint with fresh volume allocation unit. Document exact remaining native/source merge/namespace/activation/provider gaps.
- No commit/merge/cleanup.

### Task1 storage-budget refinement (before implementation completion)
The engineering checkpoint store must itself be bounded: maximum8 retained files and32MiB total logical file bytes, checked before new file creation. Only canonical `<64-lowercase-hex>.json` ordinary single-link bounded files are accepted in that store; unknown entries/links or an exhausted budget fail closed. Partial artifacts count toward the budget, are retained, and never treated as a successful earlier write. Do not automatically prune/delete. On F:128KiB allocation units this caps file-cluster rounding overhead below8*128KiB in addition to32MiB logical; directory/FS metadata excluded. Report storage refusals clearly through sanitized checkpoint failure, not a native success. No cleanup UI is part of this slice.
