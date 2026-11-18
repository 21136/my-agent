import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { buildSelfEvolutionNativeApplyEnvironment, createSelfEvolutionNativeApplyTransport, validateSelfEvolutionDisposableTarget, validateSelfEvolutionExperimentalOptIn, resolveSelfEvolutionGitCommonRoot } from "../electron/self-evolution-native-apply.ts";

const canonical = (value: any): string => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
  : value && typeof value === "object" ? `{${Object.keys(value).sort().map((key) => `${canonical(key)}:${canonical(value[key])}`).join(",")}}`
  : typeof value === "string" ? JSON.stringify(value).replace(/[\u007f-\uffff]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`)
  : JSON.stringify(value);
function request(targetRoot = "C:\\experiment\\project") {
  const body: any = {
    schema: "self_evolution_host_selected_apply_request_v1",
    session_id: "session_1", task_id: "task_1", attempt_id: "attempt_1", candidate_id: "candidate_1",
    baseline_digest: "a".repeat(64), candidate_digest: "b".repeat(64), operation_id: "op_1",
    target_root: targetRoot, target_head: "c".repeat(40), target_index_sha256: "d".repeat(64),
    target_worktree_digest: "e".repeat(64),
    files: [{ path: "desktop/electron/main.ts", action: "modify", before_sha256: "f".repeat(64), after_sha256: "0".repeat(64), before_size: 10, after_size: 11, before_mode: "100644", after_mode: "100644" }],
  };
  body.request_sha256 = createHash("sha256").update(canonical(body), "ascii").digest("hex");
  return body;
}
async function post(url: string, token: string, body: unknown, contentType = "application/json") {
  return fetch(url, { method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": contentType }, body: typeof body === "string" ? body : canonical(body) });
}
async function setup(overrides: Record<string, unknown> = {}) {
  const calls: unknown[] = [];
  const tempRoot = mkdtempSync(path.join(os.tmpdir(), "self-evolution-native-"));
  const sourceRoot = path.join(tempRoot, "source");
  const targetRoot = path.join(tempRoot, "disposable-target");
  mkdirSync(sourceRoot);
  mkdirSync(path.join(targetRoot, ".git", "objects"), { recursive: true });
  mkdirSync(path.join(targetRoot, ".git", "refs"), { recursive: true });
  writeFileSync(path.join(targetRoot, ".git", "HEAD"), "ref: refs/heads/main\n");
  writeFileSync(path.join(targetRoot, ".git", "config"), "[core]\n\trepositoryformatversion = 0\n");
  writeFileSync(path.join(targetRoot, ".self-evolution-experimental-target.json"), JSON.stringify({ schema: "self-evolution-experimental-target.v1", purpose: "self_evolution_disposable_repository", repository_root: targetRoot }));
  validateSelfEvolutionDisposableTarget({ sourceRoot, targetRoot });
  const token = randomBytes(32).toString("hex");
  const child = { pid: 81 };
  const window = { id: 15 };
  const transport = await createSelfEvolutionNativeApplyTransport({
    token,
    childIdentity: child,
    expectedTargetRoot: targetRoot,
    isChildCurrent: (value: unknown) => value === child,
    getOfficialWindow: () => window,
    isOfficialWindowCurrent: (value: unknown) => value === window,
    showNativeConfirmation: async (_window: unknown, value: unknown) => { calls.push(value); return true; },
    ...overrides,
  } as any);
  return { calls, token, child, window, transport, tempRoot, sourceRoot, targetRoot };
}

test("loopback transport displays exact validated Host request and returns exact one-use claim", async (t) => {
  const state = await setup(); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  const body = request(state.targetRoot);
  const result = await post(state.transport.url, state.token, body);
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), { approved: true, request_sha256: body.request_sha256, operation_id: body.operation_id, selected_paths: ["desktop/electron/main.ts"] });
  assert.deepEqual(state.calls, [body]);
  const replay = await post(state.transport.url, state.token, body);
  assert.equal(replay.status, 200);
  assert.deepEqual(await replay.json(), { approved: false, request_sha256: body.request_sha256, operation_id: body.operation_id, selected_paths: ["desktop/electron/main.ts"] });
  assert.equal(state.calls.length, 1);
});

test("transport rejects unauthenticated, wrong media type, malformed digest, and non-experiment target without prompting", async (t) => {
  const state = await setup(); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  const valid = request(state.targetRoot);
  assert.equal((await post(state.transport.url, "0".repeat(64), valid)).status, 401);
  assert.equal((await post(state.transport.url, state.token, valid, "text/plain")).status, 415);
  assert.equal((await post(state.transport.url, state.token, { ...valid, candidate_digest: "z".repeat(64) })).status, 400);
  const wrongTarget = { ...valid, target_root: path.join(path.dirname(state.targetRoot), "stable-target") };
  delete wrongTarget.request_sha256;
  wrongTarget.request_sha256 = createHash("sha256").update(canonical(wrongTarget), "ascii").digest("hex");
  assert.equal((await post(state.transport.url, state.token, wrongTarget)).status, 200);
  assert.equal(state.calls.length, 0);
});

test("approval is declined if official window or sidecar identity changes while dialog is open", async (t) => {
  let childAlive = true; let windowAlive = true;
  const state = await setup({
    isChildCurrent: () => childAlive,
    isOfficialWindowCurrent: () => windowAlive,
    showNativeConfirmation: async () => { childAlive = false; return true; },
  }); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  const response = await post(state.transport.url, state.token, request(state.targetRoot));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).approved, false);
  assert.equal(state.calls.length, 0);
  const again = await setup({ isOfficialWindowCurrent: () => windowAlive, showNativeConfirmation: async () => { windowAlive = false; return true; } });
  t.after(async () => { await again.transport.close(); rmSync(again.tempRoot, { recursive: true, force: true }); });
  const response2 = await post(again.transport.url, again.token, request(again.targetRoot));
  assert.equal(response2.status, 200);
  assert.equal((await response2.json()).approved, false);
});

test("transport binds to loopback ephemeral port, bounds body, times out, and closes", async (t) => {
  const state = await setup({ maxBodyBytes: 128 }); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  assert.match(state.transport.url, /^http:\/\/127\.0\.0\.1:\d+\/self-evolution\/confirm$/);
  assert.equal((await post(state.transport.url, state.token, request(state.targetRoot))).status, 413);
  const address = state.transport.address;
  assert.equal(address.address, "127.0.0.1");
  await state.transport.close();
  await assert.rejects(fetch(state.transport.url));
});









test("approval times out closed and cannot be accepted by a late dialog result", async (t) => {
  let resolveDialog: ((value: boolean) => void) | undefined;
  let clock = Date.now();
  const state = await setup({
    operationTtlMs: 1000,
    now: () => clock,
    showNativeConfirmation: () => new Promise<boolean>((resolve) => { resolveDialog = resolve; }),
  }); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  const result = await post(state.transport.url, state.token, request(state.targetRoot));
  assert.equal(result.status, 200);
  assert.equal((await result.json()).approved, false);
  resolveDialog?.(true);
  clock += 2000;
  const replay = await post(state.transport.url, state.token, request(state.targetRoot));
  assert.equal(replay.status, 200);
  assert.equal((await replay.json()).approved, false);
  assert.equal(state.calls.length, 0);
});

test("child environment strips inherited credentials and injects only the explicit loopback pair", () => {
  const result = buildSelfEvolutionNativeApplyEnvironment({
    PATH: "safe", MY_AGENT_SELF_EVOLUTION_NATIVE_URL: "http://evil", MY_AGENT_SELF_EVOLUTION_NATIVE_TOKEN: "secret",
    MY_AGENT_SELF_EVOLUTION_EXPERIMENT_TARGET_ROOT: "C:/stable", MY_AGENT_EXPERIMENTAL_SELF_EVOLUTION_STATIC_AST: "1", MY_AGENT_SELF_EVOLUTION_NATIVE_APPROVAL: "1", MY_AGENT_SELF_EVOLUTION_ENROLL_TRUST_ROOT: "1",
  }, { url: "http://127.0.0.1:1234/self-evolution/confirm", token: "a".repeat(64) });
  assert.deepEqual(result, {
    PATH: "safe", MY_AGENT_SELF_EVOLUTION_NATIVE_URL: "http://127.0.0.1:1234/self-evolution/confirm",
    MY_AGENT_SELF_EVOLUTION_NATIVE_TOKEN: "a".repeat(64),
  });
});


test("digest matches Python ensure_ascii canonicalization for non-ASCII selected paths", async (t) => {
  const state = await setup(); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  const body = request(state.targetRoot);
  body.files[0].path = "desktop/说明.ts";
  delete body.request_sha256;
  body.request_sha256 = createHash("sha256").update(canonical(body), "ascii").digest("hex");
  const response = await post(state.transport.url, state.token, body);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { approved: true, request_sha256: body.request_sha256, operation_id: body.operation_id, selected_paths: ["desktop/说明.ts"] });
});


test("disposable-target validator rejects source/runtime overlap, non-sibling repositories, and forged markers", async (t) => {
  const state = await setup(); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  assert.throws(() => validateSelfEvolutionDisposableTarget({ sourceRoot: state.sourceRoot, targetRoot: state.targetRoot, excludedRoots: [path.join(state.targetRoot, "runtime")] }));
  assert.throws(() => validateSelfEvolutionDisposableTarget({ sourceRoot: state.sourceRoot, targetRoot: state.targetRoot, excludedRoots: [state.tempRoot] }));
  assert.throws(() => validateSelfEvolutionDisposableTarget({ sourceRoot: state.sourceRoot, targetRoot: path.join(state.sourceRoot, "nested") }));
  const marker = path.join(state.targetRoot, ".self-evolution-experimental-target.json");
  writeFileSync(marker, JSON.stringify({ schema: "self-evolution-experimental-target.v1", purpose: "self_evolution_disposable_repository", repository_root: state.sourceRoot }));
  assert.throws(() => validateSelfEvolutionDisposableTarget({ sourceRoot: state.sourceRoot, targetRoot: state.targetRoot }));
});

test("selections beyond the explicit 256-file bound decline without opening a partial native review", async (t) => {
  const state = await setup(); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  const body = request(state.targetRoot);
  body.files = Array.from({ length: 257 }, (_, index) => ({
    path: `desktop/file-${String(index).padStart(3, "0")}.ts`, action: "modify",
    before_sha256: "f".repeat(64), after_sha256: "0".repeat(64), before_size: 1, after_size: 2, before_mode: "100644", after_mode: "100644",
  }));
  delete body.request_sha256;
  body.request_sha256 = createHash("sha256").update(canonical(body), "ascii").digest("hex");
  const response = await post(state.transport.url, state.token, body);
  assert.equal(response.status, 400);
  assert.deepEqual(Object.keys(await response.json()).sort(), ["approved", "operation_id", "request_sha256", "selected_paths"]);
  assert.equal(state.calls.length, 0);
});


test("bounded scrollable native-review detail contains the exact first and last paths without truncation", async () => {
  const body = request();
  body.files = Array.from({ length: 80 }, (_, index) => ({
    path: `desktop/selected-${String(index).padStart(3, "0")}.ts`, action: "modify",
    before_sha256: "f".repeat(64), after_sha256: "0".repeat(64), before_size: 1, after_size: 2, before_mode: "100644", after_mode: "100644",
  }));
  const { buildSelfEvolutionNativeApplyDetail } = await import("../electron/self-evolution-native-apply.ts");
  const detail = buildSelfEvolutionNativeApplyDetail(body);
  assert.ok(detail);
  assert.match(detail!, /desktop\/selected-000\.ts/);
  assert.match(detail!, /desktop\/selected-079\.ts/);
  assert.equal((detail!.match(/desktop\/selected-/g) ?? []).length, 80);
});


test("experimental startup guard is Windows-only and refuses native approval without explicit profile", () => {
  const base = { platform: "win32", staticAstEnabled: true, nativeApprovalEnabled: false, packaged: false, fixedHost: false, testHost: false } as const;
  assert.equal(validateSelfEvolutionExperimentalOptIn(base), true);
  assert.throws(() => validateSelfEvolutionExperimentalOptIn({ ...base, platform: "linux" }));
  assert.throws(() => validateSelfEvolutionExperimentalOptIn({ ...base, packaged: true }));
  assert.throws(() => validateSelfEvolutionExperimentalOptIn({ ...base, fixedHost: true }));
  assert.throws(() => validateSelfEvolutionExperimentalOptIn({ ...base, nativeApprovalEnabled: true, staticAstEnabled: false }));
  assert.equal(validateSelfEvolutionExperimentalOptIn({ ...base, staticAstEnabled: false }), false);
});




test("transport rejects semantically matching but noncanonical raw JSON without prompting", async (t) => {
  const state = await setup(); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  const body = request(state.targetRoot);
  const raw = JSON.stringify(body);
  const response = await post(state.transport.url, state.token, raw);
  assert.equal(response.status, 400);
  assert.deepEqual(await response.json(), { approved: false, request_sha256: "", operation_id: "", selected_paths: [] });
  assert.equal(state.calls.length, 0);
});


test("shared Git worktree metadata resolves and protects its stable common root", async (t) => {
  const state = await setup(); t.after(async () => { await state.transport.close(); rmSync(state.tempRoot, { recursive: true, force: true }); });
  const stableRoot = path.join(state.tempRoot, "stable-checkout");
  const commonGit = path.join(stableRoot, ".git");
  const worktreeGit = path.join(commonGit, "worktrees", "experiment");
  mkdirSync(worktreeGit, { recursive: true });
  writeFileSync(path.join(state.sourceRoot, ".git"), `gitdir: ${worktreeGit}\n`);
  assert.equal(resolveSelfEvolutionGitCommonRoot(state.sourceRoot), stableRoot);
  assert.equal(validateSelfEvolutionDisposableTarget({ sourceRoot: state.sourceRoot, targetRoot: state.targetRoot, excludedRoots: [stableRoot] }), state.targetRoot);
});



test("preload never exposes native approval URL or token", () => {
  const preload = readFileSync(new URL("../electron/preload.ts", import.meta.url), "utf8");
  assert.doesNotMatch(preload, /MY_AGENT_SELF_EVOLUTION_NATIVE_(?:URL|TOKEN)/);
  assert.doesNotMatch(preload, /self-evolution\/confirm/);
});
