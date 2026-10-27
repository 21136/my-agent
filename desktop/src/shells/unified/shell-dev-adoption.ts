type AdoptResult = { ok: boolean; message: string; detail?: string; steps?: string[]; merged?: boolean; synced?: boolean; recoveryStash?: string };
type SyncResult = {
  ok: boolean; message: string; detail?: string; synced?: boolean;
  beforeStableHead?: string; beforeSandboxHead?: string; afterSandboxHead?: string;
  needsTheirsConfirm?: boolean; blockers?: string[];
};
type AdoptionDesktopApi = {
  mergeSandboxIntoStable?: () => Promise<AdoptResult>;
  getSandboxAdoptStatus?: () => Promise<Record<string, unknown>>;
  syncSandboxWorktree?: (options?: { allowTheirsMerge?: boolean }) => Promise<SyncResult>;
};

export function shortHead(value: unknown): string {
  const text = typeof value === "string" ? value.trim() : "";
  return text ? text.slice(0, 7) : "—";
}

export function countList(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

/** Pure formatter for flat dual-lane cards (no legacySessionLanes gate). */
export function formatDualLaneStrip(status: Record<string, unknown> | undefined): {
  stableHead: string; sandboxHead: string; stableBranch: string; sandboxBranch: string;
  commitsAhead: string; stableAhead: string;
  sandboxDirty: string; stableDirty: string; hint: string; blockers: string; showBlockers: boolean;
} {
  if (!status) {
    return {
      stableHead: "—", sandboxHead: "—", stableBranch: "—", sandboxBranch: "—",
      commitsAhead: "—", stableAhead: "—",
      sandboxDirty: "—", stableDirty: "—",
      hint: "无法读取合入状态（请在 Electron 桌面工作台打开双车道）。",
      blockers: "", showBlockers: false,
    };
  }
  const branch = (value: unknown) => {
    const text = typeof value === "string" ? value.trim() : "";
    return text || "—";
  };
  const commitsAhead = status.commitsAhead == null ? "未知" : String(status.commitsAhead);
  const stableAhead = status.stableAheadOfSandbox == null ? "未知" : String(status.stableAheadOfSandbox);
  const sandboxDirty = String(countList(status.sandboxDirtyProduct));
  const stableDirty = String(countList(status.stableDirtyProduct));
  const syncBlockers = Array.isArray(status.syncBlockers) ? status.syncBlockers.map(String) : [];
  const fatalBlockers = Array.isArray(status.fatalBlockers) ? status.fatalBlockers.map(String) : [];
  // Prefer sync-oriented blockers for the cards; also surface fatal adopt blockers that are not the benign "nothing to merge".
  const adoptNoise = new Set(["没有待合入的实验改动。"]);
  const usefulFatal = fatalBlockers.filter((b) => !adoptNoise.has(b));
  const blockers = [...syncBlockers, ...usefulFatal.filter((b) => !syncBlockers.includes(b))];
  let hint = "双车道已对齐。";
  if (status.needsSandboxSync === true && Number(status.stableAheadOfSandbox) > 0) {
    hint = `稳定比实验新 ${status.stableAheadOfSandbox} 个提交。可点「用稳定刷实验」。`;
  } else if (Number(status.commitsAhead) > 0) {
    hint = `实验比稳定新 ${status.commitsAhead} 个提交。可点「合入稳定」。`;
  } else if (countList(status.sandboxDirtyProduct) > 0) {
    hint = `实验区有 ${sandboxDirty} 个未提交 product 文件；刷入前会拒绝。`;
  }
  if (blockers.length) hint = blockers[0];
  return {
    stableHead: shortHead(status.stableHead),
    sandboxHead: shortHead(status.sandboxHead),
    stableBranch: branch(status.currentBranch),
    sandboxBranch: branch(status.sandboxBranch),
    commitsAhead,
    stableAhead,
    sandboxDirty,
    stableDirty,
    hint,
    blockers: blockers.join("\n"),
    showBlockers: blockers.length > 0,
  };
}

/** Keep view orchestration separate from the main-process authorization boundary. */
export function createShellDevAdoptionController(
  api: AdoptionDesktopApi | undefined,
  feedback: (text: string, tone?: "ok" | "error" | "busy") => void,
  setBusy: (busy: boolean) => void,
  onStatus?: (status: Record<string, unknown> | undefined) => void,
) {
  let busy = false;

  function formatResult(result: AdoptResult): string {
    return [result.message, result.steps?.join(" → "), result.detail,
      result.recoveryStash ? "本次恢复备份：" + result.recoveryStash : ""].filter(Boolean).join("\n");
  }

  function formatSync(result: SyncResult): string {
    const before = shortHead(result.beforeSandboxHead);
    const after = shortHead(result.afterSandboxHead || result.beforeSandboxHead);
    const heads = result.beforeSandboxHead ? `实验 HEAD ${before} → ${after}` : "";
    return [result.message, heads, result.detail, ...(result.blockers || [])].filter(Boolean).join("\n");
  }

  async function refreshStatus(): Promise<Record<string, unknown> | undefined> {
    if (!api?.getSandboxAdoptStatus) {
      onStatus?.(undefined);
      return undefined;
    }
    const status = await api.getSandboxAdoptStatus();
    onStatus?.(status);
    return status;
  }

  async function merge(): Promise<void> {
    if (busy) return;
    if (!api?.mergeSandboxIntoStable) { feedback("请在 Electron 桌面工作台中使用合入功能；网页仅供预览。", "error"); return; }
    busy = true; setBusy(true);
    try {
      feedback("正在检查合入范围，请在桌面原生窗口确认；取消不会修改代码。", "busy");
      const result = await api.mergeSandboxIntoStable();
      feedback(formatResult(result), result.ok ? "ok" : "error");
      await refreshStatus();
    } catch (error) { feedback("合入未完成：" + (error instanceof Error ? error.message : String(error)), "error"); }
    finally { busy = false; setBusy(false); }
  }

  /** One-click path: refresh status first, surface fatal blockers in-panel, then same native adopt merge. */
  async function oneClick(): Promise<void> {
    if (busy) return;
    if (!api?.mergeSandboxIntoStable) { feedback("请在 Electron 桌面工作台中使用合入功能；网页仅供预览。", "error"); return; }
    busy = true; setBusy(true);
    try {
      if (api.getSandboxAdoptStatus) {
        feedback("正在检查合入状态…", "busy");
        const status = await api.getSandboxAdoptStatus();
        onStatus?.(status);
        const blockers = Array.isArray(status.fatalBlockers) ? status.fatalBlockers : [];
        if (blockers.length) {
          feedback(blockers.map(String).join("\n"), "error");
          return;
        }
      }
      feedback("正在检查合入范围，请在桌面原生窗口确认；取消不会修改代码。", "busy");
      const result = await api.mergeSandboxIntoStable();
      feedback(formatResult(result), result.ok ? "ok" : "error");
      await refreshStatus();
    } catch (error) { feedback("合入未完成：" + (error instanceof Error ? error.message : String(error)), "error"); }
    finally { busy = false; setBusy(false); }
  }

  async function inspect(): Promise<void> {
    if (!api?.getSandboxAdoptStatus) { feedback("请在 Electron 桌面工作台中检查合入状态。", "error"); return; }
    try {
      const status = await api.getSandboxAdoptStatus();
      onStatus?.(status);
      const blockers = Array.isArray(status.fatalBlockers) ? status.fatalBlockers : [];
      if (blockers.length) { feedback(blockers.map(String).join("\n"), "error"); return; }
      feedback([
        "稳定 HEAD " + shortHead(status.stableHead) + " · 实验 HEAD " + shortHead(status.sandboxHead),
        "已有实验提交：" + (status.commitsAhead ?? "未知") + "；稳定领先实验：" + (status.stableAheadOfSandbox ?? "未知"),
        "待保存实验文件：" + countList(status.sandboxDirtyProduct) + "；稳定区待备份：" + countList(status.stableDirtyProduct),
        "点击合入稳定后，还会由桌面主进程展示实际文件范围并请求确认。",
        "代码合入不代表真实运行或模型验收已通过。",
      ].join("\n"));
    } catch (error) { feedback("检查失败：" + String(error), "error"); }
  }

  async function syncFromStable(options: { allowTheirsMerge?: boolean } = {}): Promise<void> {
    if (busy) return;
    if (!api?.syncSandboxWorktree) { feedback("请在 Electron 桌面工作台中使用「用稳定刷实验」。", "error"); return; }
    busy = true; setBusy(true);
    try {
      feedback("正在检查稳定→实验同步状态…", "busy");
      const status = api.getSandboxAdoptStatus ? await api.getSandboxAdoptStatus() : undefined;
      if (status) onStatus?.(status);
      if (status) {
        const hard = Array.isArray(status.syncBlockers)
          ? status.syncBlockers.map(String).filter((b) => !b.includes("fast-forward 不可用") && !b.includes("-X theirs"))
          : [];
        if (hard.length && !options.allowTheirsMerge) {
          feedback(hard.join("\n"), "error");
          return;
        }
        const ahead = status.stableAheadOfSandbox;
        if (status.needsSandboxSync === true && Number(ahead) > 0) {
          feedback(`即将把稳定 ${shortHead(status.stableHead)} 刷进实验（当前实验 ${shortHead(status.sandboxHead)}，稳定领先 ${ahead}）…`, "busy");
        }
      }
      let result = await api.syncSandboxWorktree(options);
      if (result.needsTheirsConfirm && !options.allowTheirsMerge) {
        feedback(formatSync(result) + "\n再次点击「用稳定刷实验」将以稳定版为准（-X theirs）。也可先合入实验改动。", "error");
        onStatus?.(status);
        // Stash a one-shot flag on the controller via busy release; UI may call syncFromStable({allowTheirsMerge:true}).
        (syncFromStable as { pendingTheirs?: boolean }).pendingTheirs = true;
        return;
      }
      feedback(formatSync(result), result.ok ? "ok" : "error");
      (syncFromStable as { pendingTheirs?: boolean }).pendingTheirs = false;
      await refreshStatus();
    } catch (error) { feedback("刷入未完成：" + (error instanceof Error ? error.message : String(error)), "error"); }
    finally { busy = false; setBusy(false); }
  }

  async function sync(): Promise<void> {
    const pending = Boolean((syncFromStable as { pendingTheirs?: boolean }).pendingTheirs);
    await syncFromStable(pending ? { allowTheirsMerge: true } : {});
  }

  return { merge, oneClick, inspect, sync, refreshStatus, syncFromStable };
}