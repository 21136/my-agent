import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { createManagedSelectedMergeHandler } from "../electron/managed-selected-merge-bridge.ts";

const hash = (char: string) => char.repeat(64);
const canonical = (value: any): string => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : value && typeof value === "object" ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const digest = (value: any) => createHash("sha256").update(canonical(value)).digest("hex");
let fixtureNumber = 0;
const newCheckpointDigest = () => digest({ fixture: ++fixtureNumber });
const request = (checkpointDigest: string) => ({ format: 1 as const, mode: "managed_selected_merge_request_v1" as const, checkpointDigest });
const checkpoint = (checkpointDigest: string) => ({ format: 1, mode: "managed_selected_source_checkpoint_v1", checkpointDigest, request: { selectedPaths: ["desktop/approved.ts"] }, files: [{ path: "desktop/approved.ts", bytesBase64: "secret" }] });
const plan = (checkpointDigest: string, changed = false) => {
  const value: any = {
    format: 1, mode: "managed_selected_merge_plan_v1", checkpointDigest, selectedPaths: ["desktop/approved.ts"],
    files: [{ path: "desktop/approved.ts", action: "replace", rawSha256: hash("c"), mode: "100644", expectedBaseOid: hash("d"), beforeSha256: null, expectedBaseMode: null }],
    stable: { worktreeRoot: "C:\\stable-private", gitDir: "git", commonDir: "common", branch: "main", head: hash("1"), rawIndexSha256: hash(changed ? "9" : "2") },
    sandbox: { worktreeRoot: "C:\\sandbox-private", gitDir: "git", commonDir: "common", branch: "experiment/sandbox", head: hash("3"), rawIndexSha256: hash("4") },
    expectedMergedTree: hash("5"), expectedCommit: hash("6"), preservationDigest: hash("7"), sourceHistoryMerged: false, blockers: [], execution: false, readyForLaunch: false,
  };
  value.planDigest = digest(value);
  return value;
};
const receipt = (approvedPlan: any) => {
  const value: any = { format: 1, mode: "managed_selected_merge_receipt_v1", status: "source_merge_verified", planDigest: approvedPlan.planDigest, checkpointDigest: approvedPlan.checkpointDigest, selectedPaths: [...approvedPlan.selectedPaths], stableHeadBefore: approvedPlan.stable.head, stableHeadAfter: approvedPlan.expectedCommit, mergedTree: approvedPlan.expectedMergedTree, sourceMergeExecuted: true, sourceHistoryMerged: false, readyForLaunch: false };
  value.receiptDigest = digest(value);
  return value;
};
function reseal(value: any, key: string) { delete value[key]; value[key] = digest(value); return value; }
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; }
function harness(overrides: Record<string, any> = {}, checkpointDigest = newCheckpointDigest()) {
  const events: string[] = [];
  let trusted = true, reads = 0, merges = 0, confirmedPlan: any, lastCheckpoint: any;
  const deps = {
    isTrusted: () => trusted, isFixture: false,
    validateRequest: (value: any) => structuredClone(value),
    loadBackend: async () => { events.push("backend"); },
    resolveRoots: async () => { events.push("roots"); return { stableRoot: "stable", sandboxRoot: "sandbox" }; },
    readCheckpoint: () => { events.push("checkpoint"); reads++; lastCheckpoint = checkpoint(checkpointDigest); return lastCheckpoint; },
    prepare: ({ checkpoint: current }: any) => { events.push("prepare"); return plan(current.checkpointDigest); },
    confirm: ({ plan: current }: any) => { events.push("confirm"); confirmedPlan = current; return true; },
    execute: ({ plan: current, finalCheckpoint }: any) => { events.push("execute"); merges++; assert.equal(finalCheckpoint, lastCheckpoint); return receipt(current); },
    ...overrides,
  };
  return { handler: createManagedSelectedMergeHandler(deps), request: () => request(checkpointDigest), deps, events, confirmedPlan: () => confirmedPlan, setTrusted: (value: boolean) => { trusted = value; }, reads: () => reads, merges: () => merges };
}

test("confirms complete bindings then synchronously rereads, replans, and executes with final checkpoint", async () => {
  const h = harness(); const result = await h.handler({}, h.request());
  assert.equal(result.ok, true); assert.equal(h.merges(), 1);
  assert.deepEqual(h.events, ["backend", "roots", "checkpoint", "prepare", "confirm", "checkpoint", "prepare", "execute"]);
  assert.equal(h.confirmedPlan().stable.rawIndexSha256, hash("2"));
  assert.deepEqual((result as any).receipt, receipt(plan(h.request().checkpointDigest)));
  assert.doesNotMatch(JSON.stringify(result), /private|bytesBase64/);
});

test("cancel, stale checkpoint, changed plan and trust loss never execute", async () => {
  const cancelled = harness({ confirm: () => false });
  const stale = harness(); let reads = 0;
  stale.deps.readCheckpoint = () => checkpoint(++reads === 1 ? stale.request().checkpointDigest : hash("e"));
  const changed = harness(); let prepares = 0;
  changed.deps.prepare = ({ checkpoint: current }: any) => plan(current.checkpointDigest, ++prepares > 1);
  const untrusted = harness(); untrusted.deps.confirm = () => { untrusted.setTrusted(false); return true; };
  for (const h of [cancelled, stale, changed, untrusted]) { assert.equal((await h.handler({}, h.request())).ok, false); assert.equal(h.merges(), 0); }
});

test("rejects bad arity, fixture/untrusted callers, busy requests and thenable checkpoint", async () => {
  for (const extra of [false, true]) { const h = harness(); const args = extra ? [h.request(), h.request()] : []; assert.equal((await h.handler({}, ...args)).ok, false); assert.deepEqual(h.events, []); }
  for (const overrides of [{ isFixture: true }, { isTrusted: () => false }, { readCheckpoint: async () => checkpoint(hash("a")) }]) {
    const h = harness(overrides); assert.equal((await h.handler({}, h.request())).ok, false); assert.equal(h.merges(), 0);
  }
  const entered = deferred<void>(), answer = deferred<boolean>();
  const h = harness({ confirm: () => { entered.resolve(); return answer.promise; } });
  const first = h.handler({}, h.request()); await entered.promise;
  assert.equal((await h.handler({}, h.request())).ok, false); answer.resolve(false); await first;
});

test("surfaces safe refusal code and only normalized source paths, never arbitrary exception text", async () => {
  const h = harness({ prepare: () => { const error: any = new Error("sensitive raw message"); error.code = "selected_overlap"; error.paths = ["desktop/ok.ts", "desktop/bad\nname.ts", "desktop/colon:name.ts", "C:/private/file.ts", "node_modules/pkg/index.js"]; throw error; } });
  const result = await h.handler({}, h.request());
  assert.equal((result as any).code, "selected_overlap"); assert.deepEqual((result as any).paths, ["desktop/ok.ts"]);
  assert.doesNotMatch(JSON.stringify(result), /sensitive|bad|colon|private|node_modules/);
});

const unknownExecutions = [
  () => { throw Error("unknown private outcome"); },
  ({ plan: current }: any) => Promise.resolve(receipt(current)),
  ({ plan: current }: any) => ({ ...receipt(current), selectedPaths: ["desktop/wrong.ts"] }),
  () => ({ status: "maintenance_required", maintenanceRequired: true, errorCode: "selected_merge_postcondition_failed" }),
];
test("every execute uncertainty consumes the digest and blocks the second call at entry", async () => {
  for (const unknown of unknownExecutions) {
    let executions = 0;
    const h = harness({ execute: (input: any) => { executions++; return unknown(input); } });
    const first = await h.handler({}, h.request());
    assert.equal(first.ok, false); assert.equal((first as any).maintenanceRequired, true);
    const events = [...h.events];
    const second = await h.handler({}, h.request());
    assert.equal(second.ok, false); assert.equal((second as any).maintenanceRequired, true);
    assert.match(second.message, /请勿重试/); assert.equal(executions, 1);
    assert.deepEqual(h.events, events, "repeat must not load backend, resolve roots, read checkpoint, prepare, or confirm");
  }
});

test("verified success consumes the digest and repeated requests are refused, never cached success", async () => {
  const h = harness(); assert.equal((await h.handler({}, h.request())).ok, true);
  const events = [...h.events], second = await h.handler({}, h.request());
  assert.equal(second.ok, false); assert.equal((second as any).code, "selected_merge_already_completed");
  assert.equal("receipt" in second, false); assert.equal(h.merges(), 1); assert.deepEqual(h.events, events);
});

test("digest guards are shared by handler instances in the host process", async () => {
  for (const fail of [false, true]) {
    const checkpointDigest = newCheckpointDigest();
    const first = harness(fail ? { execute: () => { throw Error("unknown"); } } : {}, checkpointDigest);
    await first.handler({}, first.request());
    const second = harness({}, checkpointDigest); const result = await second.handler({}, second.request());
    assert.equal(result.ok, false); assert.equal(Boolean((result as any).maintenanceRequired), fail);
    assert.deepEqual(second.events, []); assert.equal(second.merges(), 0);
  }
});

test("digest is consumed before invoking execute, including reentrant calls", async () => {
  let reentered!: Promise<any>, executions = 0;
  const h = harness({ execute: () => { executions++; reentered = h.handler({}, h.request()); throw Error("unknown"); } });
  await h.handler({}, h.request());
  assert.equal((await reentered).maintenanceRequired, true); assert.equal(executions, 1);
  assert.equal(h.events.filter(event => event === "confirm").length, 1);
});

test("a second handler already awaiting confirmation cannot execute an already consumed digest", async () => {
  const checkpointDigest = newCheckpointDigest(), entered = deferred<void>(), answer = deferred<boolean>();
  const waiting = harness({ confirm: () => { entered.resolve(); return answer.promise; } }, checkpointDigest);
  const pending = waiting.handler({}, waiting.request()); await entered.promise;
  const winner = harness({}, checkpointDigest); assert.equal((await winner.handler({}, winner.request())).ok, true);
  answer.resolve(true); const result = await pending;
  assert.equal(result.ok, false); assert.equal((result as any).code, "selected_merge_already_completed"); assert.equal(waiting.merges(), 0);
});

test("rejects symlink mode, mismatched object format, and an unapproved post-merge HEAD", async () => {
  for (const change of [(value: any) => { value.files[0].mode = "120000"; }, (value: any) => { value.expectedCommit = "6".repeat(40); }]) {
    const h = harness({ prepare: ({ checkpoint: current }: any) => { const value = plan(current.checkpointDigest); change(value); return reseal(value, "planDigest"); } });
    assert.equal((await h.handler({}, h.request())).ok, false); assert.equal(h.merges(), 0);
  }
  const h = harness({ execute: ({ plan: current }: any) => { const value = receipt(current); value.stableHeadAfter = "9".repeat(40); return reseal(value, "receiptDigest"); } });
  assert.equal((await h.handler({}, h.request()) as any).maintenanceRequired, true);
});

test("rejects thenable prepare before confirmation without consuming the digest", async () => {
  const h = harness({ prepare: ({ checkpoint: current }: any) => Promise.resolve(plan(current.checkpointDigest)) });
  assert.equal((await h.handler({}, h.request())).ok, false); assert.equal(h.events.includes("confirm"), false);
  h.deps.prepare = ({ checkpoint: current }: any) => plan(current.checkpointDigest);
  assert.equal((await h.handler({}, h.request())).ok, true);
});

test("native bridge rejects malformed preservation bytes or paths before presenting consent", async () => {
  for (const invalid of [{ mode: "managed_stable_working_preservation_v1", files: [] }, { mode: "unknown", files: [{ path: "desktop/approved.ts" }] }]) {
    const h = harness({ prepare: ({ checkpoint: current }: any) => {
      const p = plan(current.checkpointDigest); p.stablePreservation = invalid; return reseal(p, "planDigest");
    } });
    const result = await h.handler({}, h.request());
    assert.equal(result.ok, false);
    assert.equal(h.merges(), 0);
    assert.ok(!h.events.includes("confirm"));
  }
});

function withPreservation(checkpointDigest: string) {
  const p = plan(checkpointDigest);
  const bytes = Buffer.from("stable original bytes\n");
  const originalHash = createHash("sha256").update(bytes).digest("hex");
  const originalOid = createHash("sha256").update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest("hex");
  p.files[0].beforeSha256 = originalHash; p.files[0].expectedBaseMode = "100644";
  const author = "Managed Selected Merge <managed-selected-merge@invalid> 0 +0000";
  const commit = (tree: string, parent: string, message: string) => {
    const body = Buffer.from(`tree ${tree}\nparent ${parent}\nauthor ${author}\ncommitter ${author}\n\n${message}\n`);
    return createHash("sha256").update(Buffer.from(`commit ${body.length}\0`)).update(body).digest("hex");
  };
  const tree = hash("8");
  const preserved = commit(tree, p.stable.head, `Preserve stable working bytes for checkpoint ${checkpointDigest}`);
  p.stablePreservation = { mode: "managed_stable_working_preservation_v1", rawBytes: bytes.length, expectedTree: tree, expectedCommit: preserved,
    files: [{ path: "desktop/approved.ts", sha256: originalHash, oid: originalOid, mode: "100644", bytesBase64: bytes.toString("base64") }] };
  p.expectedCommit = commit(p.expectedMergedTree, preserved, `Selected content from checkpoint ${checkpointDigest}`);
  return reseal(p, "planDigest");
}
test("native bridge accepts bound preservation ancestry without leaking original bytes in result", async () => {
  const h = harness({ prepare: ({ checkpoint: current }: any) => withPreservation(current.checkpointDigest) });
  const r = await h.handler({}, h.request());
  assert.equal(r.ok, true); assert.equal(h.merges(), 1);
  assert.equal(h.confirmedPlan().stablePreservation.mode, "managed_stable_working_preservation_v1");
  assert.doesNotMatch(JSON.stringify(r), /bytesBase64|stable original bytes/);
});
test("native bridge refuses preservation tampering even after the outer plan is resealed", async () => {
  for (const change of [
    (p: any) => { p.stablePreservation.files[0].path = "desktop/other.ts"; },
    (p: any) => { p.stablePreservation.files[0].bytesBase64 = "Zm9yZ2Vk"; },
    (p: any) => { p.stablePreservation.rawBytes++; },
    (p: any) => { p.expectedCommit = hash("9"); },
  ]) {
    const h = harness({ prepare: ({ checkpoint: current }: any) => { const p = withPreservation(current.checkpointDigest); change(p); return reseal(p, "planDigest"); } });
    assert.equal((await h.handler({}, h.request())).ok, false); assert.equal(h.merges(), 0); assert.ok(!h.events.includes("confirm"));
  }
});
