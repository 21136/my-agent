import { existsSync } from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const SANDBOX_BRANCH = "experiment/sandbox";
const PRODUCT_PREFIXES = ["agent-core/", "desktop/", "evolve/tools/", "evolve/skills/"];
/** `git stash` / `git add` path args (no trailing slash). */
const PRODUCT_PATH_ARGS = ["agent-core", "desktop", "evolve/tools", "evolve/skills"];

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const repoRoot = path.resolve(desktopDir, "..");

export function resolveSandboxRoot(): string {
  const raw = (process.env.MY_AGENT_SANDBOX_ROOT || "").trim();
  if (raw) return path.resolve(raw);
  return path.join(repoRoot, ".worktrees", "sandbox");
}

export type SandboxAdoptStatus = {
  repoRoot: string;
  sandboxRoot: string;
  sandboxActive: boolean;
  currentBranch: string;
  sandboxBranch: string;
  commitsAhead: number | null;
  /** Commits on stable branch not yet in experiment/sandbox (合入后常见，需同步 worktree). */
  stableAheadOfSandbox: number | null;
  stableDirtyProduct: string[];
  sandboxDirtyProduct: string[];
  /** Human-only issues (missing branch, nothing to merge, …). */
  fatalBlockers: string[];
  needsSandboxCommit: boolean;
  needsStableStash: boolean;
  needsSandboxSync: boolean;
  readyToMerge: boolean;
  recentCommits: string[];
};

export type SandboxAdoptPrepareOptions = {
  /** Commit dirty product paths in sandbox worktree before merge. */
  autoCommitSandbox?: boolean;
  sandboxCommitMessage?: string;
  /** Stash dirty product paths on stable worktree before merge. */
  autoStashStable?: boolean;
};

export type SandboxAdoptResult = {
  ok: boolean;
  message: string;
  detail?: string;
  steps?: string[];
};

function runGit(cwd: string, args: string[]): Promise<{ code: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    const child = spawn("git", args, { cwd, windowsHide: true });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => {
      stdout += String(chunk);
    });
    child.stderr?.on("data", (chunk) => {
      stderr += String(chunk);
    });
    child.on("close", (code) => resolve({ code: code ?? 1, stdout, stderr }));
    child.on("error", (err) => resolve({ code: 1, stdout, stderr: err.message }));
  });
}

async function branchExists(name: string): Promise<boolean> {
  const { code } = await runGit(repoRoot, ["show-ref", "--verify", "--quiet", `refs/heads/${name}`]);
  return code === 0;
}

async function porcelainProduct(cwd: string): Promise<string[]> {
  const lines: string[] = [];
  for (const prefix of PRODUCT_PREFIXES) {
    const { stdout } = await runGit(cwd, ["status", "--porcelain", "--", prefix]);
    const trimmed = stdout.trim();
    if (trimmed) lines.push(...trimmed.split(/\r?\n/).filter(Boolean));
  }
  return lines;
}

export async function getSandboxAdoptStatus(): Promise<SandboxAdoptStatus> {
  const sandboxRoot = resolveSandboxRoot();
  const sandboxDesktop = path.join(sandboxRoot, "desktop");
  const sandboxActive =
    existsSync(path.join(sandboxDesktop, "package.json")) && existsSync(path.join(sandboxRoot, "agent-core"));

  const branchRes = await runGit(repoRoot, ["rev-parse", "--abbrev-ref", "HEAD"]);
  const currentBranch = branchRes.code === 0 ? branchRes.stdout.trim() : "?";

  const fatalBlockers: string[] = [];
  if (!existsSync(path.join(repoRoot, ".git"))) {
    fatalBlockers.push("不是 Git 仓库，无法合入。");
  }
  if (!(await branchExists(SANDBOX_BRANCH))) {
    fatalBlockers.push(`没有分支 ${SANDBOX_BRANCH}，请先运行 init-sandbox.bat。`);
  }
  if (!sandboxActive) {
    fatalBlockers.push("Sandbox 未就绪，请先 init-sandbox.bat。");
  }

  const stableDirtyProduct = await porcelainProduct(repoRoot);

  let sandboxDirtyProduct: string[] = [];
  if (existsSync(sandboxRoot)) {
    sandboxDirtyProduct = await porcelainProduct(sandboxRoot);
  }

  const needsStableStash = stableDirtyProduct.length > 0;
  const needsSandboxCommit = sandboxDirtyProduct.length > 0;

  let commitsAhead: number | null = null;
  let stableAheadOfSandbox: number | null = null;
  let recentCommits: string[] = [];
  if ((await branchExists(SANDBOX_BRANCH)) && currentBranch !== "?") {
    const countRes = await runGit(repoRoot, ["rev-list", "--count", `${currentBranch}..${SANDBOX_BRANCH}`]);
    if (countRes.code === 0) {
      const n = Number.parseInt(countRes.stdout.trim(), 10);
      commitsAhead = Number.isFinite(n) ? n : null;
    }
    const behindRes = await runGit(repoRoot, ["rev-list", "--count", `${SANDBOX_BRANCH}..${currentBranch}`]);
    if (behindRes.code === 0) {
      const n = Number.parseInt(behindRes.stdout.trim(), 10);
      stableAheadOfSandbox = Number.isFinite(n) ? n : null;
    }
    const logRes = await runGit(repoRoot, ["log", "--oneline", "-8", `${currentBranch}..${SANDBOX_BRANCH}`]);
    if (logRes.code === 0 && logRes.stdout.trim()) {
      recentCommits = logRes.stdout.trim().split(/\r?\n/).filter(Boolean);
    }
  }

  const needsSandboxSync = (stableAheadOfSandbox ?? 0) > 0;

  if ((commitsAhead ?? 0) === 0 && !needsSandboxCommit && !needsSandboxSync) {
    fatalBlockers.push("没有可合入的实验提交；先在 sandbox 里改代码并提交。");
  }

  const readyToMerge =
    fatalBlockers.length === 0 && !needsStableStash && !needsSandboxCommit && (commitsAhead ?? 0) > 0;

  return {
    repoRoot,
    sandboxRoot,
    sandboxActive,
    currentBranch,
    sandboxBranch: SANDBOX_BRANCH,
    commitsAhead,
    stableAheadOfSandbox,
    stableDirtyProduct,
    sandboxDirtyProduct,
    fatalBlockers,
    needsSandboxCommit,
    needsStableStash,
    needsSandboxSync,
    readyToMerge,
    recentCommits,
  };
}

/** After stable merges experiment/sandbox, bring the sandbox worktree branch up to date with stable. */
export async function syncSandboxWorktreeWithStable(): Promise<SandboxAdoptResult> {
  const status = await getSandboxAdoptStatus();
  if (status.fatalBlockers.length > 0 && !status.sandboxActive) {
    return { ok: false, message: status.fatalBlockers[0] };
  }
  if (!status.needsSandboxSync) {
    return { ok: true, message: "Sandbox 已与当前稳定分支同步，无需操作。" };
  }
  if (status.sandboxDirtyProduct.length > 0) {
    return {
      ok: false,
      message: "Sandbox 有未提交改动，请先 commit 或丢弃后再同步。",
      detail: status.sandboxDirtyProduct.slice(0, 12).join("\n"),
    };
  }

  const sandboxRoot = resolveSandboxRoot();
  const branchErr = await assertSandboxWorktreeBranch(sandboxRoot);
  if (branchErr) return branchErr;
  const mergeRes = await runGit(sandboxRoot, [
    "merge",
    status.currentBranch,
    "-X",
    "theirs",
    "--no-edit",
    "-m",
    `chore(sandbox): sync with ${status.currentBranch} after stable merge`,
  ]);
  if (mergeRes.code !== 0) {
    return {
      ok: false,
      message: "同步 sandbox 失败（可能有冲突，请在 .worktrees/sandbox 内手动解决）。",
      detail: (mergeRes.stderr || mergeRes.stdout).trim(),
    };
  }
  return {
    ok: true,
    message: `已将 ${status.currentBranch} 合入 sandbox 工作区（${SANDBOX_BRANCH}）。请重启 start-desktop.bat，5174 会与稳定对齐。`,
    detail: mergeRes.stdout.trim() || undefined,
  };
}

async function assertSandboxWorktreeBranch(sandboxRoot: string): Promise<SandboxAdoptResult | null> {
  const branchRes = await runGit(sandboxRoot, ["rev-parse", "--abbrev-ref", "HEAD"]);
  const head = branchRes.code === 0 ? branchRes.stdout.trim() : "";
  if (head && head !== SANDBOX_BRANCH) {
    return {
      ok: false,
      message: `Sandbox 工作区当前在「${head}」分支，应在 ${SANDBOX_BRANCH}。请在 .worktrees/sandbox 内 checkout 正确分支。`,
    };
  }
  return null;
}

async function commitSandboxProduct(message: string): Promise<SandboxAdoptResult> {
  const sandboxRoot = resolveSandboxRoot();
  const branchErr = await assertSandboxWorktreeBranch(sandboxRoot);
  if (branchErr) return branchErr;
  const addRes = await runGit(sandboxRoot, ["add", "--", ...PRODUCT_PATH_ARGS]);
  if (addRes.code !== 0) {
    return { ok: false, message: "Sandbox 暂存失败。", detail: addRes.stderr || addRes.stdout };
  }
  const commitRes = await runGit(sandboxRoot, ["commit", "-m", message]);
  if (commitRes.code !== 0) {
    return { ok: false, message: "Sandbox 提交失败。", detail: commitRes.stderr || commitRes.stdout };
  }
  return { ok: true, message: "已在 sandbox 提交实验改动。" };
}

async function stashStableProduct(): Promise<SandboxAdoptResult & { stashRef?: string }> {
  const label = `my-agent pre-merge ${new Date().toISOString().replace(/[:.]/g, "-")}`;
  const stashRes = await runGit(repoRoot, ["stash", "push", "-m", label, "--", ...PRODUCT_PATH_ARGS]);
  if (stashRes.code !== 0) {
    return { ok: false, message: "稳定树暂存（stash）失败。", detail: stashRes.stderr || stashRes.stdout };
  }
  const listRes = await runGit(repoRoot, ["stash", "list", "-1", "--oneline"]);
  const stashRef = listRes.code === 0 ? listRes.stdout.trim() : undefined;
  return {
    ok: true,
    message: "已将稳定树 product 改动放入 stash。",
    detail: stashRef,
    stashRef,
  };
}

export async function prepareAndMergeSandboxIntoStable(
  options: SandboxAdoptPrepareOptions = {},
): Promise<SandboxAdoptResult> {
  const steps: string[] = [];
  let stashedStableProduct = false;
  let status = await getSandboxAdoptStatus();

  if (status.fatalBlockers.length > 0) {
    return {
      ok: false,
      message: status.fatalBlockers[0],
      detail: status.fatalBlockers.length > 1 ? status.fatalBlockers.slice(1).join("\n") : undefined,
    };
  }

  if (status.needsSandboxCommit) {
    if (!options.autoCommitSandbox) {
      return {
        ok: false,
        message: "Sandbox 还有未提交改动，需要你先确认自动提交，或自行在 sandbox 里 commit 后再点合入。",
      };
    }
    const msg = (options.sandboxCommitMessage || "").trim() || "chore(sandbox): prepare merge into stable";
    const committed = await commitSandboxProduct(msg);
    if (!committed.ok) return committed;
    steps.push(committed.message);
    status = await getSandboxAdoptStatus();
  }

  if (status.needsStableStash) {
    if (!options.autoStashStable) {
      return {
        ok: false,
        message: "稳定树还有未提交改动，需要你先确认自动 stash，或自行提交/ stash 后再点合入。",
      };
    }
    const stashed = await stashStableProduct();
    if (!stashed.ok) return stashed;
    stashedStableProduct = true;
    steps.push(stashed.message);
    if (stashed.stashRef) steps.push(stashed.stashRef);
    status = await getSandboxAdoptStatus();
  }

  if (!status.readyToMerge) {
    const tail = status.fatalBlockers[0] || "合入前检查仍未通过。";
    return {
      ok: false,
      message: tail,
      detail: steps.length ? steps.join("\n") : undefined,
      steps,
    };
  }

  const mergeRes = await runGit(repoRoot, ["merge", SANDBOX_BRANCH, "--no-edit", "--no-ff"]);
  if (mergeRes.code !== 0) {
    const tail = (mergeRes.stderr || mergeRes.stdout).trim();
    return {
      ok: false,
      message: "git merge 失败（可能有冲突，需在仓库里手动解决）。",
      detail: [steps.join("\n"), tail].filter(Boolean).join("\n\n"),
      steps,
    };
  }

  steps.push(`已 merge ${SANDBOX_BRANCH} → ${status.currentBranch}`);

  if (stashedStableProduct) {
    const popRes = await runGit(repoRoot, ["stash", "pop"]);
    if (popRes.code !== 0) {
      return {
        ok: true,
        message:
          "实验分支已合入，但未能自动恢复合入前 stash 的稳定树改动（壳预览等可能在 stash 里）。请执行 git stash pop。",
        detail: [steps.join("\n"), popRes.stderr || popRes.stdout].filter(Boolean).join("\n\n"),
        steps,
      };
    }
    steps.push("已恢复合入前 stash 的稳定树本地改动");
  }

  const syncHint =
    "合入后请点壳预览里的「同步 sandbox 与稳定」，否则 5174 仍是合入前的旧实验树。";

  return {
    ok: true,
    message: stashedStableProduct
      ? `合入完成，并已恢复合入前的稳定树本地改动。请重启 start-desktop.bat，在 5173 验收。${syncHint}`
      : `合入完成。请重启 start-desktop.bat，在 5173 验收。${syncHint}`,
    detail: mergeRes.stdout.trim() || undefined,
    steps,
  };
}

/** Merge only when already clean; use prepareAndMergeSandboxIntoStable for the full button flow. */
export async function mergeSandboxIntoStable(): Promise<SandboxAdoptResult> {
  return prepareAndMergeSandboxIntoStable({});
}
