import { createHash } from "node:crypto";
import { validateManagedStablePreservation as validateStablePreservation } from "../launcher/managed-stable-preservation.mjs";
import type { ManagedSelectedMergePlan } from "../launcher/managed-selected-merge.mjs";
type Request = { format: 1; mode: "managed_selected_merge_request_v1"; checkpointDigest: string };
type Roots = { stableRoot: string; sandboxRoot: string };
type Checkpoint = Record<string, any>;
type MergePlan = ManagedSelectedMergePlan;
type SafeReceipt = {
  format: 1; mode: "managed_selected_merge_receipt_v1"; status: "source_merge_verified";
  planDigest: string; checkpointDigest: string; selectedPaths: string[]; stableHeadBefore: string;
  stableHeadAfter: string; mergedTree: string; sourceMergeExecuted: true; sourceHistoryMerged: false;
  readyForLaunch: false; receiptDigest: string;
};
type Failure = { ok: false; message: string; code?: string; paths?: string[]; maintenanceRequired?: true };
type Result = { ok: true; receipt: SafeReceipt } | Failure;
type PrepareInput<Caller> = { caller: Caller; request: Request; roots: Roots; checkpoint: Checkpoint };
type Options<Caller> = {
  isTrusted: (caller: Caller) => boolean;
  isFixture?: boolean;
  validateRequest: (value: unknown) => Request;
  loadBackend?: (caller: Caller) => void | Promise<void>;
  resolveRoots: (caller: Caller) => Roots | Promise<Roots>;
  /** Read is deliberately synchronous: the post-confirm read, prepare, trust check, and execute have no async gap. */
  readCheckpoint: (caller: Caller, request: Request, roots: Roots) => Checkpoint;
  prepare: (input: PrepareInput<Caller>) => MergePlan;
  /** Must display selected paths/files/hashes/modes, both root bindings and heads/indexes, preservation scope, and explicitly exclude history, unrelated changes, activation, and model calls. */
  confirm: (input: { caller: Caller; request: Request; plan: MergePlan }) => boolean | Promise<boolean>;
  /** Synchronous backend transaction. Receives the exact final re-read checkpoint; invocation uncertainty is maintenance-required, never retry-safe. */
  execute: (input: { caller: Caller; request: Request; plan: MergePlan; roots: Roots; finalCheckpoint: Checkpoint }) => unknown;
};

const DENIED = "仅允许可信工作台主页面执行管理源码合并。";
const ERROR = "管理源码合并无法安全完成，请检查来源状态后再操作。";
const CANCELLED = "Merge was not performed.";
// Host-process lifetime, shared across handler instances. Never evict consumed identities:
// only the backend's durable recovery path can resolve an unknown outcome after restart.
const executionStates = new Map<string, "consumed" | "maintenance" | "completed">();
function maintenanceFailure(): Failure {
  return { ok: false, maintenanceRequired: true, message: "源码合并结果未能验证，可能需要维护处理；请勿重试或启动候选版本。" };
}
function consumedRefusal(checkpointDigest: string): Failure | null {
  const state = executionStates.get(checkpointDigest);
  if (state === undefined) return null;
  if (state === "completed") return { ok: false, code: "selected_merge_already_completed", message: "此检查点已完成源码合并，禁止重复执行；这不是当前源码状态的重新验证。" };
  return maintenanceFailure();
}
const HASH = /^[a-f0-9]{64}$/;
const OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const SAFE_CODE = /^[a-z][a-z0-9_]{0,63}$/;
const canonical = (value: any): string => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : value && typeof value === "object" ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const sha256 = (value: unknown): string => createHash("sha256").update(canonical(value)).digest("hex");
const isRecord = (value: unknown): value is Record<string, any> => !!value && typeof value === "object" && !Array.isArray(value);
const thenable = (value: unknown): boolean => value !== null && (typeof value === "object" || typeof value === "function") && typeof (value as { then?: unknown }).then === "function";
function sync<T>(value: T, label: string): T {
  if (thenable(value)) throw new TypeError(`${label} must be synchronous`);
  return value;
}
function selectedPaths(checkpoint: Checkpoint): string[] {
  const paths = checkpoint.request?.selectedPaths ?? checkpoint.selectedPaths;
  if (!Array.isArray(paths) || paths.length === 0 || paths.some(path => typeof path !== "string" || !path || path.startsWith("/") || path.includes("\\") || path.split("/").some((part: string) => !part || part === "." || part === ".."))) throw new TypeError("checkpoint selected paths invalid");
  if (new Set(paths).size !== paths.length) throw new TypeError("duplicate selected paths");
  return paths;
}
function validateCheckpoint(value: unknown, request: Request): asserts value is Checkpoint {
  if (!isRecord(value) || value.format !== 1 || value.mode !== "managed_selected_source_checkpoint_v1" || value.checkpointDigest !== request.checkpointDigest || !HASH.test(value.checkpointDigest)) throw new TypeError("checkpoint binding invalid");
  selectedPaths(value);
}
function validBinding(value: unknown): boolean {
  return isRecord(value) && typeof value.worktreeRoot === "string" && !!value.worktreeRoot
    && typeof value.gitDir === "string" && !!value.gitDir && typeof value.commonDir === "string" && !!value.commonDir
    && typeof value.branch === "string" && !!value.branch && OID.test(value.head) && HASH.test(value.rawIndexSha256);
}
function validatePlan(value: unknown, checkpoint: Checkpoint, request: Request): asserts value is MergePlan {
  const paths = selectedPaths(checkpoint);
  if (!isRecord(value) || value.format !== 1 || value.mode !== "managed_selected_merge_plan_v1"
      || !HASH.test(value.planDigest) || value.checkpointDigest !== request.checkpointDigest
      || !Array.isArray(value.selectedPaths) || JSON.stringify(value.selectedPaths) !== JSON.stringify(paths)
      || !Array.isArray(value.files) || JSON.stringify(value.files.map((file: any) => file?.path)) !== JSON.stringify(paths)
      || value.files.some((file: any) => !isRecord(file) || typeof file.action !== "string" || !file.action || !HASH.test(file.rawSha256)
        || typeof file.mode !== "string" || !/^(?:100644|100755)$/.test(file.mode)
        || !(file.expectedBaseOid === null || OID.test(file.expectedBaseOid))
        || !(file.beforeSha256 === null || HASH.test(file.beforeSha256))
        || !(file.expectedBaseMode === null || /^(?:100644|100755)$/.test(file.expectedBaseMode)))
      || !validBinding(value.stable) || !validBinding(value.sandbox)
      || !OID.test(value.expectedMergedTree) || !OID.test(value.expectedCommit) || value.expectedMergedTree.length !== value.expectedCommit.length || !HASH.test(value.preservationDigest)
      || value.sourceHistoryMerged !== false || !Array.isArray(value.blockers) || value.execution !== false || value.readyForLaunch !== false
      || value.planDigest !== sha256(Object.fromEntries(Object.entries(value).filter(([key]) => key !== "planDigest")))) throw new TypeError("merge plan binding invalid");
  validateStablePreservation(value as MergePlan);
}
function validateReceipt(value: unknown, plan: MergePlan): asserts value is SafeReceipt {
  const keys = ["format", "mode", "status", "planDigest", "checkpointDigest", "selectedPaths", "stableHeadBefore", "stableHeadAfter", "mergedTree", "sourceMergeExecuted", "sourceHistoryMerged", "readyForLaunch", "receiptDigest"];
  if (!isRecord(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))
      || value.format !== 1 || value.mode !== "managed_selected_merge_receipt_v1" || value.status !== "source_merge_verified"
      || value.planDigest !== plan.planDigest || value.checkpointDigest !== plan.checkpointDigest
      || JSON.stringify(value.selectedPaths) !== JSON.stringify(plan.selectedPaths) || value.stableHeadBefore !== plan.stable.head
      || value.stableHeadAfter !== plan.expectedCommit || value.mergedTree !== plan.expectedMergedTree
      || value.sourceMergeExecuted !== true || value.sourceHistoryMerged !== false || value.readyForLaunch !== false
      || !HASH.test(value.receiptDigest)
      || value.receiptDigest !== sha256(Object.fromEntries(Object.entries(value).filter(([key]) => key !== "receiptDigest")))) throw new TypeError("merge receipt binding invalid");
}
function safeRefusal(error: unknown): Failure | null {
  if (!isRecord(error) || typeof error.code !== "string" || !SAFE_CODE.test(error.code) || !Array.isArray(error.paths)) return null;
  const rootFiles = new Set(["AGENTS.md", ".gitignore", "start-desktop.bat", "init-sandbox.bat", "check-stable-before-sandbox-merge.bat", "sync-sandbox-worktree.bat"]);
  const prefixes = ["agent-core/", "desktop/", "docs/", "protocol/", "scripts/", "evolve/tools/", "evolve/skills/", "evolve/tool-catalog/"];
  const never = new Set([".git", ".venv", "venv", "node_modules", "data", "artifacts", "dist", "dist-electron", "__pycache__", "profile", "profiles", "user-data", "workspace", "workspaces", ".managed-runtime"]);
  const secret = /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i;
  const paths = error.paths.filter((path: unknown): path is string => {
    if (typeof path !== "string" || path.length > 512 || /[\u0000-\u001f\u007f<>:"|?*]/.test(path) || path.startsWith("/") || path.includes("\\")) return false;
    if (!rootFiles.has(path) && !prefixes.some(prefix => path.startsWith(prefix))) return false;
    const parts = path.split("/");
    return parts.every(part => !!part && part !== "." && part !== ".." && !/[. ]$/.test(part) && !never.has(part.toLowerCase()) && !secret.test(part) && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part));
  });
  return { ok: false, code: error.code, paths, message: `源码范围被拒绝（${error.code}）${paths.length ? `：${paths.join("、")}` : "。"}` };
}
function prepare<Caller>(options: Options<Caller>, input: PrepareInput<Caller>, checkpoint: Checkpoint): MergePlan | Failure {
  try {
    const plan = sync(options.prepare({ ...input, checkpoint }), "prepare");
    validatePlan(plan, checkpoint, input.request);
    return plan;
  } catch (error) { return safeRefusal(error) ?? { ok: false, message: ERROR }; }
}

/** Performs only the explicitly selected source merge; this bridge never activates candidates or invokes models. */
export function createManagedSelectedMergeHandler<Caller>(options: Options<Caller>) {
  let busy = false;
  return async (caller: Caller, ...args: unknown[]): Promise<Result> => {
    if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
    if (options.isFixture) return { ok: false, message: "固定或测试 Host 不支持管理源码合并。" };
    if (args.length !== 1) return { ok: false, message: "管理源码合并接口仅接受一个检查点摘要请求。" };
    let request: Request;
    try {
      const value = options.validateRequest(args[0]);
      if (!isRecord(value) || Object.keys(value).length !== 3 || value.format !== 1 || value.mode !== "managed_selected_merge_request_v1" || !HASH.test(value.checkpointDigest)) throw new TypeError("request invalid");
      request = structuredClone(value);
    } catch { return { ok: false, message: ERROR }; }
    const checkpointDigest = request.checkpointDigest;
    const entryRefusal = consumedRefusal(checkpointDigest);
    if (entryRefusal) return entryRefusal;
    if (busy) return { ok: false, message: "管理源码合并正在处理中，请稍后重试。" };
    busy = true;
    let executeStarted = false;
    try {
      await options.loadBackend?.(caller);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      const roots = await options.resolveRoots(caller);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      const firstCheckpoint = sync(options.readCheckpoint(caller, request, roots), "readCheckpoint");
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      validateCheckpoint(firstCheckpoint, request);
      const firstPlan = prepare(options, { caller, request, roots, checkpoint: firstCheckpoint }, firstCheckpoint);
      if ("ok" in firstPlan) return firstPlan;
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      const confirmed = await options.confirm({ caller, request, plan: structuredClone(firstPlan) });
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      if (confirmed !== true) return { ok: false, message: CANCELLED };
      // From this final synchronous read through execute, there is no await or event-loop yield.
      const finalCheckpoint = sync(options.readCheckpoint(caller, request, roots), "readCheckpoint");
      validateCheckpoint(finalCheckpoint, request);
      const finalPlan = prepare(options, { caller, request, roots, checkpoint: finalCheckpoint }, finalCheckpoint);
      if ("ok" in finalPlan) return finalPlan;
      if (finalPlan.planDigest !== firstPlan.planDigest || finalPlan.checkpointDigest !== firstPlan.checkpointDigest
          || JSON.stringify(finalPlan.selectedPaths) !== JSON.stringify(firstPlan.selectedPaths)) return { ok: false, message: "检查点或已选源码计划已变化，请重新审查后重试。" };
      // Another handler may have consumed this identity while this one awaited confirmation.
      const finalRefusal = consumedRefusal(checkpointDigest);
      if (finalRefusal) return finalRefusal;
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      executionStates.set(checkpointDigest, "consumed");
      executeStarted = true;
      const rawReceipt = sync(options.execute({ caller, request, plan: finalPlan, roots, finalCheckpoint }), "execute");
      validateReceipt(rawReceipt, finalPlan);
      const receipt = structuredClone(rawReceipt);
      executionStates.set(checkpointDigest, "completed");
      return { ok: true, receipt };
    } catch {
      if (executeStarted) {
        executionStates.set(checkpointDigest, "maintenance");
        return maintenanceFailure();
      }
      return { ok: false, message: ERROR };
    } finally { busy = false; }
  };
}


