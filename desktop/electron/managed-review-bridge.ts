import path from "node:path";

export type ManagedReviewReport = Record<string, unknown>;
export type ManagedReviewResult = { ok: true; report: ManagedReviewReport } | { ok: false; message: string };
type Roots = { stableRoot: string; sandboxRoot: string };
type IdentityInspector = (roots: Roots) => Promise<any> | any;
type HandlerOptions = {
  isTrusted: () => boolean;
  hasArgs: (args: unknown[]) => boolean;
  isFixture?: boolean;
  resolveRoots: () => Promise<Roots>;
  inspect: (roots: Roots) => Promise<ManagedReviewReport>;
};

const ERROR = "源码审查暂不可用，请检查受支持的工作区布局。";
function key(value: string): string { return path.resolve(value).replaceAll("\\", "/").replace(/\/$/, "").toLocaleLowerCase("en-US"); }

export async function resolveManagedReviewRoots(codeRoot: string, inspectIdentity: IdentityInspector): Promise<Roots> {
  const resolved = path.resolve(codeRoot);
  const normalized = resolved.replaceAll("\\", "/").replace(/\/$/, "");
  const suffix = "/.worktrees/sandbox";
  let stableRoot: string;
  let sandboxRoot: string;
  if (normalized.toLocaleLowerCase("en-US").endsWith(suffix)) {
    sandboxRoot = resolved;
    stableRoot = path.resolve(resolved, "..", "..");
  } else {
    stableRoot = resolved;
    sandboxRoot = path.resolve(resolved, ".worktrees", "sandbox");
  }
  if (key(stableRoot) === key(sandboxRoot)) throw new Error("managed review roots ambiguous");
  const roots = { stableRoot, sandboxRoot };
  const identity = await inspectIdentity(roots);
  const reportedStable = identity?.stable?.identity?.worktreeRoot;
  const reportedSandbox = identity?.sandbox?.identity?.worktreeRoot;
  if (typeof reportedStable !== "string" || typeof reportedSandbox !== "string"
      || key(reportedStable) !== key(stableRoot) || key(reportedSandbox) !== key(sandboxRoot)) {
    throw new Error("managed review worktree identity mismatch");
  }
  return roots;
}

export function createManagedReviewHandler(options: HandlerOptions): (...args: unknown[]) => Promise<ManagedReviewResult> {
  let busy = false;
  return async (...args) => {
    // Caller validation deliberately precedes all path and inspector access.
    if (!options.isTrusted()) return { ok: false, message: "仅允许可信工作台主页面查看源码审查报告。" };
    if (options.hasArgs(args)) return { ok: false, message: "审查接口不接受渲染器参数。" };
    if (options.isFixture) return { ok: false, message: "固定或测试 Host 不支持管理源码审查。" };
    if (busy) return { ok: false, message: "源码审查正在进行，请稍后重试。" };
    busy = true;
    try {
      const roots = await options.resolveRoots();
      return { ok: true, report: await options.inspect(roots) };
    } catch {
      return { ok: false, message: ERROR };
    } finally { busy = false; }
  };
}
