/**
 * Real Electron workbench crash/reopen recovery — opt-in guards + investigation contracts (POSIX).
 * GUI kill/reopen: Win32 only when both REAL_ELECTRON_WORKBENCH_RECOVERY_* opt-ins are set.
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  WORKBENCH_RECOVERY_ELECTRON_PROFILE,
  countRecoveryWarns,
  requireWorkbenchRecoveryElectronGuiOptIn,
  requireWorkbenchRecoveryElectronOptIn,
} from "../scripts/workbench-crash-reopen-recovery-electron.optin.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");

test("opt-in: workbench recovery Electron requires explicit env", () => {
  assert.throws(() => requireWorkbenchRecoveryElectronOptIn({}), /REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN=1/);
  assert.throws(() => requireWorkbenchRecoveryElectronGuiOptIn({ REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN: "1" }),
    /REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN=1/);
  assert.doesNotThrow(() => requireWorkbenchRecoveryElectronGuiOptIn({
    REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN: "1",
    REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN: "1",
  }));
});

test("coverage map: synthetic vs real Electron recovery", () => {
  assert.equal(WORKBENCH_RECOVERY_ELECTRON_PROFILE, "workbench-crash-reopen-recovery-v1");
  const browser = readFileSync(path.join(here, "workbench-crash-reopen-recovery-browser-e2e.test.mjs"), "utf8");
  assert.match(browser, /not real Electron kill/);
  const runner = readFileSync(path.join(repo, "desktop/scripts/run-workbench-crash-reopen-recovery-electron.mjs"), "utf8");
  assert.match(runner, /synthetic_ws_equivalent:\s*false/);
  assert.match(runner, /tray_reopen_automated:\s*false/);
  assert.match(runner, /WORKBENCH_RECOVERY_ELECTRON_PROFILE/, "runner must use the shared opt-in profile constant");
  const optin = readFileSync(path.join(repo, "desktop/scripts/workbench-crash-reopen-recovery-electron.optin.mjs"), "utf8");
  assert.match(optin, new RegExp(`export const WORKBENCH_RECOVERY_ELECTRON_PROFILE = "${WORKBENCH_RECOVERY_ELECTRON_PROFILE}"`));
});

test("warn counter accepts server recovery copy", () => {
  const frames = [
    { payload: JSON.stringify({ type: "notice", level: "warn", text: "未自动续跑 worker" }) },
    { payload: JSON.stringify({ type: "notice", level: "info", text: "ok" }) },
  ];
  assert.equal(countRecoveryWarns(frames), 1);
});

test("opt-in real Electron kill + cold reopen GUI", {
  skip: process.env.REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN === "1"
    && process.env.REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN === "1"
    && process.platform === "win32"
    ? false
    : "Win32 local only: set REAL_ELECTRON_WORKBENCH_RECOVERY_OPT_IN=1 and REAL_ELECTRON_WORKBENCH_RECOVERY_GUI_OPT_IN=1",
  timeout: 600_000,
}, async () => {
  const { runWorkbenchCrashReopenRecoveryElectron } = await import("../scripts/run-workbench-crash-reopen-recovery-electron.mjs");
  const evidence = await runWorkbenchCrashReopenRecoveryElectron();
  assert.equal(evidence.status, "real_electron_kill_reopen_recovery_observed");
  assert.equal(evidence.companion_project_bound, false);
  assert.ok(evidence.first_launch.warn_count >= 1);
  assert.ok(evidence.reopen.warn_count >= 1);
});
