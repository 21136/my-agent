import { closeSync, existsSync, lstatSync, openSync, readFileSync, realpathSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";

const SANDBOX_BRANCH = "experiment/sandbox";
// Source/release material only: never add/stash an entire checkout or its data.
const RELEASE_PREFIXES = ["agent-core/", "desktop/", "docs/", "protocol/", "scripts/", "evolve/tools/", "evolve/skills/", "evolve/tool-catalog/"];
const RELEASE_FILES = new Set(["AGENTS.md", ".gitignore", "start-desktop.bat", "init-sandbox.bat", "check-stable-before-sandbox-merge.bat", "sync-sandbox-worktree.bat"]);
function releasePath(file: string): boolean {
  const parts = file.split("/");
  if (parts.some((p) => /^(?:\.git|\.venv|venv|node_modules|data|artifacts|dist|dist-electron|__pycache__)$/i.test(p)
    || /^\.env(?:\.|$)/i.test(p) || /\.(?:pem|key|p12|pfx)$/i.test(p))) return false;
  return !file.startsWith("/") && !parts.includes("..") && (RELEASE_FILES.has(file) || RELEASE_PREFIXES.some((p) => file.startsWith(p)));
}
const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const repoRoot = path.resolve(desktopDir, "..");
export function resolveSandboxRoot(): string {
  return path.resolve(process.env.MY_AGENT_SANDBOX_ROOT?.trim() || path.join(repoRoot, ".worktrees", "sandbox"));
}
export type SandboxAdoptStatus = {
  repoRoot: string; sandboxRoot: string; sandboxActive: boolean; currentBranch: string; sandboxBranch: string;
  commitsAhead: number | null; stableDirtyProduct: string[]; sandboxDirtyProduct: string[];
  stableDirtyOutside: string[]; mergeFiles: string[]; fatalBlockers: string[];
  needsSandboxCommit: boolean; needsStableStash: boolean; readyToMerge: boolean; recentCommits: string[];
  stableHead: string; sandboxHead: string; snapshotId: string;
};
export type SandboxAdoptPrepareOptions = {
  expectedSnapshotId?: string; autoCommitSandbox?: boolean; sandboxCommitMessage?: string; autoStashStable?: boolean;
};
export type SandboxAdoptResult = {
  ok: boolean; message: string; detail?: string; steps?: string[];
  merged?: boolean; synced?: boolean; recoveryStash?: string;
};
type GitResult = { code: number; stdout: string; stderr: string };
function runGit(cwd: string, args: string[], privateOptions: { indexFile?: string; input?: string } = {}): Promise<GitResult> {
  return new Promise((resolve) => {
    const env: NodeJS.ProcessEnv = { ...process.env, GIT_TERMINAL_PROMPT: "0" };
    for (const key of Object.keys(env)) {
      if (/^GIT_(?:DIR|WORK_TREE|INDEX_FILE|COMMON_DIR|OBJECT_DIRECTORY|ALTERNATE_OBJECT_DIRECTORIES|PREFIX|CONFIG|CONFIG_.*|NAMESPACE)$/i.test(key)) delete env[key];
    }
    if (privateOptions.indexFile) env.GIT_INDEX_FILE = privateOptions.indexFile;
    const child = spawn("git", ["--literal-pathspecs", ...args], {
      cwd, windowsHide: true, env,
    });
    let stdout = "", stderr = "";
    child.stdout?.on("data", (chunk) => { stdout += chunk; });
    child.stderr?.on("data", (chunk) => { stderr += chunk; });
    child.stdin?.on("error", () => { /* process exit reports command failure without leaking patch bytes */ });
    child.stdin?.end(privateOptions.input);
    child.on("close", (code) => resolve({ code: code ?? 1, stdout, stderr }));
    child.on("error", (err) => resolve({ code: 1, stdout, stderr: err.message }));
  });
}
async function git(cwd: string, args: string[]): Promise<string> {
  const result = await runGit(cwd, args);
  if (result.code !== 0) throw new Error((result.stderr || result.stdout).trim() || "Git 检查失败");
  return result.stdout.trim();
}
type Dirty = { xy: string; file: string };
async function dirty(cwd: string): Promise<Dirty[]> {
  const result = await runGit(cwd, ["status", "--porcelain=v1", "-z", "--untracked-files=all"]);
  if (result.code !== 0) throw new Error(result.stderr || "不能读取工作区状态");
  const records = result.stdout.split("\0"); const entries: Dirty[] = [];
  for (let i = 0; i < records.length; i++) {
    const record = records[i]; if (!record) continue;
    const xy = record.slice(0, 2); entries.push({ xy, file: record.slice(3) });
    if (/[RC]/.test(xy)) entries.push({ xy, file: records[++i] });
  }
  return entries;
}
function label(entry: Dirty): string { return entry.xy + " " + entry.file; }
function fingerprint(root: string, entries: Dirty[]): string[] {
  return entries.map(({ xy, file }) => {
    if (!releasePath(file)) return xy + file;
    const full = path.resolve(root, file);
    const info = lstatSync(full, { throwIfNoEntry: false });
    if (!info) return xy + file + ":deleted";
    if (info.isSymbolicLink()) throw new Error("发布文件不能是符号链接：" + file);
    const relative = path.relative(realpathSync(root), realpathSync(full));
    if (relative.startsWith("..") || path.isAbsolute(relative) || lstatSync(full).isSymbolicLink() || !lstatSync(full).isFile()) {
      throw new Error("发布文件必须是工作区内的普通文件：" + file);
    }
    return xy + file + ":" + createHash("sha256").update(readFileSync(full)).digest("hex");
  });
}
export type AdoptionWriteObserver = {
  assertIdle():void; begin?(status:SandboxAdoptStatus):void;
  beforeWrite(event:{step:string;cwd:string}):void; afterWrite(event:{step:string;cwd:string;code:number}):void;
  backup(sha:string):void; finish(result:SandboxAdoptResult):void;
};
export type SandboxAdopter = ReturnType<typeof createSandboxAdopter>;

/** Injectable checkout roots let integration tests use real disposable Git worktrees. */
export function createSandboxAdopter(roots: { stableRoot: string; sandboxRoot: string }, runtime: {observer?:AdoptionWriteObserver;revalidate?:()=>void} = {}) {
  const stableRoot = path.resolve(roots.stableRoot), sandboxRoot = path.resolve(roots.sandboxRoot);
  async function scopedRunGit(cwd:string,args:string[],privateOptions:{indexFile?:string;input?:string}={}):Promise<GitResult> {
    const mutating = new Set(['add','commit','stash','write-tree','read-tree','merge-tree','commit-tree','merge','apply']).has(args[0]);
    const step=args[0]==='stash' ? 'stash:'+args[1] : args[0]==='merge' && args.includes('--abort') ? 'merge:abort' : args[0];
    if(mutating){runtime.revalidate?.();runtime.observer?.beforeWrite({step,cwd});}
    const result=await runGit(cwd,args,privateOptions);
    if(mutating) runtime.observer?.afterWrite({step,cwd,code:result.code});
    return result;
  }
  async function scopedGit(cwd:string,args:string[]):Promise<string> {const result=await scopedRunGit(cwd,args);if(result.code!==0)throw Error((result.stderr||result.stdout).trim()||'Git 检查失败');return result.stdout.trim();}
  async function getStatus(): Promise<SandboxAdoptStatus> {
    const status: SandboxAdoptStatus = {
      repoRoot: stableRoot, sandboxRoot, sandboxActive: false, currentBranch: "?", sandboxBranch: SANDBOX_BRANCH,
      commitsAhead: null, stableDirtyProduct: [], sandboxDirtyProduct: [], stableDirtyOutside: [], mergeFiles: [],
      fatalBlockers: [], needsSandboxCommit: false, needsStableStash: false, readyToMerge: false,
      recentCommits: [], stableHead: "", sandboxHead: "", snapshotId: "",
    };
    try {
      runtime.revalidate?.();
      const stableTop = await scopedGit(stableRoot, ["rev-parse", "--show-toplevel"]);
      const sandboxTop = await scopedGit(sandboxRoot, ["rev-parse", "--show-toplevel"]);
      if (realpathSync(stableTop) !== realpathSync(stableRoot) || realpathSync(sandboxTop) !== realpathSync(sandboxRoot)
        || realpathSync(stableRoot) === realpathSync(sandboxRoot)) throw new Error("请从稳定版 Host 合入独立 sandbox，不能从实验版合入自身。");
      const common = await scopedGit(stableRoot, ["rev-parse", "--path-format=absolute", "--git-common-dir"]);
      const otherCommon = await scopedGit(sandboxRoot, ["rev-parse", "--path-format=absolute", "--git-common-dir"]);
      if (realpathSync(common) !== realpathSync(otherCommon)) throw new Error("Sandbox 不属于当前稳定仓库的同一 Git worktree。");
      status.currentBranch = await scopedGit(stableRoot, ["symbolic-ref", "--short", "HEAD"]);
      const sandboxBranch = await scopedGit(sandboxRoot, ["symbolic-ref", "--short", "HEAD"]);
      if (status.currentBranch === SANDBOX_BRANCH || sandboxBranch !== SANDBOX_BRANCH) throw new Error("工作区分支不正确；稳定版不能检出 experiment/sandbox，sandbox 必须保留该分支。");
      status.stableHead = await scopedGit(stableRoot, ["rev-parse", "HEAD"]);
      status.sandboxHead = await scopedGit(sandboxRoot, ["rev-parse", "HEAD"]);
      if (!existsSync(path.join(sandboxRoot, "desktop", "package.json")) || !existsSync(path.join(sandboxRoot, "agent-core"))) throw new Error("Sandbox 尚未就绪。");
      status.sandboxActive = true;
      for (const cwd of [stableRoot, sandboxRoot]) {
        for (const name of ["MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge", "rebase-apply", "BISECT_LOG"]) {
          const statePath = await scopedGit(cwd, ["rev-parse", "--git-path", name]);
          if (existsSync(path.resolve(cwd, statePath))) status.fatalBlockers.push("工作区已有 Git 操作未完成，合入不会接管或中止它：" + name);
        }
      }
      const stableEntries = await dirty(stableRoot), sandboxEntries = await dirty(sandboxRoot);
      const stableSource = stableEntries.filter((e) => releasePath(e.file));
      const sandboxSource = sandboxEntries.filter((e) => releasePath(e.file));
      status.stableDirtyProduct = stableSource.map(label);
      status.sandboxDirtyProduct = sandboxSource.map(label);
      status.stableDirtyOutside = stableEntries.filter((e) => !releasePath(e.file)).map(label);
      const unsupported = sandboxEntries.filter((e) => !releasePath(e.file));
      if (unsupported.length) status.fatalBlockers.push("Sandbox 含发布范围外改动，不会自动提交：" + unsupported.map((e) => e.file).join("、"));
      if (stableEntries.some((e) => !releasePath(e.file) && e.xy[0] !== " " && e.xy !== "??")) status.fatalBlockers.push("稳定区存在发布范围外的已暂存改动，不会混入合并提交。");
      status.needsStableStash = stableSource.length > 0;
      status.needsSandboxCommit = sandboxSource.length > 0;
      const range = status.stableHead + ".." + status.sandboxHead;
      status.commitsAhead = Number(await scopedGit(stableRoot, ["rev-list", "--count", range]));
      status.recentCommits = (await scopedGit(stableRoot, ["log", "--oneline", "-8", range])).split(/\r?\n/).filter(Boolean);
      const diff = await scopedRunGit(stableRoot, ["diff", "--name-only", "--no-renames", "-z", status.stableHead + "..." + status.sandboxHead]);
      if (diff.code !== 0) throw new Error(diff.stderr || "不能比较两条分支");
      status.mergeFiles = diff.stdout.split("\0").filter(Boolean);
      const changedFiles = new Set(status.mergeFiles);
      const tree = await scopedRunGit(sandboxRoot, ["ls-tree", "-r", "-z", status.sandboxHead]);
      if (tree.code !== 0) throw new Error("不能检查实验提交的文件类型。");
      for (const entry of tree.stdout.split("\0").filter(Boolean)) {
        const tab = entry.indexOf("\t");
        if (changedFiles.has(entry.slice(tab + 1)) && !/^(?:100644|100755) blob /.test(entry)) {
          status.fatalBlockers.push("实验提交含链接或子模块，不能作为普通发布文件合入：" + entry.slice(tab + 1));
        }
      }
      const blockedCommitted = status.mergeFiles.filter((file) => !releasePath(file));
      if (blockedCommitted.length) status.fatalBlockers.push("实验提交包含发布范围外文件，禁止整支合入：" + blockedCommitted.join("、"));
      if (status.commitsAhead === 0 && !status.needsSandboxCommit) status.fatalBlockers.push("没有待合入的实验改动。");
      // Bind consent to HEAD, file bytes AND each index; same filename/status is not sufficient.
      const stableIndex = await scopedGit(stableRoot, ["diff", "--cached", "--binary"]);
      const sandboxIndex = await scopedGit(sandboxRoot, ["diff", "--cached", "--binary"]);
      status.snapshotId = createHash("sha256").update(JSON.stringify({
        stableRoot: realpathSync(stableRoot), sandboxRoot: realpathSync(sandboxRoot), branch: status.currentBranch,
        stableHead: status.stableHead, sandboxHead: status.sandboxHead,
        stable: fingerprint(stableRoot, stableEntries), sandbox: fingerprint(sandboxRoot, sandboxEntries),
        stableIndex, sandboxIndex, blockers: status.fatalBlockers,
      })).digest("hex");
      status.readyToMerge = status.fatalBlockers.length === 0 && !status.needsSandboxCommit && !status.needsStableStash;
    } catch (error) { status.fatalBlockers.push(error instanceof Error ? error.message : String(error)); }
    return status;
  }

  async function performPrepareAndMerge(options: SandboxAdoptPrepareOptions = {}): Promise<SandboxAdoptResult> {
    const steps: string[] = [];
    let lock: number | undefined, lockPath = "", recoveryStash: string | undefined;
    let merged = false, synced = false, ownMerge = false, stableRestored = false;
    const fail = (message: string, detail?: string): SandboxAdoptResult => ({ ok: false, message, detail, steps, merged, synced, recoveryStash });
    async function restoreStable(): Promise<string | undefined> {
      if (!recoveryStash || stableRestored) return;
      const applied = await scopedRunGit(stableRoot, ["stash", "apply", "--index", recoveryStash]);
      if (applied.code !== 0) return (applied.stderr || applied.stdout).trim();
      stableRestored = true;
      // Keep the exact backup until the user accepts the restarted stable version.
      // Never resolve/drop a moving stash@{n}; another task may push a stash at any time.
      steps.push("已精确恢复本次稳定改动及原暂存状态；本次备份保留至验收后处理");
    }
    try {
      const common = await scopedGit(stableRoot, ["rev-parse", "--path-format=absolute", "--git-common-dir"]);
      lockPath = path.join(common, "my-agent-sandbox-adopt.lock");
      try { lock = openSync(lockPath, "wx"); writeFileSync(lock, JSON.stringify({ pid: process.pid, at: new Date().toISOString() })); }
      catch { return fail("另一次合入正在进行中，或合入锁尚未释放；不会抢占锁。"); }
      const status = await getStatus();
      if (status.fatalBlockers.length) return fail(status.fatalBlockers[0], status.fatalBlockers.slice(1).join("\n"));
      if (!options.expectedSnapshotId || options.expectedSnapshotId !== status.snapshotId) return fail("改动已变化或尚未确认，请重新检查并确认合入。");
      // Validate ALL consents before committing even the first file.
      if (status.needsSandboxCommit && options.autoCommitSandbox !== true) return fail("需要确认保存本次 sandbox 改动。");
      if (status.needsStableStash && options.autoStashStable !== true) return fail("需要确认备份并恢复稳定区本地改动。");
      runtime.observer?.begin?.(status);
      if (status.needsSandboxCommit) {
        const files = [...new Set(status.sandboxDirtyProduct.map((line) => line.slice(3)))];
        await scopedGit(sandboxRoot, ["add", "--", ...files]);
        await scopedGit(sandboxRoot, ["commit", "-m", options.sandboxCommitMessage?.trim() || "chore(sandbox): prepare confirmed adoption"]);
        steps.push("已保存确认范围内的实验源码、协议和文档");
      }
      const sandboxHead = await scopedGit(sandboxRoot, ["rev-parse", "HEAD"]);
      if (status.needsStableStash) {
        const files = [...new Set(status.stableDirtyProduct.map((line) => line.slice(3)))];
        const backupId = randomUUID();
        const backupLabel = "my-agent adopt " + backupId;
        // Even a failed stash push may already have changed the index. Keep its
        // original tree and identify this operation's saved object before handling exit status.
        const originalIndexTree = await scopedGit(stableRoot, ["write-tree"]);
        const pushed = await scopedRunGit(stableRoot, ["stash", "push", "--include-untracked", "-m", backupLabel, "--", ...files]);
        const backups = await scopedGit(stableRoot, ["stash", "list", "--format=%H %gs"]);
        recoveryStash = backups.split(/\r?\n/).find((line) => line.endsWith(": " + backupLabel))?.split(" ")[0];
        if(recoveryStash) runtime.observer?.backup(recoveryStash);
        if (!recoveryStash) {
          await scopedGit(stableRoot, ["read-tree", originalIndexTree]);
          return fail("不能唯一确认本次稳定备份，已停止；原暂存树已恢复，保留其它现场。", pushed.stderr || pushed.stdout);
        }
        steps.push("已备份稳定区本地改动；恢复对象：" + recoveryStash);
        if (pushed.code !== 0) {
          const recovery = await restoreStable();
          return fail("稳定改动备份未完成，已停止且未移动稳定分支。", [pushed.stderr || pushed.stdout, recovery].filter(Boolean).join("\n"));
        }
      }
      // Pin exact commits, not a mutable branch name. Recheck roots/branch/index before merge.
      const fresh = await getStatus();
      if (fresh.fatalBlockers.length || fresh.stableHead !== status.stableHead || fresh.sandboxHead !== sandboxHead
        || fresh.needsSandboxCommit || fresh.needsStableStash || fresh.currentBranch !== status.currentBranch) {
        const recovery = await restoreStable();
        return fail("准备期间工作区发生变化，已停止合入。", recovery || fresh.fatalBlockers.join("\n"));
      }
      // Prove both the branch merge and stash restoration BEFORE moving stable HEAD.
      // merge-tree/commit-tree and the private index create Git objects, never check out files.
      const predicted = await scopedRunGit(stableRoot, ["merge-tree", "--write-tree", status.stableHead, sandboxHead]);
      const mergedTree = predicted.stdout.split(/\r?\n/)[0];
      let preflightError = predicted.code !== 0 || !/^[0-9a-f]{40,64}$/.test(mergedTree)
        ? (predicted.stderr || predicted.stdout || "不能预检实验合并。") : "";
      if (!preflightError && recoveryStash) {
        const temporaryCommit = await scopedGit(stableRoot, ["commit-tree", mergedTree, "-p", status.stableHead, "-p", sandboxHead, "-m", "my-agent adoption preflight (unreferenced)"]);
        const worktreeMerge = await scopedRunGit(stableRoot, ["merge-tree", "--write-tree", temporaryCommit, recoveryStash]);
        if (worktreeMerge.code !== 0) preflightError = "稳定区本地内容与实验版存在恢复冲突。";
        const untrackedParent = await scopedRunGit(stableRoot, ["rev-parse", "--verify", recoveryStash + "^3"]);
        if (!preflightError && untrackedParent.code === 0) {
          const untrackedTree = await scopedRunGit(stableRoot, ["ls-tree", "-r", "--name-only", "-z", untrackedParent.stdout.trim()]);
          const adoptedTree = await scopedRunGit(stableRoot, ["ls-tree", "-r", "--name-only", "-z", mergedTree]);
          if (untrackedTree.code !== 0 || adoptedTree.code !== 0) preflightError = "不能检查未跟踪文件的恢复范围。";
          const filesystemKey = (file: string) => process.platform === "win32"
            ? file.split("/").map((part) => part.replace(/[ .]+$/, "").toLowerCase()).join("/") : file;
          const adoptedFiles = adoptedTree.stdout.split("\0").filter(Boolean).map(filesystemKey);
          for (const file of untrackedTree.stdout.split("\0").filter(Boolean)) {
            const key = filesystemKey(file);
            if (adoptedFiles.some((adopted) => adopted === key || adopted.startsWith(key + "/") || key.startsWith(adopted + "/"))) {
              preflightError = "稳定区未跟踪文件会与合入内容冲突：" + file;
              break;
            }
          }
        }
        if (!preflightError) {
          const indexDelta = await scopedRunGit(stableRoot, ["diff", "--binary", recoveryStash + "^1", recoveryStash + "^2"]);
          if (indexDelta.code !== 0) preflightError = "不能检查原暂存区的恢复补丁。";
          else if (indexDelta.stdout) {
            const privateIndex = path.join(common, "my-agent-adopt-index-" + randomUUID());
            try {
              const read = await scopedRunGit(stableRoot, ["read-tree", mergedTree], { indexFile: privateIndex });
              if (read.code !== 0) preflightError = "不能创建恢复预检索引。";
              else {
                const apply = await scopedRunGit(stableRoot, ["apply", "--cached", "--binary", "--whitespace=nowarn"], { indexFile: privateIndex, input: indexDelta.stdout });
                if (apply.code !== 0) preflightError = "稳定区原暂存状态不能安全恢复到合入结果。";
              }
            } finally {
              // Each exact path is generated in this repository's common Git dir, never from renderer input.
              for (const file of [privateIndex, privateIndex + ".lock"]) if (existsSync(file)) unlinkSync(file);
            }
          }
        }
      }
      if (preflightError) {
        const recovery = await restoreStable();
        return fail("合入/本地改动恢复预检未通过，稳定分支未移动；没有自动选择任何一边。", [preflightError, recovery].filter(Boolean).join("\n"));
      }
      const merge = await scopedRunGit(stableRoot, ["merge", "--no-ff", "--no-commit", sandboxHead]);
      const mergePath = await scopedGit(stableRoot, ["rev-parse", "--git-path", "MERGE_HEAD"]);
      ownMerge = existsSync(path.resolve(stableRoot, mergePath));
      if (merge.code !== 0) {
        if (ownMerge) { await scopedGit(stableRoot, ["merge", "--abort"]); ownMerge = false; }
        const recovery = await restoreStable();
        return fail("实验与稳定版存在冲突或无法合并，已停止；没有自动选择任何一边。", [merge.stderr || merge.stdout, recovery].filter(Boolean).join("\n"));
      }
      if (ownMerge) { await scopedGit(stableRoot, ["commit", "-m", "chore: adopt confirmed sandbox into " + status.currentBranch]); ownMerge = false; }
      merged = true;
      steps.push("实验改动已合入稳定分支 " + status.currentBranch);
      const recovery = await restoreStable();
      if (recovery) return fail("代码已合入，但稳定改动恢复未完成；这不是完整成功。本次备份仍保留。", recovery);
      const stableHead = await scopedGit(stableRoot, ["rev-parse", "HEAD"]);
      const syncStatus = await dirty(sandboxRoot);
      if (syncStatus.length || await scopedGit(sandboxRoot, ["symbolic-ref", "--short", "HEAD"]) !== SANDBOX_BRANCH
        || await scopedGit(sandboxRoot, ["rev-parse", "HEAD"]) !== sandboxHead) return fail("代码已合入，但实验区有新改动，未自动同步；请检查后重试同步。");
      await scopedGit(sandboxRoot, ["merge", "--ff-only", stableHead]);
      synced = (await scopedGit(sandboxRoot, ["rev-parse", "HEAD"])) === stableHead;
      if (!synced) return fail("代码已合入，但实验区同步尚未确认。");
      steps.push("实验版已同步到本次稳定提交，可继续下一轮实验");
      return { ok: true, merged, synced, steps, recoveryStash, message: "合入、恢复本地改动及同步均完成。请关闭后从原 start-desktop.bat 入口重启，在稳定版验收。" };
    } catch (error) {
      let detail = error instanceof Error ? error.message : String(error);
      if (ownMerge) {
        const aborted = await scopedRunGit(stableRoot, ["merge", "--abort"]);
        if (aborted.code !== 0) return fail("合入失败且自动中止未完成；保留现场及本次备份，不执行后续恢复。", detail + "\n" + aborted.stderr);
      }
      if (recoveryStash && !merged) detail += "\n" + ((await restoreStable()) || "稳定改动已恢复。");
      return fail(merged ? "代码已合入，但收尾未完成，不能标为完整成功。" : "合入未完成，已停止。", detail);
    } finally {
      if (lock !== undefined) { closeSync(lock); unlinkSync(lockPath); }
    }
  }
  async function prepareAndMerge(options:SandboxAdoptPrepareOptions={}):Promise<SandboxAdoptResult> {
    try {runtime.revalidate?.();runtime.observer?.assertIdle();}
    catch(error){return {ok:false,message:'固定控制状态需要检查，未执行 Git 写操作。',detail:error instanceof Error?error.message:String(error)};}
    const result=await performPrepareAndMerge(options);
    runtime.observer?.finish(result);
    return result;
  }
  return { getStatus, prepareAndMerge };
}

/** Only main supplies this callback; renderer options cannot substitute for native approval. */
export function createSandboxAdoptRequestHandler(service: SandboxAdopter, confirm: (status: SandboxAdoptStatus) => Promise<boolean>) {
  let busy = false;
  return async (): Promise<SandboxAdoptResult> => {
    if (busy) return { ok: false, message: "合入确认或执行正在进行中。" };
    busy = true;
    try {
      const status = await service.getStatus();
      if (status.fatalBlockers.length) return { ok: false, message: status.fatalBlockers[0], detail: status.fatalBlockers.slice(1).join("\n") };
      if (!await confirm(status)) return { ok: false, message: "已取消，未执行任何 Git 写操作。" };
      return await service.prepareAndMerge({ expectedSnapshotId: status.snapshotId, autoCommitSandbox: status.needsSandboxCommit, autoStashStable: status.needsStableStash });
    } catch (error) { return { ok: false, message: error instanceof Error ? error.message : String(error) }; }
    finally { busy = false; }
  };
}
/** Do not overwrite malformed state or unrelated settings while leaving preview mode. */
export function clearSandboxPreviewPreference(statePath: string): void {
  if (!existsSync(statePath)) return;
  const loaded: unknown = JSON.parse(readFileSync(statePath, "utf8"));
  if (!loaded || typeof loaded !== "object" || Array.isArray(loaded)) throw new Error("主壳配置格式无效，保留原文件。");
  const payload = loaded as Record<string, unknown>;
  if (!Object.hasOwn(payload, "desktop_shell_renderer_url")) return;
  delete payload.desktop_shell_renderer_url;
  const temp = statePath + ".adopt-" + randomUUID();
  writeFileSync(temp, JSON.stringify(payload, null, 2) + "\n", { encoding: "utf8", flag: "wx" });
  try { renameSync(temp, statePath); }
  catch (error) { unlinkSync(temp); throw error; }
}

/** A Vite-managed Host can be killed by its own source merge; require fixed control code. */
export function sandboxAdoptionHostBlocker(host: { mutableDevelopmentHost: boolean; testHost: boolean; fixedHostVerified?:boolean }): string | null {
  if (host.testHost) return "测试 Host 不提供真实稳定版采用功能。";
  if (host.mutableDevelopmentHost) return "当前是源码热更新的开发态 Host，合入可能中途重启控制进程；需要先发布固定控制端，暂不执行真实采用。";
  if(host.fixedHostVerified !== true) return "当前 Host 未验证为固定控制发行，暂不执行真实采用。";
  return null;
}

export function isTrustedSandboxAdoptSender(event: { senderId: number; windowId: number; isMainFrame: boolean; origin: string; expectedOrigin: string }): boolean {
  return event.senderId === event.windowId && event.isMainFrame === true && event.origin !== "" && event.expectedOrigin !== "" && event.origin === event.expectedOrigin;
}
const defaultService = createSandboxAdopter({ stableRoot: repoRoot, sandboxRoot: resolveSandboxRoot() });
export const getSandboxAdoptStatus = defaultService.getStatus;
export const prepareAndMergeSandboxIntoStable = defaultService.prepareAndMerge;
export const mergeSandboxIntoStable = () => defaultService.prepareAndMerge();
export const createDefaultSandboxAdoptRequestHandler = (confirm: (status: SandboxAdoptStatus) => Promise<boolean>) => createSandboxAdoptRequestHandler(defaultService, confirm);
