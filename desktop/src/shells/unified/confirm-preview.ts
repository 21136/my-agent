/** Compact confirm card — headline + reason + optional detail (UX-030). */

export type ConfirmPreviewView = {
  headline: string;
  /** Full agent-relative path when the confirm is a file write/patch. */
  targetPath: string;
  reason: string;
  detail: string;
  compact: boolean;
};

const CONFIRM_POLICY_LABELS: Record<string, string> = {
  "confirm:plan_domain": "计划文档（PROJECT / TASKS / ENV 等）修改前需你确认",
  "confirm:sensitive": "敏感路径写入前需你确认",
  "confirm:new_file": "新建文件前需你确认",
  "confirm:outside_project": "写入路径在项目外，需你确认",
  "confirm:no_project_binding": "当前未绑定项目，需你确认",
  "confirm:empty_path": "目标路径为空，需你确认",
  "confirm:host": "托管区操作需你确认",
  "confirm:other": "该写入需你确认",
};

function humanizePolicyLine(policyLine: string): string {
  const raw = policyLine.replace(/^写入策略：/, "").trim();
  return CONFIRM_POLICY_LABELS[raw] ?? (raw.startsWith("confirm:") ? "该操作需你确认" : raw);
}

function basename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").pop() || path;
}

export function summarizeConfirmPreview(preview: string): ConfirmPreviewView {
  const text = preview.trim();
  if (!text) {
    return {
      headline: "待确认的工具操作",
      targetPath: "",
      reason: "执行前需要你同意",
      detail: "",
      compact: true,
    };
  }

  const lines = text.split(/\n/).map((l) => l.trim()).filter(Boolean);
  const writeLine = lines.find((l) => l.startsWith("写入文件：") || l.startsWith("修补文件："));
  const contentLine = lines.find((l) => l.startsWith("内容："));
  const policyLine = lines.find((l) => l.startsWith("写入策略："));

  if (writeLine) {
    const path = writeLine.replace(/^写入文件：|^修补文件：/, "").trim();
    const file = basename(path);
    const verb = writeLine.startsWith("修补") ? "修补" : "写入";
    const headline = `${verb} ${file}`;
    const reasonParts: string[] = [];
    if (policyLine) reasonParts.push(humanizePolicyLine(policyLine));
    else reasonParts.push("修改文件前需你确认");
    if (contentLine) {
      const meta = contentLine.replace("内容：", "").trim();
      if (meta) reasonParts.push(meta);
    }
    const normalizedPath = path.replace(/\\/g, "/");
    return {
      headline,
      targetPath: normalizedPath,
      reason: reasonParts.join(" · "),
      detail: text,
      compact: true,
    };
  }

  const runLine = lines.find((l) => l.startsWith("运行命令："));
  const kindLine = lines.find((l) => l.startsWith("命令类型："));
  const fullIdx = lines.findIndex((l) => l === "命令全文：");
  if (runLine) {
    const cmdHead = runLine.replace(/^运行命令：/, "").trim();
    const reasonParts: string[] = [];
    if (kindLine) {
      const kind = kindLine.replace(/^命令类型：/, "").trim();
      if (kind === "danger") reasonParts.push("高风险命令，请仔细核对");
      else if (kind) reasonParts.push(`类型：${kind}`);
    }
    reasonParts.push("执行前需要你同意");
    const detail =
      fullIdx >= 0
        ? lines.slice(fullIdx).join("\n").replace(/^命令全文：\n?/, "")
        : text;
    const long = detail.length > 400 || detail.split("\n").length > 8;
    return {
      headline: cmdHead ? `运行 ${cmdHead.slice(0, 100)}` : "运行命令",
      targetPath: "",
      reason: reasonParts.join(" · "),
      detail,
      compact: long,
    };
  }

  const compact = text.length > 240 || lines.some((l) => l.length > 180);
  const headline = lines[0]?.slice(0, 120) ?? "工具确认";
  return {
    headline,
    targetPath: "",
    reason: "执行前需要你同意",
    detail: text,
    compact,
  };
}

export function renderConfirmCardHtml(
  preview: string,
  actionsHtml: string,
  opts?: { resolvedStatus?: string },
): string {
  const view = summarizeConfirmPreview(preview);
  if (opts?.resolvedStatus) {
    return `<div class="unified-confirm resolved is-compact is-status-chip" role="status">
      <span class="unified-confirm-status-dot" aria-hidden="true"></span>
      <span class="unified-confirm-status-text">${escapeHtml(opts.resolvedStatus)}</span>
      <span class="unified-confirm-status-hint">${escapeHtml(view.headline)}</span>
    </div>`;
  }

  const detailBlock = view.compact
    ? `<details class="unified-confirm-details">
        <summary class="unified-confirm-details-toggle">查看详情</summary>
        <pre class="unified-confirm-preview">${escapeHtml(view.detail)}</pre>
      </details>`
    : `<pre class="unified-confirm-preview">${escapeHtml(view.detail)}</pre>`;

  const reasonBlock = view.reason
    ? `<p class="unified-confirm-reason">${escapeHtml(view.reason)}</p>`
    : "";

  const sandboxHint =
    view.targetPath.includes(".worktrees/sandbox")
      ? `<p class="unified-confirm-scope-hint">实验副本（sandbox）</p>`
      : "";

  const pathBlock = view.targetPath
    ? `<p class="unified-confirm-target-path" title="${escapeHtml(view.targetPath)}"><code>${escapeHtml(
        view.targetPath,
      )}</code></p>`
    : "";

  return `<div class="unified-surface unified-confirm is-compact-pending" role="region" aria-label="工具确认">
    <div class="unified-confirm-kicker">需要确认</div>
    <div class="unified-confirm-headline">${escapeHtml(view.headline)}</div>
    ${pathBlock}
    ${sandboxHint}
    ${reasonBlock}
    ${detailBlock}
    <div class="unified-confirm-actions">${actionsHtml}</div>
  </div>`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
