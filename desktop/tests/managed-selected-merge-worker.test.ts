import assert from "node:assert/strict";
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, rmdirSync, unlinkSync, writeFileSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Worker } from "node:worker_threads";
import { test } from "node:test";
import { performance } from "node:perf_hooks";
import { inspectManagedReview } from "../launcher/managed-review.mjs";
import { createManagedSourceSelectionProposal } from "../launcher/managed-source-selection.mjs";
import { captureManagedSourceCheckpoint } from "../launcher/managed-source-checkpoint.mjs";
import { prepareManagedSelectedMerge } from "../launcher/managed-selected-merge.mjs";

const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(desktopRoot, "..");
const workerEntry = path.join(desktopRoot, "electron", "managed-selected-merge-worker.mjs");
function cleanupTestDir(dir: string, knownFile: string) {
  const resolved = path.resolve(dir), tempRoot = path.resolve(tmpdir());
  assert.equal(path.dirname(resolved), tempRoot, "cleanup target must be a direct child of the OS temp directory");
  assert.equal(path.resolve(knownFile), path.join(resolved, "backend.mjs"), "cleanup is limited to the known fixture file");
  const entry = lstatSync(resolved);
  assert.ok(entry.isDirectory() && !entry.isSymbolicLink(), "refuse cleanup if temp directory became a link");
  unlinkSync(knownFile);
  rmdirSync(resolved);
}
function startWorker(backendSource: string, authorize: (round: number) => boolean) {
  const dir = mkdtempSync(path.join(tmpdir(), "selected-merge-worker-test-"));
  const backendPath = path.join(dir, "backend.mjs");
  writeFileSync(backendPath, backendSource, "utf8");
  const worker = new Worker(workerEntry, { workerData: {
    mode: "managed_selected_merge_worker_v1", backendPath, plan: { planDigest: "confirmed-plan" },
    checkpoint: { checkpointDigest: "confirmed-checkpoint" }, stableRoot: "stable-root", sandboxRoot: "sandbox-root",
    directory: "store-dir", gitExecutable: "git.exe",
  } });
  let rounds = 0;
  const result = new Promise<any>((resolve, reject) => {
    worker.on("message", message => {
      if (message?.type === "authorization_required") {
        rounds++;
        const signal = new Int32Array(message.signal);
        Atomics.store(signal, 0, authorize(rounds) ? 1 : 2); Atomics.notify(signal, 0);
      } else if (message?.type === "finished" || message?.type === "failed") resolve(message);
    });
    worker.once("error", reject);
    worker.once("exit", code => { if (code !== 0) reject(new Error(`worker exited ${code}`)); });
  }).finally(async () => { await worker.terminate(); cleanupTestDir(dir, backendPath); });
  return { result, rounds: () => rounds };
}



const artifactRoot = path.resolve(desktopRoot, "../artifacts");
function fixtureGit(cwd: string, ...args: string[]) {
  const executable = "C:/Program Files/Git/cmd/git.exe";
  assert.ok(existsSync(executable), "real Git for Windows is required for the isolated fixture");
  const env = { PATH: path.dirname(executable), SYSTEMROOT: process.env.SYSTEMROOT, WINDIR: process.env.WINDIR,
    TEMP: process.env.TEMP, TMP: process.env.TMP, HOME: cwd, USERPROFILE: cwd,
    GIT_CONFIG_NOSYSTEM: "1", GIT_CONFIG_GLOBAL: "NUL", GIT_TERMINAL_PROMPT: "0", LC_ALL: "C" };
  const result = spawnSync(executable, ["-c", `safe.directory=${cwd}`, "-c", "core.hooksPath=NUL", "-c", "commit.gpgSign=false", "-c", "protocol.allow=never", "-c", "gc.auto=0", "-c", "maintenance.auto=false", ...args], { cwd, env, encoding: "utf8", windowsHide: true });
  assert.equal(result.status, 0, `fixture git ${args.join(" ")} failed: ${result.stderr}`);
  return result.stdout.trim();
}
function cleanupArtifactFixture(root: string) {
  const resolved = path.resolve(root), allowedParent = path.resolve(artifactRoot);
  assert.equal(path.dirname(resolved), allowedParent, "cleanup must be limited to this unique direct child of sandbox artifacts");
  assert.equal(realpathSync(resolved), resolved, "refuse cleanup if fixture path is redirected");
  const stat = lstatSync(resolved);
  assert.ok(stat.isDirectory() && !stat.isSymbolicLink() && !(stat.attributes & 0x400), "refuse cleanup if fixture root became a link/reparse point");
  rmSync(resolved, { recursive: true, force: false });
}

test("worker performs a real selected-content fast-forward in an F-drive isolated Git worktree", {
  skip: process.env.RUN_REAL_MANAGED_SELECTED_MERGE_WORKER !== "1",
}, async t => {
  assert.equal(realpathSync(artifactRoot), artifactRoot, "fixture parent must resolve to the sandbox artifacts directory");
  const root = mkdtempSync(path.join(artifactRoot, "managed-selected-merge-worker-"));
  t.after(() => cleanupArtifactFixture(root));
  const stableRoot = path.join(root, "project"), sandboxRoot = path.join(stableRoot, ".worktrees", "sandbox");
  const selectedPath = "desktop/selected.txt", unselectedPath = "docs/unselected.txt";
  mkdirSync(path.dirname(path.join(stableRoot, selectedPath)), { recursive: true }); mkdirSync(path.dirname(path.join(stableRoot, unselectedPath)), { recursive: true });
  writeFileSync(path.join(stableRoot, selectedPath), "base\n"); writeFileSync(path.join(stableRoot, unselectedPath), "stable base\n");
  fixtureGit(stableRoot, "init", "-b", "main"); fixtureGit(stableRoot, "config", "user.name", "Isolated fixture");
  fixtureGit(stableRoot, "config", "user.email", "fixture@example.invalid"); fixtureGit(stableRoot, "add", selectedPath, unselectedPath);
  fixtureGit(stableRoot, "commit", "-m", "isolated base"); mkdirSync(path.dirname(sandboxRoot), { recursive: true });
  fixtureGit(stableRoot, "worktree", "add", "-b", "experiment/sandbox", sandboxRoot, "HEAD");
  writeFileSync(path.join(sandboxRoot, selectedPath), "sandbox selected bytes\n");
  writeFileSync(path.join(sandboxRoot, unselectedPath), "sandbox unselected bytes\n");

  const review = await inspectManagedReview({ stableRoot, sandboxRoot });
  const proposal = createManagedSourceSelectionProposal({ review, request: { format: 1, mode: "managed_source_selection_request_v1", reviewDigest: review.review_digest, selectedPaths: [selectedPath] } });
  const checkpoint = captureManagedSourceCheckpoint({ review, request: {
    format: 1, mode: "managed_source_checkpoint_request_v1", reviewDigest: review.review_digest,
    proposalDigest: proposal.proposal_digest, selectedPaths: [selectedPath],
  }, stableRoot, sandboxRoot });
  const prepareStart = performance.now();
  const plan = prepareManagedSelectedMerge({ stableRoot, sandboxRoot, checkpoint, gitExecutable: "C:/Program Files/Git/cmd/git.exe" });
  const prepareMs = performance.now() - prepareStart;
  assert.deepEqual(plan.selectedPaths, [selectedPath]);
  const stableHeadBefore = fixtureGit(stableRoot, "rev-parse", "HEAD");
  let confirmations = 0;
  let previousAuthorizationAt: number | undefined;
  const authorizationIntervalsMs: number[] = [];
  const worker = new Worker(workerEntry, { workerData: {
    mode: "managed_selected_merge_worker_v1", backendPath: path.join(desktopRoot, "launcher", "managed-selected-merge.mjs"),
    plan, checkpoint, stableRoot, sandboxRoot, gitExecutable: "C:/Program Files/Git/cmd/git.exe",
    directory: path.join(sandboxRoot, "artifacts", "managed-selected-merges", plan.planDigest),
  } });
  const executeStart = performance.now();
  const result = await new Promise<any>((resolve, reject) => {
    worker.on("message", message => {
      if (message?.type === "authorization_required") {
        confirmations++;
        const now = performance.now();
        if (previousAuthorizationAt !== undefined) authorizationIntervalsMs.push(now - previousAuthorizationAt);
        previousAuthorizationAt = now;
        const signal = new Int32Array(message.signal); Atomics.store(signal, 0, 1); Atomics.notify(signal, 0);
      } else if (message?.type === "finished" || message?.type === "failed") resolve(message);
    });
    worker.once("error", reject);
    worker.once("exit", code => { if (code !== 0) reject(new Error(`worker exited ${code}`)); });
  }).finally(async () => { await worker.terminate(); });
  const workerExecuteTotalMs = performance.now() - executeStart;

  assert.equal(result.type, "finished", `real isolated worker failed: ${JSON.stringify(result.receipt)}`);
  assert.equal(result.receipt.status, "source_merge_verified"); assert.equal(result.receipt.sourceHistoryMerged, false);
  assert.notEqual(fixtureGit(stableRoot, "rev-parse", "HEAD"), stableHeadBefore);
  assert.equal(fixtureGit(stableRoot, "show", `HEAD:${selectedPath}`), "sandbox selected bytes");
  assert.equal(readFileSync(path.join(stableRoot, unselectedPath), "utf8"), "stable base\n");
  assert.equal(readFileSync(path.join(sandboxRoot, selectedPath), "utf8"), "sandbox selected bytes\n");
  assert.equal(readFileSync(path.join(sandboxRoot, unselectedPath), "utf8"), "sandbox unselected bytes\n");
  assert.ok(confirmations > 0);
  t.diagnostic(JSON.stringify({ metrics: { prepareMs: Number(prepareMs.toFixed(2)), workerExecuteTotalMs: Number(workerExecuteTotalMs.toFixed(2)), authorizationConfirmations: confirmations, authorizationIntervalMs: authorizationIntervalsMs.map(value => Number(value.toFixed(2))) } }));
});

test("worker requests a fresh main-thread authorization at each launcher write boundary", async () => {
  const run = startWorker(`export function executeManagedSelectedMerge(input) { if (input.plan.planDigest !== "confirmed-plan" || input.checkpoint.checkpointDigest !== "confirmed-checkpoint") throw Error("binding"); if (!input.assertTrusted() || !input.assertTrusted()) throw Error("denied"); return { status: "verified" }; }`, () => true);
  assert.deepEqual(await run.result, { type: "finished", receipt: { status: "verified" } });
  assert.equal(run.rounds(), 2);
});

test("worker fails closed when main-thread authorization is revoked", async () => {
  const run = startWorker(`export function executeManagedSelectedMerge(input) { if (!input.assertTrusted()) throw Error("denied"); if (!input.assertTrusted()) throw Error("revoked"); return { status: "verified" }; }`, round => round === 1);
  assert.equal((await run.result).type, "failed");
  assert.equal(run.rounds(), 2);
});

test("worker uses the exact host-confirmed plan and checkpoint bindings", async () => {
  const run = startWorker(`export function executeManagedSelectedMerge(input) { if (input.plan.planDigest !== input.checkpoint.checkpointDigest) throw Error("binding mismatch"); return { status: "verified" }; }`, () => true);
  assert.equal((await run.result).type, "failed");
  assert.equal(run.rounds(), 0);
});

test("main resolves the worker entry from the repository root and that source file exists", () => {
  const source = readFileSync(path.join(desktopRoot, "electron", "main.ts"), "utf8");
  const match = source.match(/new Worker\(path\.join\(AGENT_ROOT,((?:\s*"[^"]+"\s*,?)+)\)\s*,/);
  assert.ok(match, "worker path must be an explicit AGENT_ROOT-relative join");
  const segments = [...match[1].matchAll(/"([^"]+)"/g)].map(item => item[1]);
  const agentRoot = repositoryRoot;
  const resolvedWorker = path.join(agentRoot, ...segments);
  assert.equal(resolvedWorker, path.join(desktopRoot, "electron", "managed-selected-merge-worker.mjs"));
  assert.equal(existsSync(resolvedWorker), true, "compiled main must resolve to the real sandbox worker source");
  assert.equal(existsSync(path.join(agentRoot, "desktop", "launcher", "managed-selected-merge.mjs")), true);
});

test("authorization wait accepts a prior grant and fails closed after a bounded timeout", async () => {
  const moduleUrl = new URL("../electron/managed-selected-merge-authorization.mjs", import.meta.url).href;
  const code = 'import { parentPort, workerData } from "node:worker_threads"; const { waitForMainAuthorization } = await import(' + JSON.stringify(moduleUrl) + '); const cell = new Int32Array(workerData.signal); if (workerData.prime) Atomics.store(cell,0,1); const result = waitForMainAuthorization(workerData.signal, workerData.timeout); parentPort.postMessage(result);';
  const execute = (prime: boolean, timeout: number) => new Promise<boolean>((resolve, reject) => {
    const signal = new SharedArrayBuffer(Int32Array.BYTES_PER_ELEMENT);
    const worker = new Worker(code, { eval: true, workerData: { signal, prime, timeout } });
    worker.once("message", resolve); worker.once("error", reject);
  });
  assert.equal(await execute(true, 100), true, "a grant stored before Atomics.wait must not be mistaken for denial");
  const start = Date.now();
  assert.equal(await execute(false, 80), false, "timeout without a grant is denied");
  assert.ok(Date.now() - start < 3000, "wait must remain bounded");
});

test("worker entry uses bounded authorization and main handles messageerror as uncertain failure", () => {
  const workerSource = readFileSync(path.join(desktopRoot, "electron", "managed-selected-merge-worker.mjs"), "utf8");
  const mainSource = readFileSync(path.join(desktopRoot, "electron", "main.ts"), "utf8");
  assert.match(workerSource, /waitForMainAuthorization/);
  assert.match(mainSource, /worker\.on\("messageerror"/);
});
