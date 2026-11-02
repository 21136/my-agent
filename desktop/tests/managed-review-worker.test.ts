import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { copyFile, lstat, mkdir, mkdtemp, realpath, rm, rmdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { Worker } from "node:worker_threads";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const workerPath = new URL("../electron/managed-review-worker.mjs", import.meta.url);
const launcherSource = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../launcher");
function git(root, ...args) {
  execFileSync("git", ["-C", root, ...args], { stdio: "ignore", windowsHide: true });
}
async function fixture() {
  const parent = await mkdtemp(path.join(os.tmpdir(), "managed-review-worker-fixtures-"));
  const root = path.join(parent, "worktree");
  await mkdir(root);
  const launcher = path.join(root, "desktop", "launcher");
  await mkdir(launcher, { recursive: true });
  for (const name of ["managed-source-plan.mjs"]) {
    await copyFile(path.join(launcherSource, name), path.join(launcher, name));
  }
  await writeFile(path.join(launcher, "managed-review.mjs"), `export async function inspectManagedReview(roots) { await new Promise(resolve => setTimeout(resolve, 250)); return { mode: "managed_source_review_v1", fixture: true, stableRoot: roots.stableRoot, sandboxRoot: roots.sandboxRoot }; }`);
  git(root, "init", "-b", "main");
  git(root, "config", "user.name", "Managed Review Fixture");
  git(root, "config", "user.email", "fixture@example.invalid");
  await mkdir(path.join(root, "agent-core"), { recursive: true });
  for (let i = 0; i < 80; i++) await writeFile(path.join(root, "agent-core", `file-${i}.py`), `value = ${i}\n`);
  git(root, "add", "agent-core"); git(root, "commit", "-m", "fixture baseline");
  await mkdir(path.join(root, ".worktrees"), { recursive: true });
  const sandbox = path.join(root, ".worktrees", "sandbox");
  git(root, "worktree", "add", "-b", "experiment/sandbox", sandbox);
  for (let i = 0; i < 20; i++) await writeFile(path.join(sandbox, "agent-core", `file-${i}.py`), `value = ${i + 100}\n`);
  git(sandbox, "add", "agent-core"); git(sandbox, "commit", "-m", "fixture sandbox update");
  return { parent, root };
}
async function removeFixture({ parent, root }) {
  const tempRoot = path.resolve(os.tmpdir());
  const parentPath = path.resolve(parent), targetPath = path.resolve(root);
  assert.equal(path.dirname(parentPath), tempRoot, "fixture parent must be an immediate child of the OS temp directory");
  assert.match(path.basename(parentPath), /^managed-review-worker-fixtures-/);
  assert.equal(path.relative(parentPath, targetPath), "worktree", "recursive target must be the exact fixture child");
  for (const candidate of [parentPath, targetPath]) {
    const info = await lstat(candidate);
    assert.ok(info.isDirectory() && !info.isSymbolicLink() && (info.attributes & 0x400) === 0, "fixture path must not be a symlink or reparse point");
    assert.equal(await realpath(candidate), candidate, "fixture path must resolve to itself");
  }
  await rm(targetPath, { recursive: true, force: false });
  await rmdir(parentPath);
}
function start(codeRoot) {
  return new Worker(workerPath, {
    workerData: { mode: "managed_source_review_worker_v1", codeRoot },
    env: { PATH: process.env.PATH ?? process.env.Path ?? "", SYSTEMROOT: process.env.SYSTEMROOT ?? process.env.SystemRoot ?? "", WINDIR: process.env.WINDIR ?? process.env.Windir ?? "" },
  });
}

 test("real launcher Git fixture runs identity and async review off-thread with the exact worktree roots", async () => {
  const fixtureTree = await fixture();
  const { root } = fixtureTree;
  const { inspectManagedSources } = await import(pathToFileURL(path.join(root, "desktop", "launcher", "managed-source-plan.mjs")).href);
  assert.equal(inspectManagedSources({ stableRoot: root, sandboxRoot: path.join(root, ".worktrees", "sandbox"), metadataOnlyHistory: true }).mode, "managed_source_inspection_v1");
  const worker = start(root);
  let ticks = 0, authorizations = 0;
  const timer = setInterval(() => ticks++, 10);
  try {
    const result = await new Promise((resolve, reject) => {
      worker.on("message", (message) => {
        if (message.type === "authorization_required") {
          authorizations++;
          const signal = new Int32Array(message.signal); Atomics.store(signal, 0, 1); Atomics.notify(signal, 0);
        } else if (message.type === "finished") resolve(message);
        else reject(new Error(`worker failed at ${message.phase ?? "unknown"}`));
      });
      worker.once("error", reject);
    });
    assert.equal(authorizations, 2, "authorization is checked before identity scan and before review scan");
    assert.ok(ticks > 0, `main event loop ran ${ticks} timer ticks during real Git scans`);
    assert.equal(result.report.mode, "managed_source_review_v1");
    assert.equal(result.report.fixture, true, "worker awaited the asynchronous fixture reviewer before structured cloning its result");
    assert.equal(result.roots.stableRoot, root);
    assert.equal(result.roots.sandboxRoot, path.join(root, ".worktrees", "sandbox"));
  } finally {
    clearInterval(timer); await worker.terminate(); await removeFixture(fixtureTree);
  }
});

test("denied trust recheck aborts before report generation and worker errors are sanitized", async () => {
  const fixtureTree = await fixture();
  const { root } = fixtureTree;
  const worker = start(root);
  let authorizations = 0;
  try {
    const failure = await new Promise((resolve, reject) => {
      worker.on("message", (message) => {
        if (message.type === "authorization_required") {
          authorizations++;
          const signal = new Int32Array(message.signal); Atomics.store(signal, 0, authorizations === 1 ? 1 : 2); Atomics.notify(signal, 0);
        } else resolve(message);
      });
      worker.once("error", reject);
    });
    assert.equal(failure.type, "failed");
    assert.equal(failure.phase, "identity_git_scan", JSON.stringify(failure));
    assert.equal(authorizations, 2);
  } finally {
    await worker.terminate(); await removeFixture(fixtureTree);
  }
});






