import path from "node:path";

const key = (value) => path.resolve(value).replaceAll("\\", "/").replace(/\/$/, "").toLocaleLowerCase("en-US");

export async function resolveManagedReviewRoots(codeRoot, inspectIdentity) {
  const resolved = path.resolve(codeRoot);
  const normalized = resolved.replaceAll("\\", "/").replace(/\/$/, "");
  const suffix = "/.worktrees/sandbox";
  let stableRoot;
  let sandboxRoot;
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
