import assert from "node:assert/strict";
import { test } from "node:test";
import { createHash } from "node:crypto";
import { selfEvolutionPromotionPreviewDigest } from "../launcher/self-evolution-selected-merge.mjs";
import { registerSelfEvolutionConfirmationIpc } from "../electron/self-evolution-confirmation-ipc.ts";
import { createSelfEvolutionConfirmationService } from "../electron/self-evolution-confirmation.ts";
import { SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED } from "../launcher/self-evolution-selected-merge-store.mjs";

const canonical = (v: any): string => Array.isArray(v) ? `[${v.map(canonical).join(",")}]`
  : v && typeof v === "object" ? `{${Object.keys(v).sort().map(k => `${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}` : JSON.stringify(v);
const stableState = { head: "e".repeat(40), indexSha256: "f".repeat(64), worktreeDigest: "1".repeat(64), gitMetadataDigest: "2".repeat(64), gitCommonId: "common", worktreeId: "stable" };
const file = { path: "desktop/electron/file.ts", action: "add", beforeSha256: null, afterSha256: "a".repeat(64), beforeMode: null, afterMode: "100644", beforeSize: null, afterSize: 1, beforeOid: null, afterOid: "1".repeat(40), contentRef: "blob" };
const body: any = { format: 1, mode: "self_evolution_promotion_preview_v1", taskId: "task-1", attemptId: "attempt-1", candidateId: "candidate-1", candidateDigest: "a".repeat(64), checkpointDigest: "b".repeat(64), stableState, stableStateDigest: createHash("sha256").update(canonical(stableState)).digest("hex"), selectedFiles: [file], verificationRefs: [{ checkId: "check", candidateDigest: "a".repeat(64), evidenceRef: "evidence", status: "passed" }], blockers: [] };
const sealed = (x: any) => ({ ...x, previewDigest: selfEvolutionPromotionPreviewDigest(x) });
const canonicalPreview = sealed(body);
const preview = { ...canonicalPreview, operationId: "op-1", sessionId: "session-1", display: { title: "Host preview", summary: "One reviewed file.", verificationSummary: "Checks passed", exclusions: "No launch." } };
const request = { format: 1, mode: "self_evolution_native_confirmation_request_v1", operationId: preview.operationId, taskId: preview.taskId, attemptId: preview.attemptId, candidateId: preview.candidateId, candidateDigest: preview.candidateDigest, checkpointDigest: preview.checkpointDigest, stableStateDigest: preview.stableStateDigest, previewDigest: preview.previewDigest, selectedPaths: preview.selectedFiles.map((item: any) => item.path) };

function bind(service: any) {
  let registered: ((event: unknown, ...args: unknown[]) => unknown) | undefined;
  registerSelfEvolutionConfirmationIpc({ handle(channel, handler) { assert.equal(channel, "self-evolution:request-native-confirmation"); registered = handler; } } as any, service);
  return (...args: unknown[]) => registered!(...args);
}

test("IPC adapter forwards the actual Electron event and exactly one renderer payload", async () => {
  const event = { sender: { id: 42 } };
  const service = { request: async (receivedEvent: unknown, payload: unknown) => ({ ok: receivedEvent === event, payload }) };
  assert.deepEqual(await bind(service)(event, request), { ok: true, payload: request });
});

test("IPC adapter rejects extra positional arguments without consulting confirmation service", async () => {
  let calls = 0;
  const invoke = bind({ request: async () => { calls++; return { ok: true }; } });
  assert.deepEqual(await invoke({ sender: { id: 1 } }, request, { preview }), { ok: false, code: "invalid_request" });
  assert.equal(calls, 0);
});

test("IPC confirmation is bound to trusted Electron sender and Host-resolved preview, never renderer preview", async () => {
  const sender = { id: 7 };
  const event = { sender };
  let resolverCalls = 0;
  let shown: unknown;
  const service = createSelfEvolutionConfirmationService({
    isTrustedSender: (actual: any) => actual === event && actual.sender === sender,
    senderIdentity: (actual: any) => actual.sender.id,
    sessionIdentity: () => "session-1",
    resolveAuthoritativePreview: async (_actual: unknown, operationId: string) => { resolverCalls++; return operationId === request.operationId ? preview : null; },
    showNativeConfirmation: async (_actual: unknown, authoritative: unknown) => { shown = authoritative; return true; },
    authorizationNotConsumed: SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED,
  });
  const invoke = bind(service);
  assert.deepEqual(await invoke({ sender }, request), { ok: false, code: "untrusted_sender" });
  assert.equal(resolverCalls, 0);
  const forged = await invoke(event, { ...request, preview });
  assert.deepEqual(forged, { ok: false, code: "invalid_request" });
  assert.equal(resolverCalls, 0);
  const accepted = await invoke(event, request) as any;
  assert.equal(accepted.ok, true);
  assert.deepEqual(shown, preview);
});

test("missing Host preview resolver fails closed before native dialog or receipt", async () => {
  let dialogCalls = 0;
  const service = createSelfEvolutionConfirmationService({
    isTrustedSender: (actual: unknown) => actual === eventSentinel,
    senderIdentity: () => 9,
    sessionIdentity: () => "session-1",
    resolveAuthoritativePreview: async () => null,
    showNativeConfirmation: async () => { dialogCalls++; return true; },
    authorizationNotConsumed: SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED,
  });
  const result = await bind(service)(eventSentinel, request);
  assert.deepEqual(result, { ok: false, code: "binding_mismatch" });
  assert.equal(dialogCalls, 0);
});
const eventSentinel = { sender: { id: 9 } };

test("native confirmation refuses oversized lists without issuing a receipt", async () => {
  const files = Array.from({ length: 41 }, (_, index) => ({ path: `desktop/electron/file-${index}.ts` }));
  const manyPreview = { ...preview, ...sealed({ ...body, selectedFiles: files }) };
  const manyRequest = { ...request, previewDigest: manyPreview.previewDigest, selectedPaths: files.map((item) => item.path) };
  let dialogCalls = 0;
  const service = createSelfEvolutionConfirmationService({
    isTrustedSender: () => true,
    senderIdentity: () => 7,
    sessionIdentity: () => "session-1",
    resolveAuthoritativePreview: async () => manyPreview,
    showNativeConfirmation: async () => { dialogCalls++; return true; },
    authorizationNotConsumed: SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED,
  });
  assert.deepEqual(await bind(service)({ sender: { id: 7 } }, manyRequest), { ok: false, code: "cancelled" });
  assert.equal(dialogCalls, 0);
});

test("native file list includes every selected path and rejects oversized or control-character text", async () => {
  const { buildSelfEvolutionNativeFileList } = await import("../electron/self-evolution-confirmation.ts");
  const files = Array.from({ length: 40 }, (_, index) => ({ path: `f${index}` }));
  assert.equal(buildSelfEvolutionNativeFileList(files), files.map(({path}) => `- ${path}`).join("\n"));
  assert.equal(buildSelfEvolutionNativeFileList([{ path: "x".repeat(2049) }]), null);
  assert.equal(buildSelfEvolutionNativeFileList([{ path: "x\ny" }]), null);
});


test("native confirmation rejects detail beyond the conservative platform budget before receipt issuance", async () => {
  const files = Array.from({ length: 40 }, (_, index) => ({ path: `desktop/f-${index}.ts`, action: "add", beforeSha256: null, afterSha256: "a".repeat(64), beforeMode: null, afterMode: "100644", beforeSize: null, afterSize: 1 }));
  let dialogCalls = 0;
  const service = createSelfEvolutionConfirmationService({
    isTrustedSender: () => true,
    senderIdentity: () => 7,
    sessionIdentity: () => "session-1",
    resolveAuthoritativePreview: async () => ({ ...preview, ...sealed({ ...body, selectedFiles: files }) }),
    showNativeConfirmation: async () => { dialogCalls++; return true; },
    authorizationNotConsumed: SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED,
  });
  const result = await bind(service)({ sender: { id: 7 } }, { ...request, previewDigest: sealed({ ...body, selectedFiles: files }).previewDigest, selectedPaths: files.map(({ path }) => path) });
  assert.deepEqual(result, { ok: false, code: "cancelled" });
  assert.equal(dialogCalls, 0);
});

test("IPC resolver receives only the untrusted operation lookup key, not renderer authority fields", async () => {
  const sender = { id: 11 };
  const event = { sender };
  let received: unknown;
  const service = createSelfEvolutionConfirmationService({
    isTrustedSender: (actual: any) => actual === event,
    senderIdentity: () => sender.id,
    sessionIdentity: () => "session-1",
    resolveAuthoritativePreview: async (_actual: unknown, lookupKey: unknown) => { received = lookupKey; return null; },
    showNativeConfirmation: async () => true,
    authorizationNotConsumed: SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED,
  });
  const forged = { ...request, candidateDigest: "d".repeat(64), previewDigest: "c".repeat(64), hostPreview: preview };
  assert.deepEqual(await bind(service)(event, forged), { ok: false, code: "invalid_request" });
  assert.equal(received, undefined, "extra renderer-supplied preview fields are rejected before private lookup");
  assert.deepEqual(await bind(service)(event, request), { ok: false, code: "binding_mismatch" });
  assert.equal(received, request.operationId, "IPC payload is reduced to the operation lookup key inside the service");
});
