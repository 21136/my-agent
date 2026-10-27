import type { ManagedCheckpointRequest, ManagedSourceCheckpoint, ManagedSourceCheckpointReceipt } from "../launcher/managed-source-checkpoint.mjs";

type Roots = { stableRoot: string; sandboxRoot: string };
type Request = ManagedCheckpointRequest;
type Pack = ManagedSourceCheckpoint;
type Receipt = ManagedSourceCheckpointReceipt;
type Proposal = Record<string, any>;
type Report = Record<string, any>;
type Result = { ok: true; receipt: Receipt } | { ok: false; message: string };
type Options<Caller> = {
  isTrusted: (caller: Caller) => boolean;
  isFixture?: boolean;
  validateRequest: (value: unknown) => Request;
  loadBackend?: (caller: Caller) => Promise<void>;
  resolveRoots: (caller: Caller) => Promise<Roots>;
  inspect: (roots: Roots, caller: Caller) => Promise<Report>;
  propose: (input: { review: Report; request: { format: 1; mode: "managed_source_selection_request_v1"; reviewDigest: string; selectedPaths: string[] } }) => Proposal;
  capture: (input: { review: Report; request: Request; stableRoot: string; sandboxRoot: string }, caller: Caller) => Pack;
  confirm: (pack: Pack, caller: Caller) => Promise<boolean>;
  persist: (input: { sandboxRoot: string; checkpoint: Pack }) => Receipt;
};
const DENIED = "仅允许可信工作台主页面创建源码检查点。";
const ERROR = "源码检查点暂无法创建，请重新读取审查报告后重试。";
const CANCELLED = "Checkpoint was not created.";
const HASH = /^[a-f0-9]{64}$/;
const isRecord = (value: unknown): value is Record<string, any> => !!value && typeof value === "object" && !Array.isArray(value);
function samePaths(a: unknown, b: string[]): boolean { return Array.isArray(a) && JSON.stringify(a) === JSON.stringify(b); }
function validateReview(review: Report, request: Request): void {
  if (!isRecord(review) || review.review_digest !== request.reviewDigest || !Array.isArray(review.blockers)) throw new TypeError("review binding invalid");
}
function validateProposal(proposal: Proposal, request: Request): void {
  if (!isRecord(proposal) || proposal.mode !== "managed_source_selection_proposal_v1"
      || proposal.reviewDigest !== request.reviewDigest || proposal.proposal_digest !== request.proposalDigest
      || !HASH.test(proposal.proposal_digest) || !samePaths(proposal.selectedPaths, request.selectedPaths)
      || proposal.execution !== false || proposal.readyForMerge !== false || proposal.readyForLaunch !== false
      || proposal.checkpointCreated !== false || proposal.nativeConsent !== false || !Array.isArray(proposal.blockers)) {
    throw new TypeError("proposal binding invalid");
  }
}
function synchronousPack(value: unknown): Pack {
  if (value !== null && (typeof value === "object" || typeof value === "function") && typeof (value as { then?: unknown }).then === "function") throw new TypeError("checkpoint capture must be synchronous");
  return value as Pack;
}
function validatePack(pack: Pack, review: Report, request: Request): void {
  if (!isRecord(pack) || pack.format !== 1 || pack.mode !== "managed_selected_source_checkpoint_v1"
      || pack.review?.review_digest !== review.review_digest || pack.request?.reviewDigest !== request.reviewDigest
      || pack.request?.proposalDigest !== request.proposalDigest || !samePaths(pack.request?.selectedPaths, request.selectedPaths)
      || pack.request?.mode !== request.mode || !Number.isSafeInteger(pack.rawBytes) || pack.rawBytes < 0
      || pack.execution !== false || pack.readyForMerge !== false || pack.readyForLaunch !== false || pack.nativeConsent !== false
      || !HASH.test(pack.checkpointDigest) || !Array.isArray(pack.files) || pack.files.length !== request.selectedPaths.length
      || !samePaths(pack.files.map(file => file?.path), request.selectedPaths)) throw new TypeError("checkpoint pack binding invalid");
}
function validateReceipt(receipt: Receipt, pack: Pack): void {
  const keys = ["format", "mode", "reviewDigest", "proposalDigest", "checkpointDigest", "selectedPaths", "rawBytes", "storedBytes", "checkpointCreated", "execution", "readyForMerge", "readyForLaunch", "nativeConsent"];
  if (!isRecord(receipt) || Object.keys(receipt).length !== keys.length || keys.some(key => !Object.hasOwn(receipt, key))
      || receipt.format !== 1 || receipt.mode !== "managed_source_checkpoint_receipt_v1"
      || receipt.reviewDigest !== pack.request.reviewDigest || receipt.proposalDigest !== pack.request.proposalDigest
      || receipt.checkpointDigest !== pack.checkpointDigest || !samePaths(receipt.selectedPaths, pack.request.selectedPaths)
      || receipt.rawBytes !== pack.rawBytes || !Number.isSafeInteger(receipt.storedBytes) || receipt.storedBytes < 0
      || receipt.checkpointCreated !== true || receipt.execution !== false || receipt.readyForMerge !== false
      || receipt.readyForLaunch !== false || receipt.nativeConsent !== false) throw new TypeError("checkpoint receipt binding invalid");
}

/** Native checkpointing is a host-only byte-capture confirmation, never adoption or merge authority. */
export function createManagedCheckpointHandler<Caller>(options: Options<Caller>) {
  let busy = false;
  return async (caller: Caller, ...args: unknown[]): Promise<Result> => {
    if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
    if (options.isFixture) return { ok: false, message: "固定或测试 Host 不支持管理源码检查点。" };
    if (args.length !== 1) return { ok: false, message: "检查点接口仅接受一个路径选择请求。" };
    let request: Request;
    try { request = structuredClone(options.validateRequest(args[0])); }
    catch { return { ok: false, message: ERROR }; }
    if (busy) return { ok: false, message: "源码检查点正在处理中，请稍后重试。" };
    busy = true;
    try {
      await options.loadBackend?.(caller);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      const roots = await options.resolveRoots(caller);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      const review = await options.inspect(roots, caller);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      validateReview(review, request);
      const proposal = options.propose({ review, request: { format: 1, mode: "managed_source_selection_request_v1", reviewDigest: request.reviewDigest, selectedPaths: [...request.selectedPaths] } });
      validateProposal(proposal, request);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      const captured = synchronousPack(options.capture({ review, request, ...roots }, caller));
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      validatePack(captured, review, request);
      const confirmed = await options.confirm(captured, caller);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      if (confirmed !== true) return { ok: false, message: CANCELLED };
      const finalReview = await options.inspect(roots, caller);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      validateReview(finalReview, request);
      const finalProposal = options.propose({ review: finalReview, request: { format: 1, mode: "managed_source_selection_request_v1", reviewDigest: request.reviewDigest, selectedPaths: [...request.selectedPaths] } });
      validateProposal(finalProposal, request);
      if (finalProposal.proposal_digest !== proposal.proposal_digest) return { ok: false, message: "源码范围已变化，请重新审查后重试。" };
      const finalPack = synchronousPack(options.capture({ review: finalReview, request, ...roots }, caller));
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      validatePack(finalPack, finalReview, request);
      if (finalReview.review_digest !== review.review_digest || finalProposal.proposal_digest !== proposal.proposal_digest
          || finalPack.checkpointDigest !== captured.checkpointDigest || finalPack.rawBytes !== captured.rawBytes) {
        return { ok: false, message: "源码或检查点内容已变化，未创建检查点。" };
      }
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      // Keep this final trust check adjacent to the synchronous writer: no async gap is permitted here.
      const receipt = options.persist({ sandboxRoot: roots.sandboxRoot, checkpoint: finalPack });
      validateReceipt(receipt, finalPack);
      return { ok: true, receipt: structuredClone(receipt) };
    } catch { return { ok: false, message: ERROR }; }
    finally { busy = false; }
  };
}
