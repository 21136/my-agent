/**
 * Real Electron (sealed H1) workbench crash/reopen recovery — machine-gated, Win32 GUI opt-in.
 *
 * Not CI-default: synthetic WS/browser tests remain the automated contract (see
 * workbench-crash-reopen-recovery-*). This harness kills the real Electron process tree and
 * cold-relaunches into the same isolated profile (tray double-click is manual; documented).
 */
import { randomUUID } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {
  WORKBENCH_RECOVERY_ELECTRON_PROFILE,
  WORKBENCH_RECOVERY_SESSION_ID,
  assertWorkbenchRecoveryWin32GuiTarget,
  captureGuiWebSocketFrames,
  countRecoveryWarns,
  deny,
  forceKillProcessTree,
  requireWorkbenchRecoveryElectronGuiOptIn,
  requireWorkbenchRecoveryElectronOptIn,
  resolveDefaultElectronExecutable,
} from "./workbench-crash-reopen-recovery-electron.optin.mjs";
import {
  assertRunLocalRenderer,
  assertSealedAgentRootMarker,
  buildH1HostBaseEnvironment,
  buildIsolatedGuiEnvironment,
  connectCdp,
  discoverElectronPage,
  resolveH1PythonExecutable,
  verifyOrdinaryChatH1AgentPaths,
  waitForH1Workbench,
} from "./run-ordinary-chat-h1.mjs";
import {
  buildH1ElectronMainOutputs,
  captureStableBaseline,
  freezeHostFixture,
  startFrozenHost,
  verifyFrozenHostFixture,
  verifyStableBaselineUnchanged,
} from "./verify-evolution-a.mjs";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, "../..");
const artifactsRoot = path.join(repoRoot, "artifacts", "workbench-crash-reopen-recovery");

function samePath(a, b) {
  return process.platform === "win32"
    ? path.resolve(a).toLowerCase() === path.resolve(b).toLowerCase()
    : path.resolve(a) === path.resolve(b);
}

function newMarkedRun() {
  if (!existsSync(artifactsRoot)) mkdirSync(artifactsRoot, { recursive: true });
  const runRoot = path.join(artifactsRoot, `run-${randomUUID()}`);
  mkdirSync(runRoot, { recursive: false });
  writeFileSync(path.join(runRoot, ".evolution-test-run"), "evolution-a\n", { flag: "wx" });
  writeFileSync(path.join(runRoot, ".workbench-crash-reopen-recovery.json"), JSON.stringify({
    format: 1,
    run_id: path.basename(runRoot),
    fixture: WORKBENCH_RECOVERY_ELECTRON_PROFILE,
    created_at: new Date().toISOString(),
  }), { flag: "wx" });
  return runRoot;
}

function seedRecoverySession(appRoot, pythonExecutable) {
  const seed = path.join(scriptDir, "workbench-crash-reopen-recovery-electron-seed.py");
  const run = spawnSync(pythonExecutable, [seed, appRoot], {
    cwd: repoRoot,
    env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1", WORKBENCH_RECOVERY_SESSION_ID },
    encoding: "utf8",
  });
  if (run.status !== 0) deny(`recovery seed failed: ${run.stderr || run.stdout}`);
  return JSON.parse(run.stdout.trim());
}

async function observeRecoveryPass(host, verified, runRoot, passLabel) {
  const browserWs = await host.waitForCdp();
  const debugPort = Number(new URL(browserWs.replace(/^ws:/, "http:")).port);
  const discovered = await discoverElectronPage(debugPort, host.renderer_port, runRoot);
  const cdp = await connectCdp(discovered.page.webSocketDebuggerUrl);
  try {
    await cdp.command("Network.enable");
    await cdp.command("Page.enable");
    await cdp.command("Runtime.enable");
    await waitForH1Workbench(cdp, host.renderer_port);
    const deadline = Date.now() + 90_000;
    let warnCount = 0;
    let currentWorkVisible = false;
    while (Date.now() < deadline) {
      const frames = captureGuiWebSocketFrames(cdp.events, host.port);
      warnCount = countRecoveryWarns(frames.receivedFrames);
      const dom = await cdp.command("Runtime.evaluate", {
        expression: `(() => ({status: document.querySelector("#unified-status")?.textContent ?? "", currentWork: !!document.querySelector('button.unified-topbar-current-work')}))()`,
        returnByValue: true,
      }, 5_000);
      currentWorkVisible = dom?.result?.value?.currentWork === true;
      if (warnCount >= (passLabel === "reopen" ? 1 : 1) && currentWorkVisible) break;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    const frames = captureGuiWebSocketFrames(cdp.events, host.port);
    warnCount = countRecoveryWarns(frames.receivedFrames);
    if (warnCount < 1) deny(`${passLabel}: recovery warn notice was not observed on the real Electron WebSocket`);
    if (!currentWorkVisible) deny(`${passLabel}: 当前工作 entry did not mount in the real workbench shell`);
    return { passLabel, warnCount, python_ws_port: host.port, renderer_port: host.renderer_port, frames };
  } finally {
    cdp.close();
  }
}

export async function runWorkbenchCrashReopenRecoveryElectron(options = {}) {
  if (options.env !== undefined && options.env !== process.env) deny("workbench recovery opt-in must come from the real process environment");
  requireWorkbenchRecoveryElectronGuiOptIn(process.env);
  assertWorkbenchRecoveryWin32GuiTarget(process.env);

  const runRoot = newMarkedRun();
  let host = null;
  let verified = null;
  let baseline = null;
  const python = await resolveH1PythonExecutable();
  const electron = resolveDefaultElectronExecutable(repoRoot);
  if (!existsSync(electron)) deny(`Electron executable missing at ${electron}`);

  const api = await import(pathToFileURL(path.join(scriptDir, "verify-evolution-a.mjs")).href);
  try {
    baseline = captureStableBaseline(repoRoot, runRoot);
    const build = await buildH1ElectronMainOutputs({ sandboxRoot: repoRoot, runRoot });
    const frozen = await freezeHostFixture({
      sandboxRoot: repoRoot,
      runRoot,
      trustedPythonExecutable: python,
      electronExecutable: electron,
      chatFixtureProfile: WORKBENCH_RECOVERY_ELECTRON_PROFILE,
    }, { electronBuildRoot: build.output_root, rendererBuild: true });
    verified = verifyFrozenHostFixture({ sandboxRoot: repoRoot, runRoot, ...frozen });
    const frozenInput = { sandboxRoot: repoRoot, runRoot, ...frozen };
    assertSealedAgentRootMarker(verified.app_root);
    assertRunLocalRenderer(JSON.parse(readFileSync(verified.manifest_path, "utf8")), verified.renderer_root, runRoot);

    const profileRoot = verified.profile_root;
    const environmentRoots = {
      profile: profileRoot,
      runRoot,
      agentRoot: verified.app_root,
      userProfile: path.join(profileRoot, "userprofile"),
      appData: path.join(profileRoot, "appdata", "Roaming"),
      localAppData: path.join(profileRoot, "appdata", "Local"),
      home: path.join(profileRoot, "home"),
      xdgConfigHome: path.join(profileRoot, "xdg-config"),
      xdgDataHome: path.join(profileRoot, "xdg-data"),
      xdgCacheHome: path.join(profileRoot, "xdg-cache"),
      temp: path.join(profileRoot, "tmp"),
    };
    buildIsolatedGuiEnvironment(buildH1HostBaseEnvironment(), environmentRoots);
    verifyOrdinaryChatH1AgentPaths(api, verified);

    const seedMarker = seedRecoverySession(verified.app_root, python);
    if (seedMarker.project_bound) deny("companion seed must stay project-agnostic");
    if (seedMarker.conversation_id !== WORKBENCH_RECOVERY_SESSION_ID) deny("seed session id mismatch");

    host = await startFrozenHost(frozenInput, { cdp: { enabled: true } });
    const firstPass = await observeRecoveryPass(host, verified, runRoot, "first_launch");
    await forceKillProcessTree(host.child);
    try { await host.close(); } catch { /* process tree was killed */ }
    rmSync(path.join(runRoot, "host", "logs"), { recursive: true, force: true });

    host = await startFrozenHost(frozenInput, { cdp: { enabled: true } });
    const reopenPass = await observeRecoveryPass(host, verified, runRoot, "reopen");
    if (reopenPass.warnCount < 1) deny("reopen did not observe a recovery warn after real process kill");

    const exit = await host.close();
    host = null;
    if (exit.forced_termination === true) deny("H1 close should not require forced termination after CDP shutdown");

    verifyStableBaselineUnchanged(baseline, captureStableBaseline(repoRoot, runRoot));

    const evidence = {
      format: 1,
      status: "real_electron_kill_reopen_recovery_observed",
      scope: "sealed H1 Electron + real Python sidecar; process tree kill + cold relaunch; not synthetic WS fixture",
      synthetic_ws_equivalent: false,
      tray_reopen_automated: false,
      profile: WORKBENCH_RECOVERY_ELECTRON_PROFILE,
      session_id: WORKBENCH_RECOVERY_SESSION_ID,
      companion_project_bound: false,
      first_launch: { warn_count: firstPass.warnCount },
      reopen: { warn_count: reopenPass.warnCount },
      run_root: runRoot,
      created_at: new Date().toISOString(),
    };
    writeFileSync(path.join(runRoot, "workbench-crash-reopen-recovery-electron-evidence.json"), `${JSON.stringify(evidence, null, 2)}\n`, { flag: "wx" });
    (options.output ?? process.stdout).write(`${JSON.stringify(evidence, null, 2)}\n`);
    return evidence;
  } finally {
    if (host) try { await host.close(); } catch { /* preserve primary failure */ }
  }
}

if (process.argv[1] && samePath(path.resolve(process.argv[1]), fileURLToPath(import.meta.url))) {
  runWorkbenchCrashReopenRecoveryElectron().catch(error => {
    console.error(`Workbench crash/reopen Electron harness failed: ${error.code ?? "fixture_error"}: ${error.message}`);
    process.exitCode = 1;
  });
}
