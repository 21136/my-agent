# Workbench crash/reopen — real Electron E2E (machine-gated)

## What is already automated (does **not** kill Electron)

| Layer | Files | Claims |
| --- | --- | --- |
| agent-core | `agent-core/tests/test_workbench_crash_reopen_recovery_harness.py` | Real `session.open` + work-record store; no GUI |
| Desktop render | `desktop/tests/workbench-crash-reopen-recovery.test.mjs` | Panel HTML labels/controls |
| Synthetic WS | `workbench-crash-reopen-recovery-fixture.py` + gui harness (POSIX) | Late WS, overview contract, cancel WS |
| Playwright (Win32) | `workbench-crash-reopen-recovery-browser-e2e.test.mjs` | Full **unified shell** over **synthetic WS** + `ws.close()` + `connect()` — **not** real Electron kill/tray |

Green Win32 browser/harness results **do not** prove real Electron tray/cold-reopen timing.

## What was missing

- No harness launched **real Electron + real `server.py`** with seeded interrupted work records, then **killed the process tree** and **cold-relaunched** into the same isolated profile.
- Tray double-click / single-instance activation is **not** automated here (see `desktop/electron/workbench-single-instance.mjs` unit tests only).

## What this PR adds (Option B — honest machine gate)

- **Profile** `workbench-crash-reopen-recovery-v1` — sealed H1 chat fixture (same control/CDP path as ordinary-chat H1).
- **Seed** `desktop/scripts/workbench-crash-reopen-recovery-electron-seed.py` — one **project-agnostic** companion session + foreign-owner stale run (independent items only).
- **Runner** `desktop/scripts/run-workbench-crash-reopen-recovery-electron.mjs` — freeze → seed → Electron+Python → CDP observe warn + 当前工作 → `taskkill /T` → relaunch → observe again.
- **Tests** `desktop/tests/workbench-crash-reopen-recovery-electron.optin.test.mjs` — POSIX opt-in/contract tests; real GUI test **skipped** unless opt-in on Win32.

Tray reopen remains **manual** until a dedicated UI driver exists; cold relaunch with persisted `electron-userData` is the automated honest proxy.

## Run on Win32 (local)

Prerequisites: repo `.venv` with deps, `desktop/node_modules` + Electron binary, built desktop (runner triggers H1 esbuild/vite freeze).

```bat
set REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN=1
set REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN=1
node desktop\scripts\run-workbench-crash-reopen-recovery-electron.mjs
```

Or via node:test (same env):

```bat
set REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN=1
set REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN=1
node --test desktop\tests\workbench-crash-reopen-recovery-electron.optin.test.mjs
```

Evidence JSON is written under `artifacts/workbench-crash-reopen-recovery/run-<uuid>/`.

## Keep synthetic regression green (POSIX / Win32 CI)

```bash
node --test desktop/tests/workbench-crash-reopen-recovery.test.mjs \
  desktop/tests/workbench-crash-reopen-recovery-gui-harness.test.mjs
# Win32 only:
node --test desktop/tests/workbench-crash-reopen-recovery-browser-e2e.test.mjs
```
