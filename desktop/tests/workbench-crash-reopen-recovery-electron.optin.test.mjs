/**
 * Real Electron workbench crash/reopen recovery — opt-in guards + investigation contracts (POSIX).
 * GUI kill/reopen: Win32 only when both REAL_ELECTRON_WORKBENCH_RECOVERY_* opt-ins are set.
 */
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import {
  WORKBENCH_RECOVERY_ELECTRON_PROFILE,
  buildEvolutionControlPrepHint,
  countRecoveryWarns,
  sampleRecoveryNoticeFrames,
  validateWorkbenchRecoveryDataRootEntries,
  isEvolutionControlBuildReady,
  requireWorkbenchRecoveryElectronGuiOptIn,
  requireWorkbenchRecoveryElectronOptIn,
  resolveWorkbenchRecoverySandboxRoot,
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
  assert.match(runner, /ensureEvolutionControlBuild/, "runner must ensure evolution control-release before H1 freeze");
  assert.match(runner, /resolveWorkbenchRecoverySandboxRoot/, "runner must resolve evolution sandbox root");
  const optin = readFileSync(path.join(repo, "desktop/scripts/workbench-crash-reopen-recovery-electron.optin.mjs"), "utf8");
  assert.match(optin, new RegExp(`export const WORKBENCH_RECOVERY_ELECTRON_PROFILE = "${WORKBENCH_RECOVERY_ELECTRON_PROFILE}"`));
  assert.match(runner, /session_id:\s*WORKBENCH_RECOVERY_SESSION_ID/);
  assert.match(runner, /assertWorkbenchRecoverySeedLayout/);
  assert.match(runner, /Page\.reload/, "CDP must reload after Network.enable to capture session-open notices");
  assert.match(runner, /reconcileWorkbenchRecoveryRuntimeAppWrites/);
  const verify = readFileSync(path.join(repo, "desktop/scripts/verify-evolution-a.mjs"), "utf8");
  assert.match(verify, /evolve\/user_profile\.md/, "workbench recovery must allow first-run user_profile runtime file");
  const hint = buildEvolutionControlPrepHint(repo);
  assert.match(hint, /control-release\.js/);
  assert.match(hint, /build-evolution-control/);
  assert.match(hint, /tray reopen remains manual/i);
});

test("sandbox root: prefers .worktrees/sandbox when stable lacks control-release", () => {
  const resolved = resolveWorkbenchRecoverySandboxRoot(repo);
  const sandbox = path.join(repo, ".worktrees", "sandbox");
  if (existsSync(path.join(sandbox, "desktop", "package.json")) && !isEvolutionControlBuildReady(repo)) {
    assert.equal(resolved, path.resolve(sandbox));
  }
});

test("post-seed data root allows guard and post-open runtime entries", () => {
  const entries = [
    ".file-guard",
    ".session-guard",
    "sessions",
    "state.json",
    "logs",
    "auto-recognition-preference.sqlite3",
  ].sort();
  assert.equal(validateWorkbenchRecoveryDataRootEntries(entries).ok, true);
  assert.equal(validateWorkbenchRecoveryDataRootEntries(["sessions", "state.json"]).ok, true);
  assert.equal(validateWorkbenchRecoveryDataRootEntries(["sessions", "state.json", "extra.json"]).ok, false);
});

test("warn counter accepts server recovery copy", () => {
  const frames = [
    { payload: JSON.stringify({ type: "notice", level: "warn", text: "未自动续跑 worker" }) },
    { payload: JSON.stringify({ type: "notice", level: "info", text: "ok" }) },
  ];
  assert.equal(countRecoveryWarns(frames), 1);
});

test("notice sampler keeps recovery warn payloads", () => {
  const frames = [
    { payload: JSON.stringify({ type: "notice", level: "warn", text: "无法核对已保存事项的执行状态" }), sequence: 1 },
    { payload: JSON.stringify({ type: "assistant.done", text: "x" }), sequence: 2 },
  ];
  const samples = sampleRecoveryNoticeFrames(frames);
  assert.equal(samples.length, 1);
  assert.match(samples[0].text, /无法核对/);
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
