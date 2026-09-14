import type { ProjectDocItem } from "../../api/ws";
import { escapeHtml } from "../chat-state";

export const PIPELINE_DOC_TITLES: Record<string, string> = {
  "PROJECT.md": "项目说明",
  "SCOPE.md": "范围",
  "DESIGN.md": "方案",
  "TECH-DESIGN.md": "技术要点",
  "TASKS.md": "任务",
  "VERIFY.md": "验收",
  "ENV.md": "环境与质量",
  "RELEASE.md": "发布",
  "MAP.md": "地图",
};

export type DocViewMode = "preview" | "edit";

export type DocsCatalogModel = {
  projectDocs: ProjectDocItem[];
  currentDocPath: string;
  newDocName: string;
  renamingDocPath: string;
  renameDraft: string;
  deleteConfirmPath: string;
};

export type PopupMenuItem =
  | { kind: "sep" }
  | { kind: "item"; id: string; label: string; danger?: boolean; disabled?: boolean };

export function docFileName(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const slash = normalized.lastIndexOf("/");
  return slash >= 0 ? normalized.slice(slash + 1) : normalized;
}

export function docStem(path: string): string {
  const name = docFileName(path);
  return name.toLowerCase().endsWith(".md") ? name.slice(0, -3) : name;
}

export function docDisplayTitle(path: string, fallbackName?: string): string {
  const name = fallbackName || docFileName(path);
  return PIPELINE_DOC_TITLES[name] || PIPELINE_DOC_TITLES[path] || docStem(path) || name;
}

export function isPipelineDoc(path: string, isStandard?: boolean): boolean {
  if (isStandard) return true;
  const name = docFileName(path);
  return Object.prototype.hasOwnProperty.call(PIPELINE_DOC_TITLES, name);
}

export function findProjectDoc(docs: ProjectDocItem[], path: string): ProjectDocItem | undefined {
  return docs.find((doc) => doc.path === path);
}

export function deleteConfirmCopy(doc: { path: string; name?: string; is_standard?: boolean }): {
  title: string;
  body: string;
  confirmLabel: string;
} {
  const title = docDisplayTitle(doc.path, doc.name);
  const file = docFileName(doc.path);
  if (isPipelineDoc(doc.path, doc.is_standard)) {
    return {
      title: `删除「${title}」`,
      body: `这是项目标准文档（${file}）。删除后无法恢复，项目流程也可能不完整。确定永久删除？`,
      confirmLabel: "永久删除",
    };
  }
  return {
    title: `删除「${title}」`,
    body: `将永久删除这份文档，无法恢复。确定删除？`,
    confirmLabel: "删除",
  };
}

export function renderDocsCatalog(state: DocsCatalogModel): string {
  let html = `<div class="docs-catalog-toolbar">
    <input type="text" class="overlay-search-input" id="overlay-new-doc-input" placeholder="新建文档（如 需求分析）…" value="${escapeHtml(state.newDocName)}">
    <button type="button" class="unified-btn unified-btn-accent" id="overlay-new-doc-btn">新建</button>
  </div>`;

  if (state.deleteConfirmPath) {
    const doc = findProjectDoc(state.projectDocs, state.deleteConfirmPath);
    const copy = deleteConfirmCopy({
      path: state.deleteConfirmPath,
      name: doc?.name,
      is_standard: doc?.is_standard,
    });
    html += `<div class="overlay-switch-confirm" id="overlay-doc-delete-confirm">
      <div class="overlay-switch-confirm-title">${escapeHtml(copy.title)}</div>
      <div class="overlay-switch-confirm-text">${escapeHtml(copy.body)}</div>
      <div class="overlay-switch-confirm-actions">
        <button type="button" class="unified-btn unified-btn-danger" data-action="doc-delete-confirm">${escapeHtml(copy.confirmLabel)}</button>
        <button type="button" class="unified-btn" data-action="doc-delete-cancel">取消</button>
      </div>
    </div>`;
    return html;
  }

  if (!state.projectDocs.length) {
    html += `<p class="overlay-empty">暂无文档</p>`;
    return html;
  }

  const sorted = [...state.projectDocs].sort((a, b) => {
    if (a.is_standard && !b.is_standard) return -1;
    if (!a.is_standard && b.is_standard) return 1;
    return docDisplayTitle(a.path, a.name).localeCompare(docDisplayTitle(b.path, b.name), "zh");
  });

  html += `<div class="docs-catalog-label">项目文档</div>`;
  for (const doc of sorted) {
    const title = docDisplayTitle(doc.path, doc.name);
    const active = doc.path === state.currentDocPath ? " is-current" : "";
    const renaming = state.renamingDocPath === doc.path;
    const showPath = doc.name !== doc.path || title !== doc.name;
    const sub = showPath
      ? `<span class="overlay-doc-item-path">${escapeHtml(doc.path)}</span>`
      : "";
    const icon = doc.is_standard ? "📋" : "📄";
    if (renaming) {
      html += `<div class="overlay-doc-item is-renaming${active}" data-doc-path="${escapeHtml(doc.path)}" data-doc-standard="${doc.is_standard ? "1" : "0"}">
        <span class="overlay-doc-item-icon">${icon}</span>
        <input type="text" class="overlay-doc-rename-input" id="overlay-doc-rename-input" value="${escapeHtml(state.renameDraft)}" aria-label="重命名文档">
      </div>`;
      continue;
    }
    html += `<div class="overlay-doc-item${active}" role="button" tabindex="0" data-doc-path="${escapeHtml(doc.path)}" data-doc-standard="${doc.is_standard ? "1" : "0"}">
      <span class="overlay-doc-item-icon">${icon}</span>
      <span class="overlay-doc-item-main">
        <span class="overlay-doc-item-title">${escapeHtml(title)}</span>
        ${sub}
      </span>
      <span class="overlay-doc-item-hint">主区阅读</span>
      <button type="button" class="overlay-doc-item-more" data-doc-more="${escapeHtml(doc.path)}" aria-label="文档操作" title="文档操作">•••</button>
    </div>`;
  }

  return html;
}

export function renderDocumentPaneHtml(opts: {
  path: string;
  title: string;
  view: DocViewMode;
  previewHtml: string;
  draft: string;
  dirty: boolean;
  saving: boolean;
  leavePrompt: boolean;
}): string {
  const pathLabel = opts.path ? opts.path : "文档";
  const saveDisabled = !opts.dirty || opts.saving;
  const leaveBanner = opts.leavePrompt
    ? `<div class="unified-document-unsaved" role="status">
        <span>有未保存的更改。保存后再离开，还是放弃更改？</span>
        <div class="unified-document-unsaved-actions">
          <button type="button" class="unified-btn unified-btn-accent" data-action="document-save-leave">保存并离开</button>
          <button type="button" class="unified-btn unified-btn-danger" data-action="document-discard-leave">放弃更改</button>
          <button type="button" class="unified-btn" data-action="document-cancel-leave">取消</button>
        </div>
      </div>`
    : "";
  const editor = opts.view === "edit"
    ? `<textarea class="unified-document-editor" spellcheck="false" aria-label="文档正文">${escapeHtml(opts.draft)}</textarea>`
    : `<article class="unified-document-content unified-markdown">${opts.previewHtml}</article>`;
  const editActions = opts.view === "edit"
    ? `<button type="button" class="unified-btn unified-btn-accent" data-action="document-save"${saveDisabled ? " disabled" : ""}>${opts.saving ? "保存中…" : "保存"}</button>
       <button type="button" class="unified-btn" data-action="document-discard"${opts.dirty ? "" : " disabled"}>放弃更改</button>`
    : "";

  return `<div class="unified-document-inner${opts.dirty ? " is-dirty" : ""}">
      <header class="unified-document-header">
        <button type="button" class="unified-btn" data-action="document-back">← 返回聊天</button>
        <div class="unified-document-heading">
          <div class="unified-document-kicker">项目文档${opts.dirty ? " · 未保存" : ""}</div>
          <h1 title="${escapeHtml(pathLabel)}">${escapeHtml(opts.title || pathLabel)}</h1>
        </div>
        <div class="unified-document-actions">
          <div class="unified-segmented" role="group" aria-label="文档视图">
            <button type="button" class="unified-segmented-btn${opts.view === "preview" ? " is-active" : ""}" data-action="document-preview">预览</button>
            <button type="button" class="unified-segmented-btn${opts.view === "edit" ? " is-active" : ""}" data-action="document-edit">编辑</button>
          </div>
          ${editActions}
          <button type="button" class="unified-btn" data-action="document-list">文档列表</button>
        </div>
      </header>
      ${leaveBanner}
      ${editor}
    </div>`;
}

export function mountPopupMenu(opts: {
  x: number;
  y: number;
  items: PopupMenuItem[];
  onSelect: (id: string) => void;
}): () => void {
  const existing = document.querySelector(".context-menu[data-doc-menu]");
  existing?.remove();

  const menu = document.createElement("div");
  menu.className = "context-menu";
  menu.dataset.docMenu = "1";
  menu.style.left = `${opts.x}px`;
  menu.style.top = `${opts.y}px`;

  for (const item of opts.items) {
    if (item.kind === "sep") {
      const sep = document.createElement("div");
      sep.className = "context-menu-sep";
      menu.appendChild(sep);
      continue;
    }
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `context-menu-item${item.danger ? " is-danger" : ""}`;
    btn.textContent = item.label;
    btn.dataset.action = item.id;
    if (item.disabled) btn.disabled = true;
    menu.appendChild(btn);
  }

  const destroy = () => {
    menu.remove();
    document.removeEventListener("click", onDocClick);
    document.removeEventListener("contextmenu", onDocClick);
    document.removeEventListener("keydown", onKey);
  };

  const onDocClick = (ev: Event) => {
    if (menu.contains(ev.target as Node)) return;
    destroy();
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") destroy();
  };

  menu.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".context-menu-item");
    if (!btn?.dataset.action || btn.disabled) return;
    const id = btn.dataset.action;
    destroy();
    opts.onSelect(id);
  });

  document.body.appendChild(menu);
  const rect = menu.getBoundingClientRect();
  let left = opts.x;
  let top = opts.y;
  if (left + rect.width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - rect.width - 8);
  if (top + rect.height > window.innerHeight - 8) top = Math.max(8, window.innerHeight - rect.height - 8);
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;

  window.setTimeout(() => {
    document.addEventListener("click", onDocClick);
    document.addEventListener("contextmenu", onDocClick);
    document.addEventListener("keydown", onKey);
  }, 0);

  return destroy;
}

export function docCatalogMenuItems(): PopupMenuItem[] {
  return [
    { kind: "item", id: "rename", label: "重命名" },
    { kind: "sep" },
    { kind: "item", id: "delete", label: "删除", danger: true },
  ];
}
