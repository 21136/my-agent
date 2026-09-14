/** Compact confirm card — headline + reason + optional detail (UX-030). */

export type ConfirmPreviewView = {
  headline: string;
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
    return {
      headline,
      reason: reasonParts.join(" · "),
      detail: text,
      compact: true,
    };
  }

  const compact = text.length > 240 || lines.some((l) => l.length > 180);
  const headline = lines[0]?.slice(0, 120) ?? "工具确认";
  return {
    headline,
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
    return `<div class="unified-surface unified-confirm resolved is-compact">
      <div class="unified-confirm-headline">${escapeHtml(view.headline)}</div>
      <div class="text-muted unified-confirm-status">${escapeHtml(opts.resolvedStatus)}</div>
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

  return `<div class="unified-surface unified-confirm is-compact-pending" role="region" aria-label="工具确认">
    <div class="unified-confirm-kicker">需要确认</div>
    <div class="unified-confirm-headline">${escapeHtml(view.headline)}</div>
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
