import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdtempSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { createSelfEvolutionConfirmationService } from "../electron/self-evolution-confirmation.ts";
import { createSelfEvolutionMergeOperationStore, SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED } from "../launcher/self-evolution-selected-merge-store.mjs";
import { validateSelfEvolutionNativeConfirmation } from "../launcher/self-evolution-selected-merge.mjs";

const hash = (value: unknown) => createHash("sha256").update(canonical(value)).digest("hex");
const canonical = (value: any): string => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
  : value && typeof value === "object" ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`
  : JSON.stringify(value);
const seal = (value: Record<string, any>, field: string) => ({ ...value, [field]: hash(Object.fromEntries(Object.entries(value).filter(([key]) => key !== field))) });
const filePath = "desktop/electron/example.ts";

function storeOperation() {
  const candidate = {
    format: 1, mode: "self_evolution_candidate_binding_v1", taskId: "task-1", attemptId: "attempt-2",
    candidateId: "candidate-3", candidateDigest: "a".repeat(64), baselineManifestDigest: "b".repeat(64),
    gitCommonId: "common-1", stableWorktreeId: "stable-1", candidateWorktreeId: "candidate-tree-1", baseHead: "c".repeat(40),
  };
  const stableIndexBytes = Buffer.from("synthetic stable index");
  const stableState = {
    head: candidate.baseHead, indexSha256: createHash("sha256").update(stableIndexBytes).digest("hex"),
    worktreeDigest: "e".repeat(64), gitMetadataDigest: "f".repeat(64), gitCommonId: candidate.gitCommonId, worktreeId: candidate.stableWorktreeId,
  };
  const content = "safe fixture\n";
  const selectedFiles = [{ path: filePath, action: "add", beforeSha256: null, afterSha256: createHash("sha256").update(content).digest("hex"),
    beforeMode: null, afterMode: "100644", beforeOid: null, afterOid: "1".repeat(40), beforeSize: null,
    afterSize: Buffer.byteLength(content), contentRef: "blob-ref-1" }];
  const verificationRefs = [{ checkId: "targeted-test", candidateDigest: candidate.candidateDigest, evidenceRef: "evidence-1", status: "passed" }];
  const checkpoint = seal({ format: 1, mode: "self_evolution_merge_checkpoint_v1", taskId: candidate.taskId,
    attemptId: candidate.attemptId, candidateId: candidate.candidateId, candidateDigest: candidate.candidateDigest,
    baselineManifestDigest: candidate.baselineManifestDigest, stableState, candidateHead: "9".repeat(40),
    candidateWorktreeDigest: "8".repeat(64), selectedFiles, verificationRefs, privateBlobSetRef: "blob-set-1" }, "checkpointDigest");
  const preview = seal({ format: 1, mode: "self_evolution_promotion_preview_v1", taskId: candidate.taskId,
    attemptId: candidate.attemptId, candidateId: candidate.candidateId, candidateDigest: candidate.candidateDigest,
    checkpointDigest: checkpoint.checkpointDigest, stableState, stableStateDigest: hash(stableState), selectedFiles,
    verificationRefs, blockers: [] }, "previewDigest");
  const request = { format: 1, mode: "self_evolution_native_confirmation_request_v1", operationId: "operation-4",
    taskId: candidate.taskId, attemptId: candidate.attemptId, candidateId: candidate.candidateId,
    candidateDigest: candidate.candidateDigest, checkpointDigest: checkpoint.checkpointDigest,
    stableStateDigest: preview.stableStateDigest, previewDigest: preview.previewDigest, selectedPaths: [filePath] };
  return { candidate, checkpoint, preview, request, stableIndexBytes };
}
function intentFor(receipt: any) {
  return seal({ format: 1, mode: "self_evolution_merge_intent_v1", state: "prepared",
    operationId: receipt.operationId, taskId: receipt.taskId, attemptId: receipt.attemptId, candidateId: receipt.candidateId,
    candidateDigest: receipt.candidateDigest, checkpointDigest: receipt.checkpointDigest,
    stableStateDigest: receipt.stableStateDigest, previewDigest: receipt.previewDigest, confirmationNonce: receipt.nonce }, "intentDigest");
}
function setup(targetRequest: any, authorityPreview: any, overrides: Record<string, unknown> = {}) {
  let currentTime = Date.now();
  const calls: unknown[] = [];
  let resolverCalls = 0;
  const service = createSelfEvolutionConfirmationService({
    isTrustedSender: (event: unknown) => event === "trusted-main-frame" || event === "sender-A" || event === "sender-B",
    senderIdentity: (event: unknown) => String(event),
    sessionIdentity: () => "session-1",
    resolveAuthoritativePreview: async (_event: unknown, operationId: string) => {
      resolverCalls++;
      return operationId === targetRequest.operationId
        ? { ...authorityPreview, sessionId: "session-1", operationId: targetRequest.operationId, display: { title: "review", summary: "Review changes", verificationSummary: "Checks passed", exclusions: "No launch." } }
        : null;
    },
    showNativeConfirmation: async (_event: unknown, context: unknown) => {
      calls.push(context); return (overrides.answer as boolean | undefined) ?? true;
    },
    authorizationNotConsumed: SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED,
    now: () => currentTime,
    ...overrides,
  } as any);
  return { service, calls, resolverCalls: () => resolverCalls, advance: (ms: number) => { currentTime += ms; } };
}
const defaultOperation = storeOperation();
const defaultPreview = { ...defaultOperation.preview, sessionId: "session-1",
  display: { title: "Review", summary: "Review changes", verificationSummary: "Checks passed", exclusions: "No launch." } };
const defaultRequest = defaultOperation.request;

test("cancelled native confirmation issues no receipt", async () => {
  const { service, calls } = setup(defaultRequest, defaultPreview, { answer: false });
  const result = await service.request("trusted-main-frame", defaultRequest);
  assert.equal(result.ok, false); assert.equal(result.code, "cancelled"); assert.equal(calls.length, 1);
});

test("issued receipt exactly satisfies the adapter confirmation validator", async () => {
  const { service } = setup(defaultRequest, defaultPreview);
  const result = await service.request("trusted-main-frame", defaultRequest);
  assert.equal(result.ok, true); if (!result.ok) return;
  assert.deepEqual(Object.keys(result.receipt).sort(), ["attemptId", "candidateDigest", "candidateId", "checkpointDigest", "decision", "format", "issuedAt", "issuer", "mode", "nonce", "operationId", "previewDigest", "selectedPaths", "stableStateDigest", "taskId"].sort());
  assert.equal("receiptId" in result.receipt, false); assert.equal("expiresAt" in result.receipt, false);
  assert.deepEqual(validateSelfEvolutionNativeConfirmation(result.receipt, defaultPreview), result.receipt);
});

test("untrusted sender and forged binding are rejected before authority lookup or dialog", async () => {
  const { service, calls, resolverCalls } = setup(defaultRequest, defaultPreview);
  assert.equal((await service.request("forged-frame", defaultRequest)).ok, false);
  assert.equal((await service.request("trusted-main-frame", { ...defaultRequest, candidateDigest: "e".repeat(64) })).ok, false);
  assert.equal(resolverCalls(), 1, "only the trusted but mismatched request may reach the Host resolver"); assert.equal(calls.length, 0);
});

test("claimAuthorization is synchronous and one-shot under the real durable store.claim", async (t) => {
  const data = storeOperation();
  const { service } = setup(data.request, data.preview);
  const confirmation = await service.request("trusted-main-frame", data.request);
  assert.equal(confirmation.ok, true); if (!confirmation.ok) return;
  const intent = intentFor(confirmation.receipt);
  const root = mkdtempSync(path.join(os.tmpdir(), "se-confirm-claim-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const store = createSelfEvolutionMergeOperationStore({ root });
  const prepared = store.prepare({ candidate: data.candidate, checkpoint: data.checkpoint, preview: data.preview, nativeConfirmation: confirmation.receipt, intent, stableIndexBytes: data.stableIndexBytes });
  let callbackCalls = 0;
  const claimed = store.claim(data.request.operationId, prepared.intentDigest, { assertAuthorized: () => {
    callbackCalls++;
    const result = service.claimAuthorization(confirmation.receipt, prepared.intent, "trusted-main-frame");
    assert.equal(typeof (result as any)?.then, "undefined", "claim authorization must not return a Promise");
    return result;
  }});
  assert.equal(claimed.status, "executing"); assert.equal(callbackCalls, 1);
  assert.equal(store.read(data.request.operationId).status, "maintenance_required");
  assert.throws(() => store.claim(data.request.operationId, prepared.intentDigest, { assertAuthorized: () => service.claimAuthorization(confirmation.receipt, intent, "trusted-main-frame") }), { code: "self_evolution_intent_unavailable" });
});

test("a process-restarted service cannot replay an old receipt into a prepared durable intent", async (t) => {
  const data = storeOperation();
  const firstService = setup(data.request, data.preview).service;
  const confirmation = await firstService.request("trusted-main-frame", data.request);
  assert.equal(confirmation.ok, true); if (!confirmation.ok) return;
  const intent = intentFor(confirmation.receipt);
  const root = mkdtempSync(path.join(os.tmpdir(), "se-confirm-restart-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const store = createSelfEvolutionMergeOperationStore({ root });
  const prepared = store.prepare({ candidate: data.candidate, checkpoint: data.checkpoint, preview: data.preview, nativeConfirmation: confirmation.receipt, intent, stableIndexBytes: data.stableIndexBytes });
  const restartedService = setup(data.request, data.preview).service;
  assert.throws(() => store.claim(data.request.operationId, prepared.intentDigest, {
    assertAuthorized: () => restartedService.claimAuthorization(confirmation.receipt, prepared.intent, "trusted-main-frame"),
  }), { code: "self_evolution_confirmation_untrusted" });
  assert.equal(store.read(data.request.operationId).status, "prepared");
});

test("expiry metadata stays private and expired authorization returns the store's non-consumption sentinel", async () => {
  const state = setup(defaultRequest, defaultPreview);
  const issued = await state.service.request("trusted-main-frame", defaultRequest);
  assert.equal(issued.ok, true); if (!issued.ok) return;
  assert.equal("expiresAt" in issued.receipt, false);
  state.advance(61_000);
  assert.equal(state.service.claimAuthorization(issued.receipt, intentFor(issued.receipt), "trusted-main-frame"), SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED);
});

test("preview changing while native confirmation is open invalidates the receipt", async () => {
  const data = storeOperation(); let reads = 0; let dialogs = 0;
  const state = setup(data.request, data.preview, { resolveAuthoritativePreview: async () => {
    reads++;
    return { ...data.preview, sessionId: "session-1", operationId: data.request.operationId,
      display: { title: "review", summary: "Review changes", verificationSummary: "Checks passed", exclusions: "No launch." },
      previewDigest: reads === 1 ? data.preview.previewDigest : "e".repeat(64) };
  }, showNativeConfirmation: async () => { dialogs++; return true; } });
  const result = await state.service.request("trusted-main-frame", data.request);
  assert.equal(result.ok, false); assert.equal(result.code, "binding_mismatch");
  assert.equal(reads, 2); assert.equal(dialogs, 1);
});

test("confirmation rejects selected file metadata or stable state changed while dialog is open", async () => {
  for (const mutate of [
    (p: any) => { p.selectedFiles[0].afterSha256 = "1".repeat(64); },
    (p: any) => { p.selectedFiles[0].afterMode = "100755"; },
    (p: any) => { p.selectedFiles[0].afterSize += 1; },
    (p: any) => { p.stableState.head = "1".repeat(40); },
    (p: any) => { p.stableState.indexSha256 = "2".repeat(64); },
  ]) {
    const data = storeOperation(); let reads = 0; let dialogs = 0;
    const state = setup(data.request, data.preview, {
      resolveAuthoritativePreview: async () => {
        const next = structuredClone(data.preview);
        if (++reads === 2) mutate(next);
        return { ...next, sessionId: "session-1", operationId: data.request.operationId,
          display: { title: "review", summary: "Review changes", verificationSummary: "Checks passed", exclusions: "No launch." } };
      },
      showNativeConfirmation: async () => { dialogs++; return true; },
    });
    const result = await state.service.request("trusted-main-frame", data.request);
    assert.deepEqual(result, { ok: false, code: "binding_mismatch" });
    assert.equal(dialogs, 1, "the mutation must be detected after the dialog");
  }
});

test("dialog cannot mutate the captured display and make a changed re-read look approved", async () => {
  const data = storeOperation(); let reads = 0; let dialogs = 0;
  const display = { title: "review", summary: "Original", verificationSummary: "Checks passed", exclusions: "No launch." };
  const state = setup(data.request, data.preview, {
    resolveAuthoritativePreview: async () => ({ ...data.preview, sessionId: "session-1", operationId: data.request.operationId,
      display: { ...display, summary: ++reads === 1 ? "Original" : "Changed" } }),
    showNativeConfirmation: async (_event: unknown, shown: any) => { dialogs++; shown.display.summary = "Changed"; return true; },
  });
  assert.deepEqual(await state.service.request("trusted-main-frame", data.request), { ok: false, code: "binding_mismatch" });
  assert.equal(dialogs, 1);
});

test("confirmation rejects a forged initial canonical preview digest before dialog", async () => {
  const data = storeOperation();
  const corrupted = structuredClone(data.preview);
  corrupted.selectedFiles[0].afterSha256 = "1".repeat(64);
  const state = setup(data.request, corrupted);
  assert.deepEqual(await state.service.request("trusted-main-frame", data.request), { ok: false, code: "binding_mismatch" });
  assert.equal(state.calls.length, 0);
});

test("canonical preview rejects inconsistent stable digest and unknown wrapper fields", async () => {
  const data = storeOperation();
  const invalidStableDigest = seal({ ...data.preview, stableStateDigest: "0".repeat(64) }, "previewDigest");
  const alteredRequest = { ...data.request, stableStateDigest: invalidStableDigest.stableStateDigest, previewDigest: invalidStableDigest.previewDigest };
  const first = setup(alteredRequest, invalidStableDigest);
  assert.deepEqual(await first.service.request("trusted-main-frame", alteredRequest), { ok: false, code: "binding_mismatch" });
  assert.equal(first.calls.length, 0);
  const second = setup(data.request, data.preview, { resolveAuthoritativePreview: async () => ({ ...data.preview, operationId: data.request.operationId, sessionId: "session-1", display: {}, extra: "unexpected" }) });
  assert.deepEqual(await second.service.request("trusted-main-frame", data.request), { ok: false, code: "binding_mismatch" });
  assert.equal(second.calls.length, 0);
});

test("service without trusted sender, authority, or claim sentinel is inert", async () => {
  const service = createSelfEvolutionConfirmationService({} as any);
  assert.equal((await service.request("x", defaultRequest)).ok, false);
  assert.equal(service.claimAuthorization({}, {}, "x"), undefined);
});
test("authorization is bound to the sender that received the native confirmation", async () => {
  const data = storeOperation();
  const state = setup(data.request, data.preview, { isTrustedSender: (event: unknown) => event === "sender-A" || event === "sender-B", senderIdentity: (event: unknown) => String(event) });
  const confirmation = await state.service.request("sender-A", data.request);
  assert.equal(confirmation.ok, true); if (!confirmation.ok) return;
  assert.equal(state.service.claimAuthorization(confirmation.receipt, intentFor(confirmation.receipt), "sender-B"), SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED);
  assert.equal(state.service.claimAuthorization(confirmation.receipt, intentFor(confirmation.receipt), "sender-A"), true);
  assert.equal(state.service.claimAuthorization(confirmation.receipt, intentFor(confirmation.receipt), "sender-A"), SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED);
});

test("authorization cannot be consumed from another session sharing the same sender identity", async () => {
  const sessionPreview = { ...defaultPreview, operationId: defaultRequest.operationId, sessionId: "session-A" };
  const service = createSelfEvolutionConfirmationService({
    isTrustedSender: () => true,
    senderIdentity: () => "same-webcontents",
    sessionIdentity: (event: any) => event.sessionId,
    resolveAuthoritativePreview: async (event: any) => ({ ...sessionPreview, sessionId: event.sessionId, display: { title: "Review", summary: "Summary", verificationSummary: "Checks passed", exclusions: "No launch." } }),
    showNativeConfirmation: async () => true,
    authorizationNotConsumed: SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED,
  });
  const confirmedEvent = { sessionId: "session-A" };
  const result = await service.request(confirmedEvent, defaultRequest);
  assert.equal(result.ok, true); if (!result.ok) return;
  assert.equal(service.claimAuthorization(result.receipt, intentFor(result.receipt), { sessionId: "session-B" }), SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED);
  assert.equal(service.claimAuthorization(result.receipt, intentFor(result.receipt), confirmedEvent), true);
});

test("same operation cannot open or issue a second native confirmation", async () => {
  const state = setup(defaultRequest, defaultPreview);
  const first = await state.service.request("trusted-main-frame", defaultRequest);
  const second = await state.service.request("trusted-main-frame", defaultRequest);
  assert.equal(first.ok, true);
  assert.deepEqual(second, { ok: false, code: "busy" });
  assert.equal(state.calls.length, 1);
});


test("wrong sender cannot consume a prepared store operation, then the confirmed sender can claim it", async (t) => {
  const data = storeOperation();
  const state = setup(data.request, data.preview, { senderIdentity: (event: unknown) => String(event) });
  const confirmation = await state.service.request("sender-A", data.request);
  assert.equal(confirmation.ok, true); if (!confirmation.ok) return;
  const intent = intentFor(confirmation.receipt);
  const root = mkdtempSync(path.join(os.tmpdir(), "se-confirm-sender-store-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const store = createSelfEvolutionMergeOperationStore({ root });
  const prepared = store.prepare({ candidate: data.candidate, checkpoint: data.checkpoint, preview: data.preview,
    nativeConfirmation: confirmation.receipt, intent, stableIndexBytes: data.stableIndexBytes });
  assert.throws(() => store.claim(data.request.operationId, prepared.intentDigest, {
    assertAuthorized: () => state.service.claimAuthorization(confirmation.receipt, intent, "sender-B"),
  }), { code: "self_evolution_confirmation_untrusted" });
  assert.equal(store.read(data.request.operationId).status, "prepared");
  assert.equal(store.claim(data.request.operationId, prepared.intentDigest, {
    assertAuthorized: () => state.service.claimAuthorization(confirmation.receipt, intent, "sender-A"),
  }).status, "executing");
});

test("native confirmation detail renders the authoritative display and every bound file change without truncation", async () => {
  const { buildSelfEvolutionNativeConfirmationDetail } = await import("../electron/self-evolution-confirmation.ts");
  const preview = {
    ...defaultPreview,
    stableState: { head: "e".repeat(40), indexSha256: "f".repeat(64) },
    display: { title: "Review candidate", summary: "Two reviewed source changes.", verificationSummary: "unit-check: passed for the displayed candidate digest", exclusions: "No history merge, activation, release, or launch is authorized." },
    selectedFiles: [
      { path: "desktop/a.ts", action: "modify", beforeSha256: "1".repeat(64), afterSha256: "2".repeat(64), beforeMode: "100644", afterMode: "100644", beforeSize: 12, afterSize: 14 },
      { path: "desktop/b.ts", action: "add", beforeSha256: null, afterSha256: "3".repeat(64), beforeMode: null, afterMode: "100755", beforeSize: null, afterSize: 20 },
    ],
  };
  const detail = buildSelfEvolutionNativeConfirmationDetail(preview as any);
  assert.ok(detail);
  assert.match(detail!, /Review candidate/);
  assert.match(detail!, /unit-check: passed/);
  assert.match(detail!, /desktop\/a\.ts \| modify/);
  assert.match(detail!, /desktop\/b\.ts \| add/);
  assert.match(detail!, /No history merge/);
  assert.match(detail!, /3{64}/);
});

test("native confirmation display fails closed for controls, incomplete manifest, or oversized detail", async () => {
  const { buildSelfEvolutionNativeConfirmationDetail } = await import("../electron/self-evolution-confirmation.ts");
  const valid = {
    ...defaultPreview,
    stableState: { head: "e".repeat(40), indexSha256: "f".repeat(64) },
    display: { title: "Review", summary: "Summary", verificationSummary: "Checks passed", exclusions: "No launch." },
    selectedFiles: [{ path: "desktop/a.ts", action: "add", beforeSha256: null, afterSha256: "a".repeat(64), beforeMode: null, afterMode: "100644", beforeSize: null, afterSize: 1 }],
  };
  assert.equal(buildSelfEvolutionNativeConfirmationDetail({ ...valid, display: { ...valid.display, summary: "unsafe\ntext" } } as any), null);
  assert.equal(buildSelfEvolutionNativeConfirmationDetail({ ...valid, selectedFiles: [{ path: "desktop/a.ts" }] } as any), null);
  assert.equal(buildSelfEvolutionNativeConfirmationDetail({ ...valid, display: { ...valid.display, summary: "x".repeat(9000) } } as any), null);
});
test("native dialog failure issues no receipt and does not authorize a durable claim", async (t) => {
  const data = storeOperation();
  const state = setup(data.request, data.preview, { showNativeConfirmation: async () => { throw new Error("dialog host crashed"); } });
  const confirmation = await state.service.request("trusted-main-frame", data.request);
  assert.deepEqual(confirmation, { ok: false, code: "unavailable" });
  const later = await state.service.request("trusted-main-frame", data.request);
  assert.deepEqual(later, { ok: false, code: "unavailable" });
  assert.equal(state.resolverCalls(), 2);
  assert.equal(state.calls.length, 0);
});


test("resolver receives only the operation lookup key and renderer authority fields cannot override its preview", async () => {
  const data = storeOperation();
  let lookup: unknown;
  let dialogs = 0;
  const state = setup(data.request, data.preview, {
    resolveAuthoritativePreview: async (_event: unknown, lookupKey: unknown) => {
      lookup = lookupKey;
      return { ...data.preview, sessionId: "session-1", operationId: data.request.operationId,
        display: { title: "review", summary: "Review changes", verificationSummary: "Checks passed", exclusions: "No launch." } };
    },
    showNativeConfirmation: async () => { dialogs++; return true; },
  });
  const forged = { ...data.request, candidateDigest: "d".repeat(64), previewDigest: "c".repeat(64) };
  assert.deepEqual(await state.service.request("trusted-main-frame", forged), { ok: false, code: "binding_mismatch" });
  assert.equal(lookup, data.request.operationId, "resolver gets only the lookup key, never caller-supplied authority fields");
  assert.equal(dialogs, 0, "a private preview that does not match the renderer's claimed binding cannot be confirmed");
});
