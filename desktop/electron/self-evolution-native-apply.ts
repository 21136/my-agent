import { createHash, timingSafeEqual } from "node:crypto";
import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import path from "node:path";
import { lstatSync, readFileSync, realpathSync } from "node:fs";

export const SELF_EVOLUTION_NATIVE_APPLY_PATH = "/self-evolution/confirm";
export const SELF_EVOLUTION_NATIVE_APPLY_CONTENT_TYPE = "application/json";
export const SELF_EVOLUTION_NATIVE_URL_ENV = "MY_AGENT_SELF_EVOLUTION_NATIVE_URL";
export const SELF_EVOLUTION_NATIVE_TOKEN_ENV = "MY_AGENT_SELF_EVOLUTION_NATIVE_TOKEN";
type SelectedApplyFile = {
  path: string; action: "add" | "modify" | "delete"; before_sha256: string | null; after_sha256: string | null;
  before_size: number | null; after_size: number | null; before_mode: "100644" | "100755" | null; after_mode: "100644" | "100755" | null;
};
type HostSelectedApplyRequest = {
  schema: "self_evolution_host_selected_apply_request_v1"; session_id: string; task_id: string; attempt_id: string; candidate_id: string;
  baseline_digest: string; candidate_digest: string; operation_id: string; target_root: string; target_head: string;
  target_index_sha256: string; target_worktree_digest: string; files: SelectedApplyFile[]; request_sha256: string;
};
type NativeApplyOptions = {
  token: string; expectedTargetRoot: string; maxBodyBytes?: number; operationTtlMs?: number; now?: () => number;
  childIdentity?: unknown; getChildIdentity?: () => unknown; isChildCurrent: (child: unknown) => boolean;
  getOfficialWindow: () => unknown | null; isOfficialWindowCurrent: (window: unknown) => boolean;
  showNativeConfirmation: (window: unknown, request: HostSelectedApplyRequest, exactBody: Buffer) => Promise<boolean> | boolean;
};
export type SelfEvolutionNativeApplyTransport = { url: string; token: string; address: { address: string; family: string; port: number }; close: () => Promise<void> };
export function validateSelfEvolutionExperimentalOptIn(input: {
  platform: NodeJS.Platform; staticAstEnabled: boolean; nativeApprovalEnabled: boolean;
  packaged: boolean; fixedHost: boolean; testHost: boolean;
}): boolean {
  if (input.nativeApprovalEnabled && !input.staticAstEnabled) throw new TypeError("native selected-apply approval requires the explicit static-AST experimental profile");
  if (!input.staticAstEnabled) return false;
  if (input.platform !== "win32") throw new TypeError("experimental self-evolution is Windows-only");
  if (input.packaged || input.fixedHost || input.testHost) throw new TypeError("experimental self-evolution is unavailable in packaged, fixed, or test hosts");
  return true;
}

export function buildSelfEvolutionNativeApplyEnvironment(base: NodeJS.ProcessEnv, transport?: { url: string; token: string }): NodeJS.ProcessEnv {
  const env = { ...base };
  delete env[SELF_EVOLUTION_NATIVE_URL_ENV];
  delete env[SELF_EVOLUTION_NATIVE_TOKEN_ENV];
  delete env.MY_AGENT_SELF_EVOLUTION_EXPERIMENT_TARGET_ROOT;
  delete env.MY_AGENT_EXPERIMENTAL_SELF_EVOLUTION_STATIC_AST;
  delete env.MY_AGENT_SELF_EVOLUTION_NATIVE_APPROVAL;
  delete env.MY_AGENT_SELF_EVOLUTION_ENROLL_TRUST_ROOT;
  if (transport) {
    if (typeof transport.url !== "string" || !/^http:\/\/127\.0\.0\.1:\d+\/self-evolution\/confirm$/.test(transport.url)
        || typeof transport.token !== "string" || !/^[a-f0-9]{64}$/.test(transport.token)) throw new TypeError("native approval child environment values are invalid");
    env[SELF_EVOLUTION_NATIVE_URL_ENV] = transport.url;
    env[SELF_EVOLUTION_NATIVE_TOKEN_ENV] = transport.token;
  }
  return env;
}
const REQUEST_KEYS = ["schema", "session_id", "task_id", "attempt_id", "candidate_id", "baseline_digest", "candidate_digest", "operation_id", "target_root", "target_head", "target_index_sha256", "target_worktree_digest", "files", "request_sha256"];
const FILE_KEYS = ["path", "action", "before_sha256", "after_sha256", "before_size", "after_size", "before_mode", "after_mode"];
const SHA256 = /^[a-f0-9]{64}$/;
const HEAD = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
const PATH_CONTROL = /[\u0000-\u001f\u007f-\u009f\u202a-\u202e\u2066-\u2069]/u;
const DEFAULT_MAX_BODY_BYTES = 1024 * 1024;
const DEFAULT_OPERATION_TTL_MS = 2 * 60_000;
const MAX_OPERATION_TTL_MS = 10 * 60_000;
const MAX_OPERATION_RECORDS = 512;
const MAX_RESPONSE_BYTES = 60 * 1024;
const DECLINED = (request: HostSelectedApplyRequest): NativeApplyResponse => ({ approved: false, request_sha256: request.request_sha256, operation_id: request.operation_id, selected_paths: request.files.map((file) => file.path) });
const EMPTY_DECLINE: NativeApplyResponse = Object.freeze({ approved: false, request_sha256: "", operation_id: "", selected_paths: [] });
function declinedUnknown(value: unknown): NativeApplyResponse {
  if (!isRecord(value) || typeof value.request_sha256 !== "string" || !SHA256.test(value.request_sha256)
      || typeof value.operation_id !== "string" || !ID.test(value.operation_id) || !Array.isArray(value.files)
      || value.files.length < 1 || value.files.length > 256 || !value.files.every(validFile)) return EMPTY_DECLINE;
  return DECLINED(value as unknown as HostSelectedApplyRequest);
}

function isRecord(value: unknown): value is Record<string, unknown> { return value !== null && typeof value === "object" && !Array.isArray(value); }
type NativeApplyResponse = { approved: boolean; request_sha256: string; operation_id: string; selected_paths: string[] };
function exactKeys(value: unknown, keys: string[]): value is Record<string, unknown> { return isRecord(value) && Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key)); }
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (isRecord(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  if (typeof value === "string") return JSON.stringify(value).replace(/[\u007f-\uffff]/g, (char) => `\\u${char.charCodeAt(0).toString(16).padStart(4, "0")}`);
  return JSON.stringify(value);
}
function hashRequestBody(value: HostSelectedApplyRequest): string {
  const { request_sha256: _ignored, ...body } = value;
  return createHash("sha256").update(canonicalJson(body), "ascii").digest("hex");
}
function normalizeRoot(value: unknown): string | null {
  if (typeof value !== "string" || !value || value.includes("\0")) return null;
  const resolved = path.resolve(value);
  if (!path.isAbsolute(value)) return null;
  return process.platform === "win32" ? resolved.toLocaleLowerCase("en-US") : resolved;
}

function hasReparseAttribute(value: unknown): boolean { return Boolean((((value as { attributes?: number }).attributes) ?? 0) & 0x400); }
function normalizedRealDirectory(value: string): string {
  if (typeof value !== "string" || !path.isAbsolute(value)) throw new TypeError("disposable target path must be absolute");
  const canonical = realpathSync.native(value);
  const info = lstatSync(canonical);
  if (!info.isDirectory() || info.isSymbolicLink() || hasReparseAttribute(info) || realpathSync.native(canonical) !== canonical) throw new TypeError("disposable target path is not a plain canonical directory");
  return process.platform === "win32" ? canonical.toLocaleLowerCase("en-US") : canonical;
}
function rootsOverlap(left: string, right: string): boolean {
  const relative = path.relative(left, right);
  const reverse = path.relative(right, left);
  const within = (value: string) => value === "" || (value !== ".." && !value.startsWith(".." + path.sep) && !path.isAbsolute(value));
  return within(relative) || within(reverse);
}
/** Resolve the owning checkout root, including a worktree's shared stable repository. */
export function resolveSelfEvolutionGitCommonRoot(sourceRoot: string): string {
  const source = realpathSync.native(sourceRoot);
  const marker = path.join(source, ".git");
  const info = lstatSync(marker);
  if (info.isDirectory() && !info.isSymbolicLink() && !hasReparseAttribute(info)) return source;
  if (!info.isFile() || info.isSymbolicLink() || hasReparseAttribute(info)) throw new TypeError("source Git marker is not a plain repository or worktree marker");
  const match = /^gitdir: ([^\r\n]+)\r?\n?$/.exec(readFileSync(marker, "utf8"));
  if (!match || !match[1]) throw new TypeError("source Git worktree marker is malformed");
  let current = realpathSync.native(path.resolve(source, match[1]));
  for (;;) {
    if (path.basename(current).toLocaleLowerCase("en-US") === ".git") return path.dirname(current);
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  throw new TypeError("source Git common directory is unresolved");
}

/** Require a marked, standalone sibling repo disjoint from source and protected runtime roots. */
export function validateSelfEvolutionDisposableTarget({ sourceRoot, targetRoot, excludedRoots = [] }: { sourceRoot: string; targetRoot: string; excludedRoots?: string[] }): string {
  const source = normalizedRealDirectory(sourceRoot);
  const target = normalizedRealDirectory(targetRoot);
  if (source === target || path.dirname(source) !== path.dirname(target)) throw new TypeError("disposable target must be adjacent to, and separate from, the source root");
  for (const value of [source, ...excludedRoots]) {
    const excluded = normalizedRealDirectory(value);
    if (rootsOverlap(target, excluded)) throw new TypeError("disposable target overlaps a protected source, stable, storage, or runtime root");
  }
  const git = path.join(targetRoot, ".git");
  const gitInfo = lstatSync(git);
  if (!gitInfo.isDirectory() || gitInfo.isSymbolicLink() || hasReparseAttribute(gitInfo) || realpathSync.native(git) !== git) throw new TypeError("disposable target must be a standalone Git repository");
  const gitEntries: Array<[string, boolean]> = [["HEAD", false], ["config", false], ["objects", true], ["refs", true]];
  for (const [name, directory] of gitEntries) {
    const item = path.join(git, name);
    const info = lstatSync(item);
    if (info.isSymbolicLink() || hasReparseAttribute(info) || info.isDirectory() !== directory || (!directory && !info.isFile()) || realpathSync.native(item) !== item) throw new TypeError("disposable Git repository metadata is linked or incomplete");
  }
  const markerPath = path.join(targetRoot, ".self-evolution-experimental-target.json");
  const markerInfo = lstatSync(markerPath);
  if (!markerInfo.isFile() || markerInfo.isSymbolicLink() || hasReparseAttribute(markerInfo) || markerInfo.size > 4096 || realpathSync.native(markerPath) !== markerPath) throw new TypeError("disposable target marker is missing or unsafe");
  let marker;
  try { marker = JSON.parse(readFileSync(markerPath, "utf8")); } catch { throw new TypeError("disposable target marker is malformed"); }
  const expected: Record<string, string> = { schema: "self-evolution-experimental-target.v1", purpose: "self_evolution_disposable_repository", repository_root: targetRoot };
  if (!exactKeys(marker, Object.keys(expected)) || Object.keys(expected).some((key) => marker[key] !== expected[key])) throw new TypeError("disposable target marker does not bind this repository root");
  return targetRoot;
}

function validFile(file: unknown): file is SelectedApplyFile {
  if (!exactKeys(file, FILE_KEYS) || typeof file.path !== "string" || !file.path || file.path.length > 512 || PATH_CONTROL.test(file.path)
      || file.path.startsWith("/") || file.path.includes("\\") || file.path.split("/").some((part) => !part || part === "." || part === "..")
      || typeof file.action !== "string" || !["add", "modify", "delete"].includes(file.action)) return false;
  const digest = (value: unknown) => value === null || (typeof value === "string" && SHA256.test(value));
  const mode = (value: unknown) => value === null || value === "100644" || value === "100755";
  const size = (value: unknown) => value === null || (Number.isSafeInteger(value) && (value as number) >= 0);
  if (!digest(file.before_sha256) || !digest(file.after_sha256) || !mode(file.before_mode) || !mode(file.after_mode) || !size(file.before_size) || !size(file.after_size)) return false;
  if (file.action === "add") return file.before_sha256 === null && file.after_sha256 !== null && file.before_mode === null && file.after_mode !== null && file.before_size === null && file.after_size !== null;
  if (file.action === "modify") return file.before_sha256 !== null && file.after_sha256 !== null && file.before_mode !== null && file.after_mode !== null && file.before_size !== null && file.after_size !== null;
  return file.before_sha256 !== null && file.after_sha256 === null && file.before_mode !== null && file.after_mode === null && file.before_size !== null && file.after_size === null;
}
function validateRequest(value: unknown, { expectedTargetRoot }: { expectedTargetRoot: string }): string | null {
  if (!exactKeys(value, REQUEST_KEYS) || value.schema !== "self_evolution_host_selected_apply_request_v1") return "invalid_request";
  if (![value.session_id, value.task_id, value.attempt_id, value.candidate_id, value.operation_id].every((item) => typeof item === "string" && ID.test(item))) return "invalid_request";
  if (![value.baseline_digest, value.candidate_digest, value.target_index_sha256, value.target_worktree_digest, value.request_sha256].every((item) => typeof item === "string" && SHA256.test(item))
      || typeof value.target_head !== "string" || !HEAD.test(value.target_head)) return "invalid_request";
  if (typeof value.target_root !== "string" || value.target_root.length > 512 || PATH_CONTROL.test(value.target_root) || normalizeRoot(value.target_root) === null) return "invalid_request";
  if (normalizeRoot(value.target_root) !== expectedTargetRoot) return "wrong_target";
  if (!Array.isArray(value.files) || value.files.length < 1 || value.files.length > 256 || !value.files.every(validFile)) return "invalid_request";
  const paths = value.files.map((file) => file.path);
  if (new Set(paths).size !== paths.length || [...paths].sort().some((item, index) => item !== paths[index])) return "invalid_request";
  if (hashRequestBody(value as unknown as HostSelectedApplyRequest) !== value.request_sha256) return "digest_mismatch";
  const expectedResponse = DECLINED(value as unknown as HostSelectedApplyRequest);
  if (Buffer.byteLength(JSON.stringify(expectedResponse), "utf8") > MAX_RESPONSE_BYTES) return "response_too_large";
  return null;
}
export function buildSelfEvolutionNativeApplyDetail(value: unknown): string | null {
  if (!isRecord(value) || !Array.isArray(value.files)) return null;
  const request = value as unknown as HostSelectedApplyRequest;
  if (!isRecord(request) || !Array.isArray(request.files) || request.files.length < 1 || request.files.length > 256 || !request.files.every(validFile)) return null;
  const files = request.files.map((file) => `- ${file.path} | ${file.action} | ${file.before_mode ?? "none"} -> ${file.after_mode ?? "none"} | ${file.before_size ?? "none"} -> ${file.after_size ?? "none"} bytes | ${file.before_sha256 ?? "none"} -> ${file.after_sha256 ?? "none"}`).join("\n");
  const detail = [
    `Session: ${request.session_id}`, `Task: ${request.task_id} / Attempt: ${request.attempt_id}`,
    `Candidate: ${request.candidate_id}`, `Operation: ${request.operation_id}`, `Target: ${request.target_root}`,
    `HEAD: ${request.target_head}`, `Index SHA-256: ${request.target_index_sha256}`,
    `Worktree SHA-256: ${request.target_worktree_digest}`, `Baseline SHA-256: ${request.baseline_digest}`,
    `Candidate SHA-256: ${request.candidate_digest}`, `Request SHA-256: ${request.request_sha256}`,
    "", "Exact selected file changes:", files, "",
    "This confirms only the displayed host-selected file application. It does not authorize history merge, activation, release, launch, or writes to another target.",
  ].join("\n");
  return Buffer.byteLength(detail, "utf8") <= 128 * 1024 ? detail : null;
}
function readBounded(req: IncomingMessage, maxBytes: number): Promise<Buffer> {
  return new Promise<Buffer>((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let overflow = false;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > maxBytes) {
        if (!overflow) {
          overflow = true;
          req.pause();
          reject(Object.assign(new Error("body_limit"), { statusCode: 413 }));
        }
        return;
      }
      chunks.push(chunk);
    });
    req.once("end", () => { if (!overflow) resolve(Buffer.concat(chunks)); });
    req.once("error", reject);
  });
}
function writeJson(res: ServerResponse, status: number, value: unknown): void {
  if (res.destroyed || res.writableEnded) return;
  const bytes = Buffer.from(JSON.stringify(value), "utf8");
  try {
    res.writeHead(status, { "Content-Type": "application/json; charset=utf-8", "Content-Length": bytes.length, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", Connection: "close" });
    res.end(bytes);
  } catch { res.destroy(); }
}
function tokenMatches(header: string | string[] | undefined, token: string): boolean {
  if (typeof header !== "string" || !/^Bearer [a-f0-9]{64}$/.test(header)) return false;
  const actual = Buffer.from(header.slice(7), "ascii");
  const expected = Buffer.from(token, "ascii");
  return actual.length === expected.length && timingSafeEqual(actual, expected);
}

/**
 * Main-process-only loopback native approval transport for the HostSelectedApply contract.
 * The caller supplies private child/window identity guards; no renderer channel is involved.
 */
export async function createSelfEvolutionNativeApplyTransport(options: NativeApplyOptions): Promise<SelfEvolutionNativeApplyTransport> {
  const token = options?.token;
  const expectedTargetRoot = normalizeRoot(options?.expectedTargetRoot);
  const maxBodyBytes = options?.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES;
  const operationTtlMs = options?.operationTtlMs ?? DEFAULT_OPERATION_TTL_MS;
  if (typeof token !== "string" || !/^[a-f0-9]{64}$/.test(token) || !expectedTargetRoot
      || typeof options.isChildCurrent !== "function" || (typeof options.childIdentity === "undefined" && typeof options.getChildIdentity !== "function") || typeof options.getOfficialWindow !== "function"
      || typeof options.isOfficialWindowCurrent !== "function" || typeof options.showNativeConfirmation !== "function"
      || !Number.isSafeInteger(maxBodyBytes) || maxBodyBytes < 128 || maxBodyBytes > 1024 * 1024
      || !Number.isSafeInteger(operationTtlMs) || operationTtlMs < 1000 || operationTtlMs > MAX_OPERATION_TTL_MS) {
    throw new TypeError("self-evolution native approval transport configuration is invalid");
  }
  const records = new Map<string, { expiresAt: number }>();
  let closed = false;
  const now = options.now ?? Date.now;
  const server = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    if (closed) return writeJson(res, 503, EMPTY_DECLINE);
    if (req.method !== "POST" || req.url !== SELF_EVOLUTION_NATIVE_APPLY_PATH) return writeJson(res, 404, EMPTY_DECLINE);
    if (!tokenMatches(req.headers.authorization, token)) return writeJson(res, 401, EMPTY_DECLINE);
    if (req.headers["content-type"] !== SELF_EVOLUTION_NATIVE_APPLY_CONTENT_TYPE) return writeJson(res, 415, EMPTY_DECLINE);
    if (req.headers["transfer-encoding"] && req.headers["content-length"]) return writeJson(res, 400, EMPTY_DECLINE);
    if (req.headers["content-length"] && (!/^\d+$/.test(req.headers["content-length"]) || Number(req.headers["content-length"]) > maxBodyBytes)) return writeJson(res, 413, EMPTY_DECLINE);
    let raw: Buffer;
    let parsed: unknown;
    try {
      raw = await readBounded(req, maxBodyBytes);
      parsed = JSON.parse(raw.toString("utf8"));
    } catch (error) {
      return writeJson(res, (error as { statusCode?: number })?.statusCode ?? 400, EMPTY_DECLINE);
    }
    if (!isRecord(parsed)) return writeJson(res, 400, EMPTY_DECLINE);
    const request = parsed as unknown as HostSelectedApplyRequest;
    if (!Buffer.from(canonicalJson(request), "ascii").equals(raw)) return writeJson(res, 400, EMPTY_DECLINE);
    const invalid = validateRequest(request, { expectedTargetRoot });
    if (invalid) return writeJson(res, invalid === "wrong_target" ? 200 : 400,
      invalid === "response_too_large" ? EMPTY_DECLINE : declinedUnknown(request));
    const stamp = now();
    if (records.has(request.operation_id) || records.size >= MAX_OPERATION_RECORDS) return writeJson(res, 200, DECLINED(request));
    const currentChild = typeof options.getChildIdentity === "function" ? options.getChildIdentity() : options.childIdentity;
    if (!options.isChildCurrent(currentChild)) return writeJson(res, 200, DECLINED(request));
    const window = options.getOfficialWindow();
    if (!window || !options.isOfficialWindowCurrent(window)) return writeJson(res, 200, DECLINED(request));
    const snapshot = Buffer.from(raw);
    let clientDisconnected = false;
    res.once("close", () => { if (!res.writableEnded) clientDisconnected = true; });
    const entry: { expiresAt: number } = { expiresAt: stamp + operationTtlMs };
    records.set(request.operation_id, entry);
    let approved = false;
    let approvalTimer: NodeJS.Timeout | undefined;
    try {
      approved = await Promise.race([
        Promise.resolve().then(() => options.showNativeConfirmation(window, structuredClone(request), snapshot)).then((value) => value === true, () => false),
        new Promise<boolean>((resolve) => { approvalTimer = setTimeout(() => resolve(false), operationTtlMs); }),
      ]);
    } catch { approved = false; }
    finally { if (approvalTimer) clearTimeout(approvalTimer); }
    const unchanged = !closed && !clientDisconnected && !res.destroyed && now() < entry.expiresAt && options.isChildCurrent(currentChild) && options.getOfficialWindow() === window && options.isOfficialWindowCurrent(window);
    const sameBytes = Buffer.from(raw).equals(snapshot);
    const accepted = approved && unchanged && sameBytes;
    const response: NativeApplyResponse = { approved: accepted, request_sha256: request.request_sha256, operation_id: request.operation_id, selected_paths: request.files.map((file) => file.path) };
    writeJson(res, 200, response);
  });
  await new Promise<void>((resolve, reject) => {
    const failed = (error: Error) => { server.removeListener("listening", listening); reject(error); };
    const listening = () => { server.removeListener("error", failed); resolve(); };
    server.once("error", failed);
    server.once("listening", listening);
    server.listen(0, "127.0.0.1");
  });
  const address = server.address();
  if (!address || typeof address === "string" || address.address !== "127.0.0.1") {
    server.close(); throw new Error("loopback listener failed to bind the IPv4 loopback interface");
  }
  let closePromise: Promise<void> | null = null;
  return Object.freeze({
    url: `http://127.0.0.1:${address.port}${SELF_EVOLUTION_NATIVE_APPLY_PATH}`,
    token,
    address,
    close: () => {
      if (closePromise) return closePromise;
      closed = true; records.clear();
      closePromise = new Promise((resolve, reject) => {
        server.close((error) => error ? reject(error) : resolve());
        server.closeAllConnections?.();
      });
      return closePromise;
    },
  });
}
