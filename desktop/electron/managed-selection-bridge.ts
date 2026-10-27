/** This read-only boundary creates proposals; it never creates checkpoints or executes an adoption. */
type Roots = { stableRoot: string; sandboxRoot: string };
type Request = { reviewDigest: string };
type Report = Record<string, unknown>;
export type ManagedSelectionResult = { ok: true; proposal: Report } | { ok: false; message: string };
type Options<Caller, Selection extends Request> = {
  isTrusted: (caller: Caller) => boolean;
  isFixture?: boolean;
  validateRequest: (value: unknown) => Selection;
  resolveRoots: () => Promise<Roots>;
  inspect: (roots: Roots) => Promise<Report>;
  propose: (input: { review: Report; request: Selection }) => Report;
};

const DENIED = '仅允许可信工作台主页面核验所选源码范围。';
const ERROR = '所选范围暂无法核验，请重新读取源码审查后重试。';

export function createManagedSelectionHandler<Caller, Selection extends Request>(options: Options<Caller, Selection>) {
  let busy = false;
  return async (caller: Caller, ...args: unknown[]): Promise<ManagedSelectionResult> => {
    if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
    if (options.isFixture) return { ok: false, message: '固定或测试 Host 不支持管理源码范围提案。' };
    if (args.length !== 1) return { ok: false, message: '范围核验仅接受一个路径选择请求。' };
    let request: Selection;
    try {
      // No roots, hashes, report or confirmation from the renderer can reach source resolution.
      request = structuredClone(options.validateRequest(args[0]));
    } catch { return { ok: false, message: ERROR }; }
    if (busy) return { ok: false, message: '源码范围正在核验，请稍后重试。' };
    busy = true;
    try {
      const roots = await options.resolveRoots();
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      const review = await options.inspect(roots);
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      if (review.review_digest !== request.reviewDigest) return { ok: false, message: '源码已变化，请重新读取审查报告后选择范围。' };
      const proposal = options.propose({ review, request });
      if (!options.isTrusted(caller)) return { ok: false, message: DENIED };
      return { ok: true, proposal };
    } catch { return { ok: false, message: ERROR }; }
    finally { busy = false; }
  };
}
