import { createHash, randomUUID } from "node:crypto";
// @ts-expect-error Launcher canonical digest helper is untyped JavaScript.
import { selfEvolutionPromotionPreviewDigest } from "../launcher/self-evolution-selected-merge.mjs";

export type SelfEvolutionNativeConfirmationRequest = {
  format: 1;
  mode: "self_evolution_native_confirmation_request_v1";
  operationId: string;
  taskId: string;
  attemptId: string;
  candidateId: string;
  candidateDigest: string;
  checkpointDigest: string;
  stableStateDigest: string;
  previewDigest: string;
  selectedPaths: string[];
};

/** Exact public schema accepted by validateSelfEvolutionNativeConfirmation. */
export type SelfEvolutionNativeConfirmationReceipt = {
  format: 1;
  mode: "self_evolution_native_confirmation_v1";
  decision: "accepted";
  taskId: string;
  attemptId: string;
  candidateId: string;
  candidateDigest: string;
  checkpointDigest: string;
  stableStateDigest: string;
  previewDigest: string;
  selectedPaths: string[];
  operationId: string;
  nonce: string;
  issuer: "electron-main";
  issuedAt: string;
};

type AuthoritativePreview = {
  /** Resolved from host-private prepared intent; never copied from IPC as authority. */
  operationId: string;
  format: 1;
  mode: "self_evolution_promotion_preview_v1";
  taskId: string;
  sessionId: string;
  attemptId: string;
  candidateId: string;
  candidateDigest: string;
  checkpointDigest: string;
  stableStateDigest: string;
  stableState: { head: string; indexSha256: string };
  previewDigest: string;
  selectedFiles: Array<{ path: string; action?: string; beforeSha256?: string | null; afterSha256?: string | null; beforeMode?: string | null; afterMode?: string | null; beforeSize?: number | null; afterSize?: number | null }>;
  display: unknown;
};

type MergeIntentBinding = {
  operationId: string;
  taskId: string;
  attemptId: string;
  candidateId: string;
  candidateDigest: string;
  checkpointDigest: string;
  stableStateDigest: string;
  previewDigest: string;
  confirmationNonce: string;
};

type Options<Event> = {
  isTrustedSender: (event: Event) => boolean;
  /** Stable main-process identity (e.g. webContents.id), not a renderer-supplied value. */
  senderIdentity: (event: Event) => string | number;
  /** Host-authenticated session capability; a renderer-synchronized value is not sufficient. */
  sessionIdentity: (event: Event) => string;
  /** operationId is an untrusted lookup key only; resolve the preview from host-private state, never echo IPC fields as authority. */
  resolveAuthoritativePreview: (event: Event, operationId: string) => Promise<AuthoritativePreview | null>;
  showNativeConfirmation: (event: Event, preview: AuthoritativePreview) => Promise<boolean>;
  /** Import SELF_EVOLUTION_AUTHORIZATION_NOT_CONSUMED from the operation store and pass it here. */
  authorizationNotConsumed: symbol;
  now?: () => number;
  ttlMs?: number;
};

export type ConfirmationResult =
  | { ok: true; receipt: SelfEvolutionNativeConfirmationReceipt }
  | { ok: false; code: "unavailable" | "invalid_request" | "untrusted_sender" | "binding_mismatch" | "busy" | "cancelled" };

const DIGEST = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
/** Reject lists that cannot be fully presented in the bounded native dialog. */
export function buildSelfEvolutionNativeFileList(files: Array<{ path: string }>): string | null {
  if (files.length < 1 || files.length > 40) return null;
  if (files.some(({ path }) => !path || /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/u.test(path))) return null;
  const lines = files.map(({ path }) => `- ${path}`).join("\n");
  return lines.length <= 2048 ? lines : null;
}

const MAX_NATIVE_CONFIRMATION_DETAIL_BYTES = 2048;
const DISPLAY_CONTROL = /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/u;
const FILE_DIGEST = /^[a-f0-9]{64}$/;
const FILE_MODE = /^(?:100644|100755)$/;

/**
 * Build a conservative, complete native-dialog disclosure from the authoritative
 * preview. Native message boxes have platform-specific layout limits, so oversized
 * or incomplete disclosures are rejected rather than shortened with an ellipses.
 */
export function buildSelfEvolutionNativeConfirmationDetail(preview: AuthoritativePreview): string | null {
  if (!record(preview) || !record(preview.display) || !exactKeys(preview.display, ["title", "summary", "verificationSummary", "exclusions"])) return null;
  if (!record(preview.stableState) || typeof preview.stableState.head !== "string" || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(preview.stableState.head)
      || typeof preview.stableState.indexSha256 !== "string" || !FILE_DIGEST.test(preview.stableState.indexSha256)) return null;
  const display = preview.display as Record<string, unknown>;
  if (Object.values(display).some((value) => typeof value !== "string" || !value.trim() || DISPLAY_CONTROL.test(value) || value.length > 256)) return null;
  const files = preview.selectedFiles;
  if (!Array.isArray(files) || files.length < 1 || files.length > 40) return null;
  const lines: string[] = [];
  for (const file of files) {
    if (!record(file) || typeof file.path !== "string" || !file.path || file.path.length > 256 || DISPLAY_CONTROL.test(file.path)
        || !["add", "modify", "delete"].includes(String(file.action))) return null;
    const before = file.beforeSha256, after = file.afterSha256;
    const beforeMode = file.beforeMode, afterMode = file.afterMode;
    const beforeSize = file.beforeSize, afterSize = file.afterSize;
    if (!(before === null || (typeof before === "string" && FILE_DIGEST.test(before)))
        || !(after === null || (typeof after === "string" && FILE_DIGEST.test(after)))
        || !(beforeMode === null || (typeof beforeMode === "string" && FILE_MODE.test(beforeMode)))
        || !(afterMode === null || (typeof afterMode === "string" && FILE_MODE.test(afterMode)))
        || !(beforeSize === null || (Number.isSafeInteger(beforeSize) && (beforeSize as number) >= 0))
        || !(afterSize === null || (Number.isSafeInteger(afterSize) && (afterSize as number) >= 0))) return null;
    const action = file.action as string;
    if ((action === "add" && (before !== null || after === null || beforeMode !== null || afterMode === null || beforeSize !== null || afterSize === null))
        || (action === "modify" && (before === null || after === null || beforeMode === null || afterMode === null || beforeSize === null || afterSize === null))
        || (action === "delete" && (before === null || after !== null || beforeMode === null || afterMode !== null || beforeSize === null || afterSize !== null))) return null;
    lines.push(`- ${file.path} | ${action} | mode ${beforeMode ?? "none"} -> ${afterMode ?? "none"} | bytes ${beforeSize ?? "none"} -> ${afterSize ?? "none"} | sha256 ${before ?? "none"} -> ${after ?? "none"}`);
  }
  const detail = [
    `Task: ${preview.taskId} / Attempt: ${preview.attemptId}`,
    `Candidate: ${preview.candidateId}`,
    `Candidate digest: ${preview.candidateDigest}`,
    `Checkpoint digest: ${preview.checkpointDigest}`,
    `Stable HEAD: ${preview.stableState.head}`,
    `Stable index SHA-256: ${preview.stableState.indexSha256}`,
    `Stable state digest: ${preview.stableStateDigest}`,
    `Preview digest: ${preview.previewDigest}`,
    "",
    `Preview: ${display.title}`,
    String(display.summary),
    `Verification: ${display.verificationSummary}`,
    "Selected file changes (complete list):",
    ...lines,
    "",
    `Scope and exclusions: ${display.exclusions}`,
    "This one-time confirmation does not authorize source-history merge, activation, release, or launch.",
  ].join("\n");
  return Buffer.byteLength(detail, "utf8") <= MAX_NATIVE_CONFIRMATION_DETAIL_BYTES ? detail : null;
}

const DEFAULT_TTL_MS = 60_000;
const MAX_TTL_MS = 5 * 60_000;
const RECEIPT_KEYS = ["format", "mode", "decision", "taskId", "attemptId", "candidateId", "candidateDigest", "checkpointDigest", "stableStateDigest", "previewDigest", "selectedPaths", "operationId", "nonce", "issuer", "issuedAt"];
const REQUEST_KEYS = ["format", "mode", "operationId", "taskId", "attemptId", "candidateId", "candidateDigest", "checkpointDigest", "stableStateDigest", "previewDigest", "selectedPaths"];

function exactKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function stableJson(value: unknown): string {
  return Array.isArray(value) ? `[${value.map(stableJson).join(",")}]`
    : record(value) ? `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`
    : JSON.stringify(value);
}
function validRequest(value: unknown): value is SelfEvolutionNativeConfirmationRequest {
  if (!record(value) || !exactKeys(value, REQUEST_KEYS) || value.format !== 1 || value.mode !== "self_evolution_native_confirmation_request_v1") return false;
  if (![value.operationId, value.taskId, value.attemptId, value.candidateId].every((item) => typeof item === "string" && ID.test(item))) return false;
  if (![value.candidateDigest, value.checkpointDigest, value.stableStateDigest, value.previewDigest].every((item) => typeof item === "string" && DIGEST.test(item))) return false;
  return Array.isArray(value.selectedPaths) && value.selectedPaths.length > 0
    && value.selectedPaths.every((item) => typeof item === "string" && item.length > 0 && item.length <= 512)
    && new Set(value.selectedPaths).size === value.selectedPaths.length;
}
const PREVIEW_KEYS = ["format", "mode", "taskId", "attemptId", "candidateId", "candidateDigest", "checkpointDigest", "stableState", "stableStateDigest", "selectedFiles", "verificationRefs", "blockers", "previewDigest"];
const WRAPPER_KEYS = [...PREVIEW_KEYS, "operationId", "sessionId", "display"];

/** Validate the v1 digest over exactly its contract, not the Electron wrapper.
 * Hashes establish consistency only, not Host provenance or authorization. */
function validPreview(value: unknown): value is AuthoritativePreview {
  if (!record(value) || !exactKeys(value, WRAPPER_KEYS) || value.format !== 1 || value.mode !== "self_evolution_promotion_preview_v1"
      || typeof value.operationId !== "string" || !ID.test(value.operationId)
      || typeof value.sessionId !== "string" || !ID.test(value.sessionId)
      || !Array.isArray(value.selectedFiles) || !Object.hasOwn(value, "display")) return false;
  return [value.taskId, value.attemptId, value.candidateId].every((item) => typeof item === "string" && ID.test(item))
    && [value.candidateDigest, value.checkpointDigest, value.stableStateDigest, value.previewDigest].every((item) => typeof item === "string" && DIGEST.test(item))
    && record(value.stableState)
    && Array.isArray(value.verificationRefs) && Array.isArray(value.blockers) && value.blockers.length === 0
    && value.selectedFiles.every((file) => record(file) && typeof file.path === "string")
    && createHash("sha256").update(stableJson(value.stableState)).digest("hex") === value.stableStateDigest
    && selfEvolutionPromotionPreviewDigest(Object.fromEntries(PREVIEW_KEYS.map((key) => [key, value[key]]))) === value.previewDigest;
}
function previewMatchesRequest(preview: AuthoritativePreview, request: SelfEvolutionNativeConfirmationRequest): boolean {
  return preview.operationId === request.operationId && preview.taskId === request.taskId && preview.attemptId === request.attemptId
    && preview.candidateId === request.candidateId && preview.candidateDigest === request.candidateDigest
    && preview.checkpointDigest === request.checkpointDigest && preview.stableStateDigest === request.stableStateDigest
    && preview.previewDigest === request.previewDigest
    && JSON.stringify(preview.selectedFiles.map((file) => file.path)) === JSON.stringify(request.selectedPaths);
}
function receiptMatchesIntent(receipt: SelfEvolutionNativeConfirmationReceipt, intent: MergeIntentBinding): boolean {
  return receipt.operationId === intent.operationId && receipt.taskId === intent.taskId && receipt.attemptId === intent.attemptId
    && receipt.candidateId === intent.candidateId && receipt.candidateDigest === intent.candidateDigest
    && receipt.checkpointDigest === intent.checkpointDigest && receipt.stableStateDigest === intent.stableStateDigest
    && receipt.previewDigest === intent.previewDigest && receipt.nonce === intent.confirmationNonce;
}
function validReceipt(value: unknown): value is SelfEvolutionNativeConfirmationReceipt {
  if (!record(value) || !exactKeys(value, RECEIPT_KEYS) || value.format !== 1 || value.mode !== "self_evolution_native_confirmation_v1"
      || value.decision !== "accepted" || value.issuer !== "electron-main" || typeof value.nonce !== "string" || !ID.test(value.nonce)
      || typeof value.issuedAt !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value.issuedAt)
      || !Number.isFinite(Date.parse(value.issuedAt)) || new Date(value.issuedAt).toISOString() !== value.issuedAt) return false;
  return validRequest({
    format: 1, mode: "self_evolution_native_confirmation_request_v1", operationId: value.operationId,
    taskId: value.taskId, attemptId: value.attemptId, candidateId: value.candidateId,
    candidateDigest: value.candidateDigest, checkpointDigest: value.checkpointDigest,
    stableStateDigest: value.stableStateDigest, previewDigest: value.previewDigest, selectedPaths: value.selectedPaths,
  });
}

/**
 * Main-process confirmation primitive. IPC is connected fail-closed: the current
 * main-process preview resolver returns null until a trusted Host source exists.
 * claimAuthorization is synchronous and intended only for store.claim's
 * assertAuthorized callback. Expiry, receipt ids, and sender bindings stay process-private.
 */
export function createSelfEvolutionConfirmationService<Event>(options: Partial<Options<Event>> = {}) {
  const inFlight = new Set<string>();
  const issuedOperations = new Set<string>();
  const privateReceipts = new Map<string, { receiptId: string; receipt: SelfEvolutionNativeConfirmationReceipt; senderIdentity: string | number; sessionIdentity: string; expiresAt: number }>();
  const now = options.now ?? Date.now;
  const ttlMs = options.ttlMs ?? DEFAULT_TTL_MS;
  const configured = typeof options.isTrustedSender === "function"
    && typeof options.senderIdentity === "function"
    && typeof options.sessionIdentity === "function"
    && typeof options.resolveAuthoritativePreview === "function"
    && typeof options.showNativeConfirmation === "function"
    && typeof options.authorizationNotConsumed === "symbol"
    && Number.isInteger(ttlMs) && ttlMs > 0 && ttlMs <= MAX_TTL_MS;

  async function request(event: Event, raw: unknown): Promise<ConfirmationResult> {
    if (!configured) return { ok: false, code: "unavailable" };
    if (!options!.isTrustedSender!(event)) return { ok: false, code: "untrusted_sender" };
    if (!validRequest(raw)) return { ok: false, code: "invalid_request" };
    const requested = structuredClone(raw);
    if (inFlight.has(requested.operationId) || issuedOperations.has(requested.operationId)) return { ok: false, code: "busy" };
    inFlight.add(requested.operationId);
    try {
      const trustedSession = options!.sessionIdentity!(event);
      if (typeof trustedSession !== "string" || !ID.test(trustedSession)) return { ok: false, code: "untrusted_sender" };
      const first = await options!.resolveAuthoritativePreview!(event, requested.operationId);
      if (!options!.isTrustedSender!(event)) return { ok: false, code: "untrusted_sender" };
      if (!validPreview(first) || first.sessionId !== trustedSession || !previewMatchesRequest(first, requested)) return { ok: false, code: "binding_mismatch" };
      if (buildSelfEvolutionNativeConfirmationDetail(first) === null) return { ok: false, code: "cancelled" };
      const frozenPreview = structuredClone(first);
      const approvedSnapshot = stableJson(frozenPreview);
      const accepted = await options!.showNativeConfirmation!(event, frozenPreview);
      if (!options!.isTrustedSender!(event)) return { ok: false, code: "untrusted_sender" };
      if (accepted !== true) return { ok: false, code: "cancelled" };
      const finalPreview = await options!.resolveAuthoritativePreview!(event, requested.operationId);
      if (!options!.isTrustedSender!(event)) return { ok: false, code: "untrusted_sender" };
      if (!validPreview(finalPreview) || finalPreview.sessionId !== trustedSession || !previewMatchesRequest(finalPreview, requested)
          || options!.sessionIdentity!(event) !== trustedSession
          || stableJson(finalPreview) !== approvedSnapshot
          || stableJson(frozenPreview) !== approvedSnapshot) return { ok: false, code: "binding_mismatch" };

      const issuedAtMs = now();
      const receipt: SelfEvolutionNativeConfirmationReceipt = Object.freeze({
        format: 1, mode: "self_evolution_native_confirmation_v1", decision: "accepted",
        taskId: requested.taskId, attemptId: requested.attemptId, candidateId: requested.candidateId,
        candidateDigest: requested.candidateDigest, checkpointDigest: requested.checkpointDigest,
        stableStateDigest: requested.stableStateDigest, previewDigest: requested.previewDigest,
        selectedPaths: Object.freeze([...requested.selectedPaths]) as unknown as string[],
        operationId: requested.operationId, nonce: randomUUID(), issuer: "electron-main",
        issuedAt: new Date(issuedAtMs).toISOString(),
      });
      privateReceipts.set(receipt.nonce, { receiptId: randomUUID(), receipt: structuredClone(receipt), senderIdentity: options!.senderIdentity!(event), sessionIdentity: trustedSession, expiresAt: issuedAtMs + ttlMs });
      issuedOperations.add(requested.operationId);
      return { ok: true, receipt };
    } catch { return { ok: false, code: "unavailable" }; }
    finally { inFlight.delete(requested.operationId); }
  }

  /** Synchronous, one-shot callback to pass directly as store.claim(..., {assertAuthorized}). */
  function claimAuthorization(rawReceipt: unknown, rawIntent: unknown, event: Event): boolean | symbol {
    if (!configured || !validReceipt(rawReceipt) || !record(rawIntent)) return options!.authorizationNotConsumed!;
    try { if (!options!.isTrustedSender!(event)) return options!.authorizationNotConsumed!; }
    catch { return options!.authorizationNotConsumed!; }
    const receipt = rawReceipt;
    const intent = rawIntent as unknown as MergeIntentBinding;
    const stored = privateReceipts.get(receipt.nonce);
    let senderIdentity: string | number;
    let sessionIdentity: string;
    try { senderIdentity = options!.senderIdentity!(event); sessionIdentity = options!.sessionIdentity!(event); } catch { return options!.authorizationNotConsumed!; }
    const current = now();
    if (!stored || stored.senderIdentity !== senderIdentity || stored.sessionIdentity !== sessionIdentity || stored.expiresAt <= current || stableJson(stored.receipt) !== stableJson(receipt)
        || !ID.test(stored.receiptId) || !receiptMatchesIntent(receipt, intent)) return options!.authorizationNotConsumed!;
    privateReceipts.delete(receipt.nonce);
    return true;
  }

  return Object.freeze({ request, claimAuthorization });
}
