import { createManagedReviewCatalog } from "./managed-review-catalog.ts";
export interface ManagedSourceSelectionPanel {
  show(report: Record<string, unknown>): void;
  clear(): void;
}
export type ManagedSourceSelectionResponse = { ok: true; proposal: Record<string, unknown> } | { ok: false; message: string };
export type ManagedCheckpointReceipt = Record<string, unknown>;
export type ManagedCheckpointResponse = { ok: true; receipt: ManagedCheckpointReceipt } | { ok: false; message: string };
export type ManagedSelectedMergeResponse = { ok: true; receipt: Record<string, unknown> } | { ok: false; message: string; maintenanceRequired?: boolean };

const HASH = /^[a-f0-9]{64}$/;
const PAGE_SIZE = 25;
type Entry = Record<string, unknown> & { path: string; workingSha256: string | null };
type Catalog = { digest: string; sandbox: Entry[]; stable: Entry[] };
const isRecord = (value: unknown): value is Record<string, any> => !!value && typeof value === "object" && !Array.isArray(value);
const text = (document: Document, tag: string, value: string): HTMLElement => { const el = document.createElement(tag); el.textContent = value; return el; };
const validPath = (path: unknown): path is string => typeof path === "string" && path.length > 0 && !path.startsWith("/") && !path.includes("\\") && !path.includes(":") && !/[\u0000-\u001f\u007f]/.test(path) && path.split("/").every(part => !!part && part !== "." && part !== ".." && !/[. ]$/.test(part) && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(part)) && (new Set(["AGENTS.md", ".gitignore", "start-desktop.bat", "init-sandbox.bat", "check-stable-before-sandbox-merge.bat", "sync-sandbox-worktree.bat"]).has(path) || ["agent-core/", "desktop/", "docs/", "protocol/", "scripts/", "evolve/tools/", "evolve/skills/", "evolve/tool-catalog/"].some(prefix => path.startsWith(prefix))) && !path.split("/").some(part => /^(?:\.env(?:\..*)?|llm_secrets\.(?:json|ya?ml)|llm_models\.(?:json|ya?ml)|.*\.(?:pem|key|p12|pfx|cer|crt))$/i.test(part) || [".git", ".venv", "venv", "node_modules", "data", "artifacts", "dist", "dist-electron", "__pycache__", "profile", "profiles", "user-data", "workspace", "workspaces", ".managed-runtime"].includes(part.toLowerCase()));
function validateReport(value: unknown): Catalog {
  if (!isRecord(value) || value.format !== 1 || value.mode !== "managed_source_review_v1" || typeof value.review_digest !== "string" || !HASH.test(value.review_digest)) throw new TypeError("审查报告身份或 SHA-256 无效");
  createManagedReviewCatalog(value);
  if (value.execution !== false || value.readyForMerge !== false || value.readyForLaunch !== false) throw new TypeError("审查报告安全标志无效");
  for (const laneName of ["stable", "sandbox"]) {
    const lane = value[laneName];
    if (!isRecord(lane) || typeof lane.head !== "string" || typeof lane.branch !== "string" || typeof lane.indexDigest !== "string" || !isRecord(lane.identity) || typeof lane.identity.worktreeRoot !== "string" || !isRecord(lane.inventory)) throw new TypeError(`${laneName} 基线结构无效`);
    const inventory = lane.inventory;
    if (typeof inventory.inventory_digest !== "string" || !HASH.test(inventory.inventory_digest) || !Array.isArray(inventory.entries) || !Array.isArray(inventory.excluded) || !Array.isArray(inventory.limitations)) throw new TypeError(`${laneName} 清单结构无效`);
    if (!inventory.entries.every((entry: unknown) => isRecord(entry) && validPath(entry.path) && typeof entry.state === "string" && typeof entry.staged === "boolean" && typeof entry.rawDiverged === "boolean" && typeof entry.modeDiverged === "boolean" && (entry.workingSha256 === null || typeof entry.workingSha256 === "string" && HASH.test(entry.workingSha256)))) throw new TypeError(`${laneName} 条目结构无效`);
    const folded = inventory.entries.map((entry: Entry) => entry.path.toLocaleLowerCase("en-US"));
    if (new Set(folded).size !== folded.length) throw new TypeError(`${laneName} 路径重复或大小写别名`);
  }
  if (!isRecord(value.history) || !Array.isArray(value.history.paths) || !Array.isArray(value.history.commits) || !isRecord(value.effectiveDelta) || value.effectiveDelta.basis !== "merge_base_to_sandbox_head" || !Array.isArray(value.effectiveDelta.paths) || !Array.isArray(value.blockers) || !Array.isArray(value.limitations)) throw new TypeError("审查报告全局结构无效");
  return { digest: value.review_digest, sandbox: value.sandbox.inventory.entries, stable: value.stable.inventory.entries };
}
function stableSummary(entry: Entry | undefined): string {
  if (!entry) return "stable：清单未列出对应路径（不代表文件不存在）";
  return `stable：${String(entry.state)} · SHA-256=${entry.workingSha256 ?? "未知/缺失"}`;
}
function validProposalForSelection(proposal: Record<string, unknown> | null, digest: string, paths: string[]): boolean {
  return !!proposal && proposal.mode === "managed_source_selection_proposal_v1" && proposal.reviewDigest === digest && proposal.proposal_digest !== undefined && typeof proposal.proposal_digest === "string" && HASH.test(proposal.proposal_digest) && JSON.stringify(proposal.selectedPaths) === JSON.stringify([...paths].sort()) && proposal.execution === false && proposal.readyForMerge === false && proposal.readyForLaunch === false && proposal.checkpointCreated === false && proposal.nativeConsent === false;
}
function validReceipt(value: unknown, proposal: Record<string, unknown>, digest: string, paths: string[]): value is ManagedCheckpointReceipt {
  const keys = ["format", "mode", "reviewDigest", "proposalDigest", "checkpointDigest", "selectedPaths", "rawBytes", "storedBytes", "checkpointCreated", "execution", "readyForMerge", "readyForLaunch", "nativeConsent"];
  if (!isRecord(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))) return false;
  return value.format === 1 && value.mode === "managed_source_checkpoint_receipt_v1" && value.reviewDigest === digest && value.proposalDigest === proposal.proposal_digest && typeof value.checkpointDigest === "string" && HASH.test(value.checkpointDigest) && JSON.stringify(value.selectedPaths) === JSON.stringify([...paths].sort()) && Number.isSafeInteger(value.rawBytes) && (value.rawBytes as number) >= 0 && Number.isSafeInteger(value.storedBytes) && (value.storedBytes as number) >= 0 && value.checkpointCreated === true && value.execution === false && value.readyForMerge === false && value.readyForLaunch === false && value.nativeConsent === false;
}
function validMergeReceipt(value: unknown, checkpoint: ManagedCheckpointReceipt): value is Record<string, unknown> {
  const keys = ["format", "mode", "status", "planDigest", "checkpointDigest", "selectedPaths", "stableHeadBefore", "stableHeadAfter", "mergedTree", "sourceMergeExecuted", "sourceHistoryMerged", "readyForLaunch", "receiptDigest"];
  const oid = (item: unknown) => typeof item === "string" && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(item);
  return isRecord(value) && Object.keys(value).length === keys.length && keys.every(key => Object.hasOwn(value,key))
    && value.format === 1 && value.mode === "managed_selected_merge_receipt_v1" && value.status === "source_merge_verified"
    && value.checkpointDigest === checkpoint.checkpointDigest && typeof value.planDigest === "string" && HASH.test(value.planDigest)
    && JSON.stringify(value.selectedPaths) === JSON.stringify(checkpoint.selectedPaths) && oid(value.stableHeadBefore) && oid(value.stableHeadAfter) && oid(value.mergedTree)
    && value.sourceMergeExecuted === true && value.sourceHistoryMerged === false && value.readyForLaunch === false && typeof value.receiptDigest === "string" && HASH.test(value.receiptDigest);
}
function proposalBlockerCount(proposal: Record<string, unknown>): string {
  const blockers = proposal.blockers;
  if (!Array.isArray(blockers)) return "阻塞项：报告未提供可计数的 blockers 数组";
  return `阻塞项：${blockers.length} 条 / ${blockers.reduce((sum, item) => sum + (isRecord(item) && Array.isArray(item.paths) ? item.paths.length : 0), 0)} 个路径关联`;
}

export function mountManagedSourceSelection(container: HTMLElement, options: { isDesktop: boolean; propose?: (request: { format: 1; mode: "managed_source_selection_request_v1"; reviewDigest: string; selectedPaths: string[] }) => Promise<ManagedSourceSelectionResponse>; createCheckpoint?: (request: { format: 1; mode: "managed_source_checkpoint_request_v1"; reviewDigest: string; proposalDigest: string; selectedPaths: string[] }) => Promise<ManagedCheckpointResponse>; executeMerge?: (request: { format: 1; mode: "managed_selected_merge_request_v1"; checkpointDigest: string }) => Promise<ManagedSelectedMergeResponse> }): ManagedSourceSelectionPanel {
  const document = container.ownerDocument;
  let catalog: Catalog | null = null;
  let query = ""; let page = 1; let epoch = 0; let pending = false; let checkpointPending = false; let mergePending = false;
  let selected = new Set<string>(); let proposal: Record<string, unknown> | null = null; let receipt: ManagedCheckpointReceipt | null = null; let mergeReceipt: Record<string, unknown> | null = null; let mergeMaintenanceRequired = false; const maintenanceBlockedCheckpoints = new Set<string>(); let errorMessage = "";
  let search: HTMLInputElement | null = null; let list: HTMLOListElement | null = null; let pager: HTMLElement | null = null; let output: HTMLElement | null = null;
  let submit: HTMLButtonElement | null = null; let checkpoint: HTMLButtonElement | null = null; let merge: HTMLButtonElement | null = null; let status: HTMLElement | null = null; let selectionSummary: HTMLElement | null = null;
  const controls = new Map<string, HTMLInputElement>();
  const invalidate = () => { epoch++; pending = false; checkpointPending = false; mergePending = false; proposal = null; receipt = null; mergeReceipt = null; mergeMaintenanceRequired = false; errorMessage = ""; };
  const renderRows = () => {
    if (!catalog || !list || !pager || !search) return;
    const rows = catalog.sandbox.filter(entry => entry.path.includes(query));
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE)); page = Math.max(1, Math.min(pages, page));
    const visible = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    const stableByPath = new Map(catalog.stable.map(entry => [entry.path, entry]));
    const items = visible.map(entry => {
      const li = document.createElement("li");
      let checkbox = controls.get(entry.path);
      if (!checkbox) {
        checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.setAttribute("aria-label", `选择 ${entry.path}`);
        const control = checkbox;
        control.addEventListener("change", () => { invalidate(); if (control.checked) selected.add(entry.path); else selected.delete(entry.path); renderState(); });
        controls.set(entry.path, checkbox);
      }
      checkbox.checked = selected.has(entry.path); checkbox.disabled = false;
      const label = document.createElement("label"); label.append(checkbox, text(document, "span", `${entry.path} · sandbox SHA-256=${entry.workingSha256 ?? "未知/缺失"}`));
      label.style.overflowWrap = "anywhere";
      const stableEntry = stableByPath.get(entry.path);
      const counterpart = text(document, "p", stableSummary(stableEntry));
      counterpart.style.margin = "0";
      counterpart.style.overflowWrap = "anywhere";
      const details = document.createElement("details"); details.open = false;
      details.appendChild(text(document, "summary", "完整路径/索引/模式详情"));
      const pre = text(document, "pre", JSON.stringify({ sandbox: entry, stable: stableEntry ?? { status: "not_in_inventory" } }, null, 2));
      pre.style.whiteSpace = "pre-wrap";
      pre.style.overflowWrap = "anywhere";
      pre.style.maxWidth = "100%";
      pre.style.maxHeight = "min(28vh, 280px)";
      pre.style.overflow = "auto";
      details.appendChild(pre);
      li.style.marginBottom = "0.5rem";
      li.append(label, counterpart, details); return li;
    });
    list.setAttribute("aria-label", "sandbox 路径选择"); list.replaceChildren(...items);
    const prev = text(document, "button", "上一页") as HTMLButtonElement; prev.type = "button"; prev.disabled = page <= 1; prev.addEventListener("click", () => { page--; renderRows(); });
    const next = text(document, "button", "下一页") as HTMLButtonElement; next.type = "button"; next.disabled = page >= pages; next.addEventListener("click", () => { page++; renderRows(); });
    pager.replaceChildren(prev, text(document, "span", `第 ${page}/${pages} 页 · ${rows.length} 项`), next);
  };
  const renderState = () => {
    if (!catalog || !output || !submit || !checkpoint || !merge || !status) return;
    submit.disabled = !options.isDesktop || !options.propose || pending || checkpointPending || selected.size === 0;
    const proposalValid = validProposalForSelection(proposal, catalog.digest, [...selected]);
    checkpoint.disabled = !options.isDesktop || !options.createCheckpoint || !proposalValid || pending || checkpointPending || mergePending;
    merge.hidden = !receipt; mergeMaintenanceRequired = !!receipt && maintenanceBlockedCheckpoints.has(String(receipt.checkpointDigest));
    merge.disabled = !options.isDesktop || !options.executeMerge || !receipt || mergePending || !!mergeReceipt || mergeMaintenanceRequired;
    if (selectionSummary) selectionSummary.textContent = `已选择 ${selected.size} 条路径。`;
    status.textContent = errorMessage;
    if (proposal) {
      const details = document.createElement("details"); details.open = true; details.appendChild(text(document, "summary", "完整范围提案（原始 JSON）"));
      const flags = text(document, "p", `执行=${String(proposal.execution)} · 可合入=${String(proposal.readyForMerge)} · 可启动=${String(proposal.readyForLaunch)} · checkpoint=${String(proposal.checkpointCreated)} · native consent=${String(proposal.nativeConsent)}`);
      flags.setAttribute("role", "note"); details.append(flags, text(document, "p", proposalBlockerCount(proposal)), text(document, "pre", JSON.stringify(proposal, null, 2))); output.replaceChildren(details);
    } else output.replaceChildren();
    if (mergeReceipt) status.textContent = `源码已合入（计划 SHA-256=${String(mergeReceipt.planDigest)}）。源码已合入，但未构建激活、并非可使用验收。`;
    else if (mergeMaintenanceRequired) status.textContent = "合入结果需要维护处理；不得重试同一检查点或启动候选版本。此状态不会被面板选择变化、重读或清空面板撤销。";
    else if (receipt && !errorMessage && !mergePending) status.textContent = `已保存源码检查点 SHA-256=${String(receipt.checkpointDigest)} · ${String(receipt.rawBytes)} 字节。通过项目 Git 合入将真正执行仅采用所选内容，不合入 sandbox 整段历史。原生确认期间选择变化不会撤销宿主已确认操作；当前面板会忽略旧结果。源码检查点本身不代表已合入。`;
  };
  return {
    show(report) {
      epoch++; pending = false; checkpointPending = false; mergePending = false; selected = new Set(); proposal = null; receipt = null; mergeReceipt = null; mergeMaintenanceRequired = false; errorMessage = ""; query = ""; page = 1; catalog = null; controls.clear();
      try { catalog = validateReport(report); }
      catch (error) { container.replaceChildren(text(document, "p", `源码选择不可用：${error instanceof Error ? error.message : "报告结构无效"}`)); return; }
      const heading = text(document, "h2", `声明 sandbox 路径范围 · review SHA-256=${catalog.digest}`);
      const warning = text(document, "p", "逐路径声明仅用于生成范围提案；未验证任务归属或来源所有权。始终保留 stable 的其他内容；源码合入只会在检查点成功后由桌面可信宿主通过项目 Git 和原生确认执行，不合入 sandbox 整段历史。原生确认期间若编辑面板，宿主已确认的操作不会因此撤销，面板只忽略旧请求结果。不会启动/激活，也不会调用模型。"); warning.setAttribute("role", "note");
      selectionSummary = text(document, "p", `已选择 ${selected.size} 条路径。`); selectionSummary.setAttribute("aria-live", "polite");
      const environmentNotice = text(document, "p", options.isDesktop ? (options.propose ? "仅桌面可信宿主可核验；仍不产生原生同意或执行权。" : "桌面提案 API 不可用，提交已禁用。") : "当前为 Web 环境，不能调用桌面可信宿主；提交已禁用。"); environmentNotice.setAttribute("role", "note");
      const searchLabel = text(document, "label", "搜索 sandbox 路径"); search = document.createElement("input"); search.type = "search"; search.setAttribute("aria-label", "搜索 sandbox 路径"); search.addEventListener("input", () => { query = search!.value; page = 1; renderRows(); }); searchLabel.appendChild(search);
      list = document.createElement("ol"); pager = document.createElement("nav"); pager.setAttribute("aria-label", "选择分页");
      submit = text(document, "button", "核验所选范围（不合入）") as HTMLButtonElement; submit.type = "button"; submit.addEventListener("click", async () => {
        if (!catalog || !options.isDesktop || !options.propose || pending || selected.size === 0) return;
        const paths = [...selected].sort(); const request = { format: 1 as const, mode: "managed_source_selection_request_v1" as const, reviewDigest: catalog.digest, selectedPaths: paths };
        const bytes = new TextEncoder().encode(JSON.stringify(request)).byteLength;
        if (paths.length > 2048 || paths.some(path => new TextEncoder().encode(path).byteLength > 512) || bytes > 256 * 1024) { errorMessage = "所选范围超过请求大小限制。"; renderState(); return; }
        const current = ++epoch; pending = true; checkpointPending = false; receipt = null; proposal = null; errorMessage = "提案生成中…"; renderRows(); renderState();
        try {
          const result = await options.propose(request);
          if (current !== epoch) return;
          pending = false;
          if (!isRecord(result) || result.ok === false && typeof result.message !== "string" || result.ok === true && !isRecord(result.proposal) || result.ok !== true && result.ok !== false) throw new TypeError("提案响应格式无效");
          if (result.ok) {
            const expectedPaths = [...paths].sort();
            if (result.proposal.mode !== "managed_source_selection_proposal_v1" || result.proposal.reviewDigest !== request.reviewDigest || !Array.isArray(result.proposal.selectedPaths) || JSON.stringify(result.proposal.selectedPaths) !== JSON.stringify(expectedPaths) || typeof result.proposal.proposal_digest !== "string" || !HASH.test(result.proposal.proposal_digest) || result.proposal.execution !== false || result.proposal.readyForMerge !== false || result.proposal.readyForLaunch !== false || result.proposal.checkpointCreated !== false || result.proposal.nativeConsent !== false) throw new TypeError("提案未绑定当前审查/所选路径，或安全标志无效");
            proposal = result.proposal; errorMessage = "";
          } else { errorMessage = result.message; }
        } catch (error) { if (current !== epoch) return; pending = false; errorMessage = error instanceof Error ? error.message : "提案请求失败。"; }
        renderRows(); renderState();
      });
      checkpoint = text(document, "button", "保存选定源码检查点（不合入）") as HTMLButtonElement; checkpoint.type = "button"; checkpoint.disabled = true; checkpoint.addEventListener("click", async () => {
        if (!catalog || !proposal || !options.isDesktop || !options.createCheckpoint || checkpointPending || !validProposalForSelection(proposal, catalog.digest, [...selected])) return;
        const paths = [...selected].sort(); const reviewDigest = catalog.digest; const proposalDigest = String(proposal.proposal_digest); const current = ++epoch; checkpointPending = true; receipt = null; errorMessage = "等待可信宿主确认源码检查点…"; renderState();
        try {
          const result = await options.createCheckpoint({ format: 1, mode: "managed_source_checkpoint_request_v1", reviewDigest, proposalDigest, selectedPaths: paths });
          if (current !== epoch) return;
          checkpointPending = false;
          if (!isRecord(result) || result.ok !== true && result.ok !== false || result.ok === false && typeof result.message !== "string" || result.ok === true && !validReceipt(result.receipt, proposal, reviewDigest, paths)) throw new TypeError("检查点回执未绑定当前审查/提案/路径或安全标志无效");
          if (result.ok) { receipt = result.receipt; mergeReceipt = null; errorMessage = ""; } else { errorMessage = result.message; }
        } catch (error) { if (current !== epoch) return; checkpointPending = false; errorMessage = error instanceof Error ? error.message : "检查点请求失败。"; }
        renderState();
      });
      merge = text(document, "button", "通过项目合入此检查点") as HTMLButtonElement; merge.type = "button"; merge.hidden = true; merge.disabled = true;
      merge.addEventListener("click", async () => {
        if (!receipt || !options.isDesktop || !options.executeMerge || mergePending || mergeReceipt) return;
        const checkpointDigest = String(receipt.checkpointDigest);
        if (maintenanceBlockedCheckpoints.has(checkpointDigest) || mergeMaintenanceRequired) return;
        const current = ++epoch; mergePending = true; errorMessage = "等待可信宿主原生确认并执行所选源码合入…"; renderState();
        try {
          const result = await options.executeMerge({ format: 1, mode: "managed_selected_merge_request_v1", checkpointDigest });
          const knownRefusal = isRecord(result) && result.ok === false && typeof result.message === "string"
            && (result.maintenanceRequired === undefined || typeof result.maintenanceRequired === "boolean");
          const verifiedSuccess = isRecord(result) && result.ok === true && validMergeReceipt(result.receipt, receipt);
          if (knownRefusal && result.maintenanceRequired === true || !knownRefusal && !verifiedSuccess) maintenanceBlockedCheckpoints.add(checkpointDigest);
          if (current !== epoch) return;
          mergePending = false;
          if (!knownRefusal && !verifiedSuccess) {
            mergeMaintenanceRequired = true;
            errorMessage = "合入结果未知或回执无效，可能已经修改项目；不得重试同一检查点或启动候选版本，请先进行维护核查。";
          } else if (verifiedSuccess) { mergeReceipt = result.receipt; errorMessage = ""; }
          else if (result.ok === false) { mergeMaintenanceRequired = result.maintenanceRequired === true; errorMessage = result.message; }
        } catch {
          maintenanceBlockedCheckpoints.add(checkpointDigest);
          if (current !== epoch) return;
          mergePending = false; mergeMaintenanceRequired = true;
          errorMessage = "合入调用结果未知，可能已经修改项目；不得重试同一检查点或启动候选版本，请先进行维护核查。";
        }
        renderState();
      });
      status = text(document, "p", ""); status.setAttribute("role", "status"); output = document.createElement("section");
      container.replaceChildren(heading, warning, selectionSummary, environmentNotice, searchLabel, list, pager, submit, checkpoint, merge, status, output); renderRows(); renderState();
    },
    clear() { epoch++; catalog = null; selected.clear(); proposal = null; receipt = null; pending = false; checkpointPending = false; mergePending = false; mergeMaintenanceRequired = false; query = ""; controls.clear(); search = null; list = null; pager = null; submit = null; checkpoint = null; merge = null; status = null; output = null; selectionSummary = null; container.replaceChildren(); },
  };
}










