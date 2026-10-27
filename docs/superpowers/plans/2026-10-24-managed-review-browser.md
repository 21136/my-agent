# Managed source review browser implementation

**Date:** 2026-10-24. Continuation of approved E1 source-range presentation; no new adoption authority.
**Spec:** ../specs/2026-10-24-managed-adoption-e1-design.md sections 4/8/10.
**Goal:** Make existing read-only exact-scope reports practically inspectable in the existing shell-development page, without weakening any merge or ownership gate.

## Constraints
- All writes in existing sandbox only. No real Git writes/status, provider calls, installs, full tests/builds, new repositories, retained fixture cleanup, legacy-data or credential reads.
- Workers use gpt-6-luna, do not recursively delegate. Preserve others' edits.
- Read-only catalog is not an AdoptionPlan, ownership decision, consent or execution receipt. No IPC added; existing no-argument trusted-main-frame review API remains unchanged.
- Plain web has no report acquisition fallback. Source reports render as text only, never executable markup.
- Full exact JSON remains available. Pagination/search never changes the full report or readiness/blockers. Missing/malformed inventories cannot mean empty/clean.

## Task A: pure catalog and pagination
Files: desktop/src/shells/unified/managed-review-catalog.ts; desktop/tests/managed-review-catalog.test.ts.
Contract: createManagedReviewCatalog(report) returns {reviewDigest, baselines:string[], limitations:string[], sections:[{id,label,rows:[{key,path,summary,details}]}],execution:false,readyForMerge:false,readyForLaunch:false}. paginateManagedReviewCatalog(catalog,{sectionId,query,page,pageSize}) returns {rows,total,page,pageCount}. Export types ManagedReviewCatalog, ManagedReviewSectionId. IDs stable-preservation, sandbox-ownership, history, effective-delta, excluded, blockers, commits.
- [ ] RED: pure fixtures prove last-page completeness, full hashes and commit metadata, overlapping blocker paths remain visible, missing states not clean, excluded paths metadata-only, invalid report fails closed, no input mutation.
- [ ] GREEN: strictly validate basic packet identity/false readiness and arrays; transform metadata only; stable/sandbox sections include all nonclean/state/flag differences, never claim task ownership; exclusions per-lane; commits preserve full IDs/parents/tree; delta explicit merge-base basis, not projected merge result.
- [ ] Page size default25, bounded1..100, deterministic filtering literal text not regex; case-insensitive query by path/summary; clamp page after filtering, all rows reachable.
- [ ] Run only pure new tests; no files/fixtures needed.

## Task B: integrate existing read-only panel
Files: desktop/src/shells/unified/managed-review-browser.ts; desktop/src/shells/unified/shell-dev-review.ts; desktop/src/shells/unified/shell-dev-panel.ts; desktop/src/shells/unified/index.ts; desktop/tests/managed-review-browser.test.ts; desktop/tests/shell-dev-review.test.ts; desktop/tests/shell-dev-panel.test.ts.
- [ ] RED: tests for onReport/reset success/error transitions, report replaces instead of appends; semantic section/search/paging/detail controls; malformed input cannot retain prior actionable view. No new adopter controls.
- [ ] GREEN: mountManagedReviewBrowser(container:HTMLElement) returns {show(report):void,clear():void}; real DOM createElement/textContent, no report innerHTML. Buttons for rows show exact path and full hash metadata. Keep bounded report height with ordinary controls/accessible labels, preserve full JSON below in collapsed details.
- [ ] Existing createManagedReviewController gets optional onReport(report) and onReset() callbacks; reset before inspect, errors and web message cannot show stale data; existing caller compatibility preserved.
- [ ] Mount in existing page via new data-managed-review-browser element. Existing web requirement and trusted IPC unchanged. Rendering error leaves safe full text/error and no stale browser.
- [ ] Focused tests only. No full project launch or build.

## Parent integration and verification
- [ ] Read implementation, scoped test rerun and noEmit/type/transpile checks; independent exact-file review by 6luna.
- [ ] Exercise DOM interactions with installed runtime if available. Browser plugin not available in this session; use installed Playwright only, no installation. Component harness/captured source packet remains labeled historical/component evidence, not native/live-source acceptance.
- [ ] Recheck 871 protected source hashes, 3 index/namespace bytes and branch/HEAD/stash metadata against current baseline; no whole-stable historical cleanliness claim.
- [ ] Keep single small evidence directory, no synthetic repos, record storage overhead with exFAT131072-byte rounding clearly distinct from total dual-version footprint.
- [ ] Document remaining engineering: ownership/checkpoint/preservation, native exact consent, operational namespace, real project merge/postmerge build/fencing/activation/recovery and budgeted private provider adapter.

## Refinement during implementation: cross-lane byte comparison
- [ ] Catalog also exports section ID `cross-lane` / 两区逐路径对照, deterministic union of both allowed inventories; generic browser consumes it without a new control or authority.
- [ ] Full valid working SHA256 pairs compare as raw-byte same/different only. Preserve both complete entries and HEAD/index/flags; same bytes never establish staging equality, preservation, ownership or delivery.
- [ ] No entry in one inventory is explicitly not proof of missing filesystem content. `workingSha256:null` means reported missing working file, not an equal hash. Missing/invalid hashes mean unknown. Empty inventories are not inferred from malformed packets.
- [ ] Pure tests distinguish all above. This reduces audit fatigue without weakening the original blockers; historical counts are not current authorization.
