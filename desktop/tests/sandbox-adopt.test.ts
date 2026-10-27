import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync, existsSync, lstatSync, realpathSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";
import * as adoption from "../electron/sandbox-adopt.ts";

const fixtures = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../artifacts/dual-version-adoption-20261023/git-fixtures");
mkdirSync(fixtures, { recursive: true });
const generatedRoots: string[] = [];
after(() => {
  for (const root of generatedRoots) {
    const target = path.resolve(root);
    const relative = path.relative(realpathSync(fixtures), realpathSync(target));
    assert.ok(relative && !relative.startsWith("..") && !path.isAbsolute(relative), "fixture cleanup must stay inside the dedicated fixture root");
    assert.equal(lstatSync(target).isSymbolicLink(), false);
    rmSync(target, { recursive: true });
  }
});
function git(root: string, ...args: string[]): string {
  return execFileSync("git", args, { cwd: root, windowsHide: true, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }).trim();
}
function put(root: string, file: string, content: string): void {
  mkdirSync(path.dirname(path.join(root, file)), { recursive: true });
  writeFileSync(path.join(root, file), content);
}
function fixture() {
  assert.equal(typeof adoption.createSandboxAdopter, "function", "missing safe adoption service");
  const root = mkdtempSync(path.join(fixtures, "run-"));
  generatedRoots.push(root);
  git(root, "init", "-b", "stable");
  git(root, "config", "user.name", "Adoption Fixture");
  git(root, "config", "user.email", "fixture@example.invalid");
  git(root, "config", "commit.gpgsign", "false");
  git(root, "config", "core.autocrlf", "false");
  git(root, "config", "core.hooksPath", path.join(root, "no-hooks"));
  for (const [file, value] of Object.entries({
    ".gitignore": "data/\nartifacts/\n.env*\n",
    "desktop/package.json": "{}\n", "desktop/main.txt": "base\n",
    "agent-core/server.py": "base\n", "agent-core/other.py": "base\n",
    "docs/base.md": "base\n", "protocol/base.json": "{}\n", "notes.txt": "private work\n",
  })) put(root, file, value);
  git(root, "add", "."); git(root, "commit", "-m", "base");
  const sandbox = path.join(root, ".worktrees", "sandbox");
  mkdirSync(path.dirname(sandbox), { recursive: true });
  // The real product ignores its linked worktrees as well.
  writeFileSync(path.join(root, ".git", "info", "exclude"), ".worktrees/\nno-hooks/\n");
  git(root, "worktree", "add", "-b", "experiment/sandbox", sandbox);
  const service = adoption.createSandboxAdopter({ stableRoot: root, sandboxRoot: sandbox });
  return { root, sandbox, service };
}
async function adopt(f: ReturnType<typeof fixture>) {
  const status = await f.service.getStatus();
  return f.service.prepareAndMerge({ expectedSnapshotId: status.snapshotId, autoCommitSandbox: true, autoStashStable: true });
}

test("confirmed adoption commits protocol/docs too, preserves the stable index, and synchronizes both branches", async () => {
  const f = fixture();
  put(f.sandbox, "desktop/main.txt", "experiment\n");
  put(f.sandbox, "protocol/new.json", "{\"new\":true}\n");
  put(f.sandbox, "docs/验收 notes.md", "included\n");
  put(f.root, "agent-core/server.py", "staged\n"); git(f.root, "add", "agent-core/server.py");
  put(f.root, "agent-core/server.py", "unstaged after staged\n");
  put(f.root, "notes.txt", "unrelated private edit\n");
  const beforeIndex = git(f.root, "show", ":agent-core/server.py");
  const result = await adopt(f);
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(result.merged, true); assert.equal(result.synced, true);
  assert.equal(readFileSync(path.join(f.root, "protocol/new.json"), "utf8"), "{\"new\":true}\n");
  assert.equal(readFileSync(path.join(f.root, "docs/验收 notes.md"), "utf8"), "included\n");
  assert.equal(readFileSync(path.join(f.root, "agent-core/server.py"), "utf8"), "unstaged after staged\n");
  assert.equal(git(f.root, "show", ":agent-core/server.py"), beforeIndex);
  assert.equal(readFileSync(path.join(f.root, "notes.txt"), "utf8"), "unrelated private edit\n");
  assert.equal(git(f.root, "show", result.recoveryStash + ":notes.txt"), "private work", "source backup must exclude unrelated working content");
  assert.equal(git(f.root, "rev-parse", "HEAD"), git(f.sandbox, "rev-parse", "HEAD"));
  assert.equal(git(f.sandbox, "status", "--porcelain"), "");
});

test("no consent or a declined native confirmation never commits, stashes or merges", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate\n");
  put(f.root, "agent-core/server.py", "local\n");
  const before = git(f.sandbox, "rev-parse", "HEAD");
  const result = await f.service.prepareAndMerge({ autoCommitSandbox: true });
  assert.equal(result.ok, false);
  assert.equal(git(f.sandbox, "rev-parse", "HEAD"), before);
  assert.equal(git(f.root, "stash", "list"), "");
  const request = adoption.createSandboxAdoptRequestHandler(f.service, async () => false);
  const declined = await request(); assert.equal(declined.ok, false);
  assert.equal(git(f.sandbox, "rev-parse", "HEAD"), before);
});

test("content changed while confirmation is open invalidates approval before any write", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate one\n");
  const before = git(f.sandbox, "rev-parse", "HEAD");
  const request = adoption.createSandboxAdoptRequestHandler(f.service, async () => {
    put(f.sandbox, "desktop/main.txt", "candidate two\n"); return true;
  });
  const result = await request();
  assert.equal(result.ok, false); assert.match(result.message, /变化|重新确认/);
  assert.equal(git(f.sandbox, "rev-parse", "HEAD"), before);
});

test("native confirmation receives exact scope and the handler drives the real Git workflow", async () => {
  const f = fixture(); put(f.sandbox, "protocol/extra.json", "{}\n");
  const request = adoption.createSandboxAdoptRequestHandler(f.service, async (status) => {
    assert.ok(status.sandboxDirtyProduct.some((p: string) => p.includes("protocol/extra.json")));
    assert.equal(status.currentBranch, "stable"); return true;
  });
  const result = await request(); assert.equal(result.ok, true, JSON.stringify(result));
  assert.ok(existsSync(path.join(f.root, "protocol/extra.json")));
});

test("a conflict aborts only this merge and restores this operation's stash with its index", async () => {
  const f = fixture();
  put(f.sandbox, "desktop/main.txt", "candidate conflict\n");
  git(f.sandbox, "add", "desktop/main.txt"); git(f.sandbox, "commit", "-m", "candidate");
  put(f.root, "desktop/main.txt", "stable conflict\n");
  git(f.root, "add", "desktop/main.txt"); git(f.root, "commit", "-m", "stable");
  put(f.root, "agent-core/server.py", "staged local\n"); git(f.root, "add", "agent-core/server.py");
  const head = git(f.root, "rev-parse", "HEAD"); const index = git(f.root, "diff", "--cached");
  const result = await adopt(f);
  assert.equal(result.ok, false); assert.equal(result.merged, false);
  assert.equal(git(f.root, "rev-parse", "HEAD"), head);
  assert.equal(git(f.root, "diff", "--cached"), index);
  assert.equal(readFileSync(path.join(f.root, "desktop/main.txt"), "utf8"), "stable conflict\n");
  assert.equal(existsSync(path.join(f.root, ".git", "MERGE_HEAD")), false);
  assert.match(result.recoveryStash ?? "", /^[0-9a-f]{40,64}$/);
});

test("restoring stable work never pops a pre-existing stash", async () => {
  const f = fixture(); put(f.root, "agent-core/other.py", "older stash\n");
  git(f.root, "stash", "push", "-m", "belongs to another task");
  const oldStash = git(f.root, "rev-parse", "refs/stash");
  put(f.root, "agent-core/server.py", "current local\n");
  put(f.sandbox, "desktop/main.txt", "candidate\n");
  const result = await adopt(f); assert.equal(result.ok, true, JSON.stringify(result));
  const saved = git(f.root, "stash", "list", "--format=%H").split("\n");
  assert.ok(saved.includes(oldStash));
  assert.ok(saved.includes(result.recoveryStash ?? "missing"));
  assert.equal(saved.length, 2, "keep the exact recovery backup until user acceptance");
});

test("a known stable-worktree restore conflict is rejected before the stable branch moves", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate\n");
  put(f.root, "desktop/main.txt", "uncommitted stable\n");
  const originalHead = git(f.root, "rev-parse", "HEAD");
  const result = await adopt(f);
  assert.equal(result.ok, false); assert.equal(result.merged, false);
  assert.equal(git(f.root, "rev-parse", "HEAD"), originalHead);
  assert.equal(readFileSync(path.join(f.root, "desktop/main.txt"), "utf8"), "uncommitted stable\n");
  assert.match(result.recoveryStash ?? "", /^[0-9a-f]{40,64}$/);
  assert.equal(git(f.root, "show", result.recoveryStash + ":desktop/main.txt"), "uncommitted stable");
});

test("existing operation state or unrelated staged sandbox work fails closed", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate\n");
  put(f.sandbox, "notes.txt", "other task\n"); git(f.sandbox, "add", "notes.txt");
  const result = await adopt(f); assert.equal(result.ok, false);
  assert.equal(git(f.sandbox, "show", ":notes.txt"), "other task");
  assert.equal(git(f.root, "stash", "list"), "");
});

test("a pre-existing merge is not aborted or committed by the adoption button", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate\n");
  git(f.sandbox, "add", "desktop/main.txt"); git(f.sandbox, "commit", "-m", "candidate");
  put(f.root, "desktop/main.txt", "stable\n"); git(f.root, "add", "desktop/main.txt"); git(f.root, "commit", "-m", "stable");
  try { git(f.root, "merge", "experiment/sandbox"); } catch { /* deliberately leave a pre-existing conflict */ }
  const head = readFileSync(path.join(f.root, ".git", "MERGE_HEAD"), "utf8");
  const result = await adopt(f); assert.equal(result.ok, false);
  assert.equal(readFileSync(path.join(f.root, ".git", "MERGE_HEAD"), "utf8"), head);
});

test("same-named branch in another repository and calling from the sandbox itself are rejected", async () => {
  const f = fixture(); const foreign = fixture();
  const service = adoption.createSandboxAdopter({ stableRoot: f.root, sandboxRoot: foreign.sandbox });
  assert.ok((await service.getStatus()).fatalBlockers.length > 0);
  const self = adoption.createSandboxAdopter({ stableRoot: f.sandbox, sandboxRoot: f.sandbox });
  assert.ok((await self.getStatus()).fatalBlockers.length > 0);
});

test("already committed secrets outside the release allowlist cannot ride a whole-branch merge", async () => {
  const f = fixture(); put(f.sandbox, ".env.local", "fake_fixture_secret=not_real\n");
  git(f.sandbox, "add", "-f", ".env.local"); git(f.sandbox, "commit", "-m", "unsafe file");
  const result = await adopt(f); assert.equal(result.ok, false);
  assert.equal(existsSync(path.join(f.root, ".env.local")), false);
});

test("parallel requests are serialized and an existing lock is never stolen", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate\n");
  const status = await f.service.getStatus();
  const options = { expectedSnapshotId: status.snapshotId, autoCommitSandbox: true, autoStashStable: true };
  const results = await Promise.all([f.service.prepareAndMerge(options), f.service.prepareAndMerge(options)]);
  assert.equal(results.filter((r) => r.ok).length, 1, JSON.stringify(results));
  assert.equal(results.filter((r) => /进行中|锁/.test(r.message)).length, 1);
});


test("IPC sender policy rejects child frames, unrelated windows, remote origins and missing frames", () => {
  const valid = { senderId: 7, windowId: 7, isMainFrame: true, origin: "http://127.0.0.1:5173", expectedOrigin: "http://127.0.0.1:5173" };
  assert.equal(adoption.isTrustedSandboxAdoptSender(valid), true);
  for (const changed of [{ isMainFrame: false }, { senderId: 8 }, { origin: "https://evil.invalid" }, { origin: "" }, { expectedOrigin: "" }]) {
    assert.equal(adoption.isTrustedSandboxAdoptSender({ ...valid, ...changed }), false);
  }
});


test("an existing on-disk adoption lock is retained, never stolen or removed", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate\n");
  const lock = path.join(f.root, ".git/my-agent-sandbox-adopt.lock");
  writeFileSync(lock, "another operation");
  const result = await adopt(f);
  assert.equal(result.ok, false); assert.match(result.message, /锁/);
  assert.equal(readFileSync(lock, "utf8"), "another operation");
});

test("stable preview preference reset preserves other state and refuses malformed state", () => {
  const root = mkdtempSync(path.join(fixtures, "state-"));
  generatedRoots.push(root);
  const file = path.join(root, "state.json");
  writeFileSync(file, JSON.stringify({ desktop_shell_renderer_url: "http://127.0.0.1:5174/", selected_session: "keep-me", nested: { keep: true } }));
  adoption.clearSandboxPreviewPreference(file);
  assert.deepEqual(JSON.parse(readFileSync(file, "utf8")), { selected_session: "keep-me", nested: { keep: true } });
  writeFileSync(file, "{broken");
  assert.throws(() => adoption.clearSandboxPreviewPreference(file));
  assert.equal(readFileSync(file, "utf8"), "{broken");
  for (const bad of ["[]", "null", "42"]) {
    writeFileSync(file, bad); assert.throws(() => adoption.clearSandboxPreviewPreference(file));
    assert.equal(readFileSync(file, "utf8"), bad);
  }
});


test("the same product controller can complete a second experiment after synchronization", async () => {
  const f = fixture();
  const request = adoption.createSandboxAdoptRequestHandler(f.service, async () => true);
  put(f.sandbox, "desktop/main.txt", "first generation\n");
  assert.equal((await request()).ok, true);
  put(f.sandbox, "protocol/second.json", "{\"generation\":2}\n");
  const second = await request(); assert.equal(second.ok, true, JSON.stringify(second));
  assert.equal(git(f.root, "rev-parse", "HEAD"), git(f.sandbox, "rev-parse", "HEAD"));
  assert.equal(readFileSync(path.join(f.root, "desktop/main.txt"), "utf8"), "first generation\n");
  assert.ok(existsSync(path.join(f.root, "protocol/second.json")));
});

test("a committed source symlink cannot be promoted as an in-root regular release file", async () => {
  const f = fixture();
  const blob = execFileSync("git", ["hash-object", "-w", "--stdin"], { cwd: f.sandbox, input: "../../outside", encoding: "utf8", windowsHide: true }).trim();
  git(f.sandbox, "update-index", "--add", "--cacheinfo", "120000," + blob + ",desktop/linked-source");
  git(f.sandbox, "commit", "-m", "symlink fixture");
  const result = await adopt(f); assert.equal(result.ok, false);
  assert.equal(existsSync(path.join(f.root, "desktop/linked-source")), false);
});

test("Git index environment overrides cannot redirect an adoption outside the selected worktree", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate\n");
  const previous = process.env.GIT_INDEX_FILE;
  process.env.GIT_INDEX_FILE = path.join(f.root, "external-index");
  try {
    const status = await f.service.getStatus(); assert.deepEqual(status.fatalBlockers, []);
    assert.equal(existsSync(process.env.GIT_INDEX_FILE), false);
  } finally {
    if (previous === undefined) delete process.env.GIT_INDEX_FILE; else process.env.GIT_INDEX_FILE = previous;
  }
});


test("preflight permits compatible changes to different regions of the same file", async () => {
  const f = fixture();
  const base = Array.from({ length: 20 }, (_, i) => "line " + i).join("\n") + "\n";
  put(f.root, "desktop/main.txt", base); git(f.root, "add", "desktop/main.txt"); git(f.root, "commit", "-m", "long base");
  git(f.sandbox, "merge", "--ff-only", "stable");
  put(f.sandbox, "desktop/main.txt", base.replace("line 0\n", "candidate first\n"));
  put(f.root, "desktop/main.txt", base.replace("line 19\n", "local last\n")); git(f.root, "add", "desktop/main.txt");
  const result = await adopt(f); assert.equal(result.ok, true, JSON.stringify(result));
  const text = readFileSync(path.join(f.root, "desktop/main.txt"), "utf8");
  assert.match(text, /^candidate first\n/); assert.match(text, /local last\n$/);
});

test("preflight rejects an untracked-file restore collision without moving stable", async () => {
  const f = fixture(); put(f.sandbox, "docs/local.md", "candidate new file\n");
  put(f.root, "docs/local.md", "local untracked file\n");
  const head = git(f.root, "rev-parse", "HEAD");
  const result = await adopt(f); assert.equal(result.ok, false); assert.equal(result.merged, false);
  assert.equal(git(f.root, "rev-parse", "HEAD"), head);
  assert.equal(readFileSync(path.join(f.root, "docs/local.md"), "utf8"), "local untracked file\n");
});

test("preflight detects index-only conflicts even when stable worktree bytes match HEAD", async () => {
  const f = fixture(); put(f.sandbox, "desktop/main.txt", "candidate\n");
  put(f.root, "desktop/main.txt", "staged local\n"); git(f.root, "add", "desktop/main.txt");
  put(f.root, "desktop/main.txt", "base\n");
  const head = git(f.root, "rev-parse", "HEAD");
  const result = await adopt(f); assert.equal(result.ok, false); assert.equal(result.merged, false);
  assert.equal(git(f.root, "rev-parse", "HEAD"), head);
  assert.equal(git(f.root, "show", ":desktop/main.txt"), "staged local", JSON.stringify(result));
  assert.equal(readFileSync(path.join(f.root, "desktop/main.txt"), "utf8"), "base\n");
});


test("preflight stops Windows case-alias untracked collisions before stable moves", { skip: process.platform !== "win32" }, async () => {
  const f = fixture(); put(f.sandbox, "docs/case.txt", "candidate lower case\n");
  put(f.root, "docs/CASE.txt", "local upper case\n");
  const head = git(f.root, "rev-parse", "HEAD");
  const result = await adopt(f); assert.equal(result.ok, false);
  assert.equal(result.merged, false, JSON.stringify(result));
  assert.equal(git(f.root, "rev-parse", "HEAD"), head);
  assert.equal(readFileSync(path.join(f.root, "docs/CASE.txt"), "utf8"), "local upper case\n");
});


test("live adoption is disabled only for a rehearsal Host", () => {
  assert.equal(adoption.sandboxAdoptionHostBlocker({ mutableDevelopmentHost: true, testHost: false }), null);
  assert.match(adoption.sandboxAdoptionHostBlocker({ mutableDevelopmentHost: false, testHost: true }) ?? "", /测试/);
  assert.equal(adoption.sandboxAdoptionHostBlocker({ mutableDevelopmentHost: false, testHost: false }), null);
  assert.equal(adoption.sandboxAdoptionHostBlocker({ mutableDevelopmentHost: false, testHost: false, fixedHostVerified:true }), null);
});
