# Sandbox storage safety implementation plan

**Goal:** Keep stable and experimental source isolation while bounding backup growth and detecting dependency drift.
**Initial approved scope (before follow-up authorization):** Work only in the existing experiment/sandbox checkout. No stable code changes, deletion of existing backups, dependency installation, drive migration, merge or commit.

## Task 1: Snapshot retention
- [x] Reproduce directory pruning failure with generated snapshots, not real sessions.
- [x] Prune only timestamp-named entries inside recognized guard roots; preserve latest, unknown entries and all links/reparse points. Keep 12 historical session directories and 20 historical file snapshots; report removal failures.
- [x] Run only guard-retention and existing file-guard tests.

## Task 2: Recoverable archive tooling
- [x] Test read-only inventory, verified ZIP creation, no-overwrite and source preservation.
- [x] Add agent-core/guard_archives.py: default inventory only; explicit --archive creates a checksummed archive of historical guard snapshots, excluding mutable latest. Never removes source files. Reject links and changing files; verify every archive member before reporting success.
- [x] Validate using disposable fixture data only; inspect real stable backups with inventory mode only.

## Task 3: Dependency preflight
- [x] Test lock-vs-manifest drift, package version drift, platform optional dependencies, Electron binary version, CLI exit status and startup integration.
- [x] Add a dependency-audit CLI without requiring node_modules; compare against package-lock.json, not the other lane. Reject links escaping the desktop dependency root.
- [x] Wire checks into sandbox dev startup and preview startup, remove silent stable dependency fallback. Leave stable launcher unchanged until explicit promotion.
- [x] Run only focused node tests, syntax checks and live read-only audits.

## Verification and delivery
- [x] Document measured space, safe archive usage, exFAT constraints, remaining installed-version drift and promotion boundary.
- [x] Check scoped diff and ensure stable working-tree state was not changed by this work. No full suite or live Electron restart.

## Verified outcome (2026-10-22)

- Focused Python: 20 passed. Focused Node: 12 passed. No full suite.
- Real sandbox dev:preview exits 1 in prehook, before Vite/Electron starts; 21 dependency issues remain. Stable read-only audit passes (157 packages). No npm ci performed.
- Declaration typecheck and syntax checks pass; Electron project typecheck remains blocked by 10 errors in unmodified electron/main.ts. No build/UI acceptance claim.
- Stable guard inventory only: 17,852 historical files / 1,341,719,278 bytes; no real archival or deletion.
- Stable tracked-file SHA-256 comparison: 981 checked, unchanged. No stable write/merge/commit.
- Documentation: docs/SANDBOX-STORAGE.md. Storage migration and real space reclamation remain outside this delivery.

## Final local review and delivery (2026-10-22)

The checked implementation tasks above do not mean release acceptance.

- Fresh focused rerun: Python 20 passed; Node 12 passed, with default NTFS TEMP for link-dependent fixtures. The earlier exFAT TEMP attempt (Python 17 pass / 3 fail; Node 11 pass / 1 fail) is retained, not hidden or counted as passed. No product-source edits were made in this continuation.
- Actual npm build and dev:preview both exit 1 at dependency prehooks. Electron standalone typecheck exits 2 with 10 diagnostics. These are blocked/failed gates, not passed builds or previews.
- Independent external code review did not complete; no independent-review approval.
- Stable content: prior 981 tracked-file baseline unchanged; continuation baseline of 984 tracked/untracked files unchanged.
- Delivery record: docs/reviews/2026-10-22-sandbox-storage-local-review.md. Full suite, dependency installation, UI acceptance, real archive/restore, commit/push/merge remain unperformed.

### Open acceptance gates
- [x] Align sandbox dependencies with its own lockfile and pass deps:check (authorized npm ci + explicit locked Electron binary installation).
- [x] Resolve/retest Electron typing and complete the build (3 minimal preview compatibility fixes; tsc and npm build exit 0).
- [ ] Accept real experimental preview/Electron behavior.
- [ ] Obtain a valid independent code review.
- [ ] With explicit authorization, verify real archival and recovery before retention promotion.
- [ ] Obtain explicit authorization for commit/promotion and synchronize sandbox after merge.

## Dependency follow-up after explicit user authorization (2026-10-22)

- User approved npm ci in the sandbox desktop. Installation succeeded and package-lock remained unchanged; locked Electron binary installation was explicitly completed without stable dependency borrowing.
- Dependency preflight: 157 packages, zero issues. Type errors reduced from 10 to 3 after dependency alignment, then zero after the minimal sandbox-only window-reference/getVisible fixes. Full build exits 0 with documented warnings.
- Fresh tests: Python 20; Node dependency/path 13 + preview policy/panel/zoom 7. No full suite. Added missing-binary guidance regression was observed red before the hint fix, then green; audit remains read-only.
- Isolated Electron 45.0.0 binary/View visibility smoke passes without app windows or sidecar. Renderer 5174 starts and serves real modules; browser page visibly fails on missing Electron preload. Real application/sidecar acceptance remains open.
- npm audit reports 7 package vulnerability entries (4 high, 3 low); no audit fix or lockfile upgrade. Independent review remains incomplete.
- Record: docs/reviews/2026-10-22-sandbox-dependency-followup.md. No real backup mutation, production-app restart, commit/push/merge or filesystem migration.

### Additional open gate
- [ ] Review audit findings and validate an authorized dependency-security remediation; do not equate version alignment with security approval.
