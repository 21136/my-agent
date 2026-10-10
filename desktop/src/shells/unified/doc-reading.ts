import type { ProjectDocItem } from "../../api/ws";
import { escapeHtml } from "../chat-state";

/** Pipeline order: 说明 → 范围 → 方案 → 技术 → 任务 → 验收 → 环境 → 发布 */
export const STANDARD_DOC_ORDER = [
  "PROJECT.md",
  "SCOPE.md",
  "DESIGN.md",
  "TECH-DESIGN.md",
  "TASKS.md",
  "VERIFY.md",
  "ENV.md",
  "RELEASE.md",
] as const;

export const STANDARD_DOC_TITLES: Record<string, string> = {
  "PROJECT.md": "项目说明",
  "SCOPE.md": "范围",
  "DESIGN.md": "方案",
  "TECH-DESIGN.md": "技术要点",
  "TASKS.md": "任务",
  "VERIFY.md": "验收",
  "ENV.md": "环境与质量",
  "RELEASE.md": "发布",
};

export type DocOutlineItem = {
  id: string;
  level: number;
  text: string;
};

export type DocViewMode = "preview" | "edit";

export type PopupMenuItem =
  | { kind: "sep" }
  | { kind: "item"; id: string; label: string; danger?: boolean; disabled?: boolean };

const TITLE_ACRONYMS = new Set(["ux", "ui", "api", "toc", "id", "ai", "cli", "ws", "html", "css"]);

export function docBasename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const slash = normalized.lastIndexOf("/");
  return slash >= 0 ? normalized.slice(slash + 1) : normalized;
}

export function docStem(path: string): string {
  const name = docBasename(path);
  return name.toLowerCase().endsWith(".md") ? name.slice(0, -3) : name;
}

export function isPipelineDoc(path: string, isStandard?: boolean): boolean {
  if (isStandard) return true;
  return Object.prototype.hasOwnProperty.call(STANDARD_DOC_TITLES, docBasename(path));
}

export function findProjectDoc(docs: ProjectDocItem[], path: string): ProjectDocItem | undefined {
  return docs.find((doc) => doc.path === path);
}

export function deleteConfirmCopy(doc: { path: string; name?: string; is_standard?: boolean }): {
  title: string;
  body: string;
  confirmLabel: string;
} {
  const title = humanDocTitle(doc.path, doc.name);
  const file = docBasename(doc.path);
  if (isPipelineDoc(doc.path, doc.is_standard)) {
    return {
      title: `删除「${title}」`,
      body: `这是项目标准文档（${file}）。将移入回收站，可撤销；流程可能暂时不完整。确定删除？`,
      confirmLabel: "移入回收站",
    };
  }
  return {
    title: `删除「${title}」`,
    body: `将移入回收站，稍后可撤销。确定删除？`,
    confirmLabel: "移入回收站",
  };
}

export function renameDraftForDoc(path: string, name?: string): string {
  const mapped = STANDARD_DOC_TITLES[docBasename(path)];
  if (mapped) return mapped;
  return docStem(path) || name || "";
}

export function standardRenameWarning(path: string): string {
  if (!isPipelineDoc(path)) return "";
  const file = docBasename(path);
  const title = STANDARD_DOC_TITLES[file] || file;
  return `「${title}」是标准文档（${file}）。重命名后流程可能找不到它。`;
}

export function formatDocMtime(mtime?: number): string {
  if (!mtime || !Number.isFinite(mtime) || mtime <= 0) return "";
  const ms = mtime < 1e12 ? mtime * 1000 : mtime;
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "";
  const now = Date.now();
  const diff = Math.max(0, now - d.getTime());
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (y === new Date().getFullYear()) return `${m}-${day} ${hh}:${mm}`;
  return `${y}-${m}-${day}`;
}

export function docFolderOf(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const slash = normalized.lastIndexOf("/");
  return slash >= 0 ? normalized.slice(0, slash) : "";
}

export function filterProjectDocs(
  docs: ProjectDocItem[],
  query: string,
): ProjectDocItem[] {
  const q = query.trim().toLowerCase();
  const sorted = sortProjectDocs(docs);
  if (!q) return sorted;
  return sorted.filter((doc) => {
    const title = humanDocTitle(doc.path, doc.name).toLowerCase();
    return title.includes(q) || doc.path.toLowerCase().includes(q);
  });
}

export function groupDocsByFolder(docs: ProjectDocItem[]): Array<{ folder: string; docs: ProjectDocItem[] }> {
  const map = new Map<string, ProjectDocItem[]>();
  for (const doc of docs) {
    const folder = docFolderOf(doc.path);
    const list = map.get(folder) || [];
    list.push(doc);
    map.set(folder, list);
  }
  const folders = Array.from(map.keys()).sort((a, b) => {
    if (!a) return -1;
    if (!b) return 1;
    return a.localeCompare(b, "zh");
  });
  return folders.map((folder) => ({ folder, docs: map.get(folder) || [] }));
}

export function docCatalogMenuItems(): PopupMenuItem[] {
  return [
    { kind: "item", id: "rename", label: "重命名" },
    { kind: "sep" },
    { kind: "item", id: "delete", label: "删除", danger: true },
  ];
}

export function prettifyDocName(name: string): string {
  const file = docBasename(name);
  const base = file.replace(/\.[a-z0-9]+$/i, "").replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim();
  if (!base) return "未命名文档";
  if (!/^[\x00-\x7F]+$/.test(base)) return base;
  return base.split(" ").map((word) => {
    const lower = word.toLowerCase();
    if (TITLE_ACRONYMS.has(lower)) return lower.toUpperCase();
    return lower.charAt(0).toUpperCase() + lower.slice(1);
  }).join(" ");
}

export function firstMarkdownHeading(content: string): string {
  return extractDocOutline(content)[0]?.text || "";
}

export function humanDocTitle(path: string, name?: string, content?: string): string {
  const file = docBasename(path);
  const mapped = STANDARD_DOC_TITLES[file];
  if (mapped) return mapped;
  if (content) {
    const heading = firstMarkdownHeading(content);
    if (heading) return heading;
  }
  return prettifyDocName(name || file);
}

export function displayDocTitle(
  path: string,
  name: string | undefined,
  content: string,
  outline: DocOutlineItem[] = [],
): string {
  const title = humanDocTitle(path, name, content);
  if (STANDARD_DOC_TITLES[docBasename(path)]) return title;
  if (outline[0]?.text && title === prettifyDocName(name || path)) return outline[0].text;
  return title;
}

const INTERNAL_DOC_RE = /(^|\/)(\.plan-agent|\.agent)(\/|$)|(^|\/)RUNAWAY-PROGRESS\.md$/;

/** Agent bookkeeping is not a user document, even if a listing still contains it. */
export function isInternalProjectDoc(path: string): boolean {
  return INTERNAL_DOC_RE.test(path.replace(/\\/g, "/"));
}

export function sortProjectDocs(docs: ProjectDocItem[]): ProjectDocItem[] {
  const rank = new Map<string, number>(STANDARD_DOC_ORDER.map((file, index) => [file, index]));
  const visible = docs.filter((doc) => !isInternalProjectDoc(doc.path));
  return visible.sort((a, b) => {
    const aKey = docBasename(a.path);
    const bKey = docBasename(b.path);
    const aRank = rank.get(aKey);
    const bRank = rank.get(bKey);
    if (aRank !== undefined && bRank !== undefined) return aRank - bRank;
    if (aRank !== undefined) return -1;
    if (bRank !== undefined) return 1;
    return humanDocTitle(a.path, a.name).localeCompare(humanDocTitle(b.path, b.name), "zh");
  });
}

export function normalizeNewDocPath(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return "";
  if (/\.[a-z0-9]+$/i.test(trimmed)) return trimmed;
  return `${trimmed}.md`;
}

export function extractDocOutline(markdown: string): DocOutlineItem[] {
  const structured = extractStructuredOutline(markdown);
  if (structured.length) return structured;
  return extractLooseMarkdownTitles(markdown);
}

export function applyHeadingIds(root: ParentNode, outline: DocOutlineItem[]): void {
  const headings = Array.from(root.querySelectorAll("h1, h2, h3, h4, h5, h6"));
  outline.forEach((item, index) => {
    const heading = headings[index];
    if (heading) heading.id = item.id;
  });
}

export function extractOutlineFromRendered(root: ParentNode): DocOutlineItem[] {
  const used = new Set<string>();
  const items: DocOutlineItem[] = [];
  for (const el of Array.from(root.querySelectorAll("h1, h2, h3"))) {
    const text = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!text) continue;
    const id = el.id || slugifyHeading(text, used);
    el.id = id;
    used.add(id);
    items.push({ id, level: Number(el.tagName[1]) || 2, text });
  }
  return items;
}

export function extractLooseOutlineFromRendered(root: ParentNode): DocOutlineItem[] {
  const used = new Set<string>();
  const items: DocOutlineItem[] = [];
  for (const el of Array.from(root.querySelectorAll("p, li"))) {
    const text = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!isLooseTitle(text)) continue;
    const strongChild = el.firstElementChild;
    const strongOnly = Boolean(
      strongChild
      && (strongChild.tagName === "STRONG" || strongChild.tagName === "B")
      && (strongChild.textContent || "").replace(/\s+/g, " ").trim() === text,
    );
    const numbered = isNumberedTitle(text);
    if (!strongOnly && !numbered) continue;
    const id = el.id || slugifyHeading(text, used);
    el.id = id;
    used.add(id);
    items.push({ id, level: numbered ? 2 : 1, text });
    if (items.length >= 24) break;
  }
  return items;
}

export function finalizeDocumentOutline(root: ParentNode, markdown: string): DocOutlineItem[] {
  const structured = extractStructuredOutline(markdown);
  if (structured.length) {
    applyHeadingIds(root, structured);
    return structured;
  }
  const rendered = extractOutlineFromRendered(root);
  if (rendered.length) return rendered;
  return extractLooseOutlineFromRendered(root);
}

export function renderDocCatalogHtml(
  docs: ProjectDocItem[],
  currentPath: string,
  options: {
    newDocName?: string;
    newDocFolder?: string;
    currentContent?: string;
    inputId: string;
    createAction: string;
    renamingDocPath?: string;
    renameDraft?: string;
    deleteConfirmPath?: string;
    searchQuery?: string;
    searchInputId?: string;
    recentlyChanged?: Set<string> | string[];
    placeholderCount?: number;
    showFolderField?: boolean;
  } = {
    inputId: "overlay-new-doc-input",
    createAction: "overlay-new-doc",
  },
): string {
  const newValue = options.newDocName || "";
  const folderValue = options.newDocFolder || "";
  const searchValue = options.searchQuery || "";
  const searchId = options.searchInputId || "doc-catalog-search";
  const recent = options.recentlyChanged instanceof Set
    ? options.recentlyChanged
    : new Set(options.recentlyChanged || []);
  let html = `<div class="doc-catalog-toolbar">
    <input type="search" class="overlay-search-input doc-catalog-search" id="${escapeHtml(searchId)}" placeholder="搜索文档…" value="${escapeHtml(searchValue)}" aria-label="搜索文档">
  </div>`;
  html += `<div class="doc-catalog-new">
    <input type="text" class="overlay-search-input doc-catalog-new-input" id="${escapeHtml(options.inputId)}" placeholder="文档标题，例如：需求分析" value="${escapeHtml(newValue)}">
    ${options.showFolderField
      ? `<input type="text" class="overlay-search-input doc-catalog-folder-input" id="${escapeHtml(options.inputId)}-folder" placeholder="文件夹（可选）" value="${escapeHtml(folderValue)}" aria-label="新建文档文件夹">`
      : ""}
    <button type="button" class="unified-btn unified-btn-accent doc-catalog-new-btn" data-action="${escapeHtml(options.createAction)}">新建</button>
    <button type="button" class="unified-btn doc-catalog-ai-btn" data-action="doc-ai-draft" title="在聊天里让 AI 起草">让 AI 起草</button>
  </div>`;
  if (options.deleteConfirmPath) {
    const doc = findProjectDoc(docs, options.deleteConfirmPath);
    const copy = deleteConfirmCopy({
      path: options.deleteConfirmPath,
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
  const filtered = filterProjectDocs(docs, searchValue);
  if (!filtered.length) {
    html += `<p class="doc-catalog-empty">${searchValue ? "没有匹配的文档。" : "还没有文档。输入标题后点新建。"}</p>`;
    if ((options.placeholderCount || 0) > 0) {
      html += `<p class="doc-catalog-placeholder-hint">未填写模板 ${options.placeholderCount} 篇（已折叠）</p>`;
    }
    return html;
  }
  const groups = groupDocsByFolder(filtered);
  html += `<nav class="doc-catalog-list" aria-label="文档目录">`;
  for (const group of groups) {
    if (group.folder) {
      html += `<div class="doc-catalog-folder-label">${escapeHtml(group.folder)}/</div>`;
    } else if (groups.length > 1) {
      html += `<div class="doc-catalog-folder-label">项目根目录</div>`;
    }
    for (const doc of group.docs) {
      const content = doc.path === currentPath ? options.currentContent : undefined;
      const title = humanDocTitle(doc.path, doc.name, content);
      const active = doc.path === currentPath ? " is-current" : "";
      const recentCls = recent.has(doc.path) ? " is-recent" : "";
      const renaming = options.renamingDocPath === doc.path;
      const mtimeLabel = formatDocMtime(doc.mtime);
      if (renaming) {
        const warn = standardRenameWarning(doc.path);
        html += `<div class="doc-catalog-item overlay-doc-item is-renaming${active}" data-doc-path="${escapeHtml(doc.path)}">
          <input type="text" class="overlay-doc-rename-input" id="overlay-doc-rename-input" value="${escapeHtml(options.renameDraft || "")}" aria-label="重命名文档">
          ${warn ? `<div class="doc-catalog-rename-warn">${escapeHtml(warn)}</div>` : ""}
        </div>`;
        continue;
      }
      html += `<div class="doc-catalog-item overlay-doc-item${active}${recentCls}" role="button" tabindex="0" data-doc-path="${escapeHtml(doc.path)}" title="${escapeHtml(doc.path)}">
        <span class="doc-catalog-item-copy">
          <span class="doc-catalog-item-title">${escapeHtml(title)}${recent.has(doc.path) ? '<span class="doc-catalog-recent-badge">刚更新</span>' : ""}</span>
          <span class="doc-catalog-item-meta"><span class="doc-catalog-item-path">${escapeHtml(doc.path)}</span>${mtimeLabel ? `<span class="doc-catalog-item-mtime">${escapeHtml(mtimeLabel)}</span>` : ""}</span>
        </span>
        <button type="button" class="overlay-doc-item-more" data-doc-more="${escapeHtml(doc.path)}" aria-label="文档操作" title="文档操作">•••</button>
      </div>`;
    }
  }
  html += `</nav>`;
  if ((options.placeholderCount || 0) > 0) {
    html += `<p class="doc-catalog-placeholder-hint">未填写模板 ${options.placeholderCount} 篇（已折叠）</p>`;
  }
  return html;
}

export function renderDocOutlineHtml(outline: DocOutlineItem[]): string {
  if (!outline.length) {
    return `<p class="doc-outline-empty">本页还没有可跳转的小节</p>`;
  }
  const items = outline.map((item) => (
    `<button type="button" class="doc-outline-item is-h${item.level}" data-action="doc-outline-jump" data-heading-id="${escapeHtml(item.id)}" title="${escapeHtml(item.text)}">${escapeHtml(item.text)}</button>`
  )).join("");
  return `<nav class="doc-outline-list" aria-label="本页目录">${items}</nav>`;
}

export function renderDocumentReaderHtml(args: {
  docs: ProjectDocItem[];
  currentPath: string;
  currentContent: string;
  renderedHtml: string;
  newDocName: string;
  includeCatalog?: boolean;
  includeBack?: boolean;
  view?: DocViewMode;
  draft?: string;
  dirty?: boolean;
  saving?: boolean;
  leavePrompt?: boolean;
  conflictPrompt?: boolean;
  splitEditPreview?: boolean;
  docsChatSplit?: boolean;
  recentlyChanged?: Set<string> | string[];
  placeholderCount?: number;
  searchQuery?: string;
  newDocFolder?: string;
}): { html: string; outline: DocOutlineItem[] } {
  const view = args.view || "preview";
  const outlineSource = view === "edit" ? (args.draft ?? args.currentContent) : args.currentContent;
  const outline = extractDocOutline(outlineSource);
  const includeCatalog = args.includeCatalog !== false;
  const includeBack = Boolean(args.includeBack);
  const title = args.currentPath
    ? humanDocTitle(args.currentPath, docBasename(args.currentPath), args.currentContent)
    : "选择一篇文档";
  const pathLine = args.currentPath
    ? `<div class="unified-document-path" title="${escapeHtml(args.currentPath)}">${escapeHtml(args.currentPath)}</div>`
    : "";
  const saveDisabled = !args.dirty || args.saving;
  const leaveBanner = args.leavePrompt
    ? `<div class="unified-document-unsaved" role="status">
        <span>有未保存的更改。保存后再离开，还是放弃更改？</span>
        <div class="unified-document-unsaved-actions">
          <button type="button" class="unified-btn unified-btn-accent" data-action="document-save-leave">保存并离开</button>
          <button type="button" class="unified-btn unified-btn-danger" data-action="document-discard-leave">放弃更改</button>
          <button type="button" class="unified-btn" data-action="document-cancel-leave">取消</button>
        </div>
      </div>`
    : "";
  const conflictBanner = args.conflictPrompt
    ? `<div class="unified-document-conflict" role="status">
        <span>文档已被修改（可能是助手刚改过）。重新载入，还是仍要覆盖？</span>
        <div class="unified-document-unsaved-actions">
          <button type="button" class="unified-btn unified-btn-accent" data-action="document-conflict-reload">重新载入</button>
          <button type="button" class="unified-btn unified-btn-danger" data-action="document-conflict-overwrite">仍要覆盖</button>
          <button type="button" class="unified-btn" data-action="document-conflict-cancel">取消</button>
        </div>
      </div>`
    : "";
  const toolbar = args.currentPath && view === "edit"
    ? `<div class="unified-document-md-toolbar" role="toolbar" aria-label="编辑工具">
        <button type="button" class="unified-btn" data-action="doc-md-heading" title="标题">H</button>
        <button type="button" class="unified-btn" data-action="doc-md-bold" title="加粗"><strong>B</strong></button>
        <button type="button" class="unified-btn" data-action="doc-md-list" title="列表">• 列表</button>
        <button type="button" class="unified-btn" data-action="doc-md-code" title="代码">代码</button>
        <button type="button" class="unified-btn" data-action="doc-md-link" title="链接">链接</button>
        <button type="button" class="unified-btn" data-action="doc-selection-to-ai" title="把选中文字交给 AI">交给 AI</button>
      </div>`
    : (args.currentPath && view === "preview"
      ? `<div class="unified-document-md-toolbar" role="toolbar" aria-label="阅读工具">
          <button type="button" class="unified-btn" data-action="doc-selection-to-ai" title="把选中文字交给 AI">交给 AI</button>
        </div>`
      : "");
  const split = Boolean(args.splitEditPreview) && view === "edit" && Boolean(args.currentPath);
  const editorPane = view === "edit"
    ? `<textarea class="unified-document-editor" spellcheck="false" aria-label="文档正文">${escapeHtml(args.draft || "")}</textarea>`
    : "";
  const previewPane = (view === "preview" || split)
    ? `<div class="unified-document-content unified-markdown">${
      args.currentPath
        ? ((split ? (args.draft || args.currentContent) : args.currentContent)
          ? (split ? "" : args.renderedHtml)  /* split preview filled by caller live */
          : `<p class="doc-reader-empty">正在打开…</p>`)
        : `<p class="doc-reader-empty">从左侧打开一篇文档，在这里阅读全文。</p>`
    }</div>`
    : "";
  // For split mode, show textarea + live preview side by side
  const editor = split
    ? `<div class="unified-document-split-edit">
        <div class="unified-document-split-pane is-editor">${editorPane}</div>
        <div class="unified-document-split-pane is-preview"><div class="unified-document-content unified-markdown" data-live-preview="1">${args.renderedHtml || `<p class="doc-reader-empty">开始编辑以预览</p>`}</div></div>
      </div>`
    : (view === "edit" ? editorPane : previewPane);
  const back = includeBack
    ? `<button type="button" class="unified-btn" data-action="document-back">← 返回聊天</button>`
    : "";
  const splitToggle = args.currentPath
    ? `<button type="button" class="unified-btn" data-action="toggle-docs-chat-split" title="文档与聊天并排">${args.docsChatSplit ? "仅文档" : "与聊天并排"}</button>`
    : "";
  const editActions = args.currentPath && view === "edit"
    ? `<button type="button" class="unified-btn unified-btn-accent" data-action="document-save"${saveDisabled ? " disabled" : ""}>${args.saving ? "保存中…" : "保存"}</button>
       <button type="button" class="unified-btn" data-action="document-discard"${args.dirty ? "" : " disabled"}>放弃更改</button>`
    : "";
  const viewToggle = args.currentPath
    ? `<div class="unified-segmented" role="group" aria-label="文档视图">
        <button type="button" class="unified-segmented-btn${view === "preview" ? " is-active" : ""}" data-action="document-preview">预览</button>
        <button type="button" class="unified-segmented-btn${view === "edit" ? " is-active" : ""}" data-action="document-edit">编辑</button>
      </div>`
    : "";
  const catalog = includeCatalog
    ? `<aside class="unified-document-catalog">${renderDocCatalogHtml(args.docs, args.currentPath, {
      newDocName: args.newDocName,
      newDocFolder: args.newDocFolder,
      currentContent: args.currentContent,
      inputId: "doc-reader-new-input",
      createAction: "create-doc",
      searchQuery: args.searchQuery,
      recentlyChanged: args.recentlyChanged,
      placeholderCount: args.placeholderCount,
      showFolderField: true,
    })}</aside>`
    : "";
  const html = `<div class="unified-document-inner${args.dirty ? " is-dirty" : ""}">
    <header class="unified-document-header">
      ${back}
      <div class="unified-document-heading">
        <div class="unified-document-kicker">项目文档${args.dirty ? " · 未保存" : ""}</div>
        <h1>${escapeHtml(title)}</h1>
        ${pathLine}
      </div>
      <div class="unified-document-actions">
        ${splitToggle}
        ${viewToggle}
        ${editActions}
      </div>
    </header>
    ${leaveBanner}
    ${conflictBanner}
    ${toolbar}
    <div class="unified-document-shell${includeCatalog ? "" : " is-sidebar-catalog"}">
      ${catalog}
      <article class="unified-document-reader">
        ${editor}
      </article>
      <aside class="unified-document-outline">
        <div class="doc-outline-label">本页目录</div>
        <div class="doc-outline-body">${renderDocOutlineHtml(outline)}</div>
      </aside>
    </div>
  </div>`;
  return { html, outline };
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

function extractStructuredOutline(markdown: string): DocOutlineItem[] {
  if (!markdown.trim()) return [];
  const items: DocOutlineItem[] = [];
  const used = new Set<string>();
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  let inFence = false;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const atx = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (atx) {
      pushOutlineItem(items, used, atx[1].length, atx[2]);
      continue;
    }
    const underline = /^(=+|-+)\s*$/.exec(line);
    const previous = index > 0 ? lines[index - 1].trim() : "";
    if (underline && previous && !/^#/.test(previous) && !/^\s*```/.test(previous)) {
      pushOutlineItem(items, used, line.trim().startsWith("=") ? 1 : 2, previous);
    }
  }
  return items;
}

function extractLooseMarkdownTitles(markdown: string): DocOutlineItem[] {
  if (!markdown.trim()) return [];
  const items: DocOutlineItem[] = [];
  const used = new Set<string>();
  let inFence = false;
  for (const rawLine of markdown.replace(/\r\n/g, "\n").split("\n")) {
    if (/^\s*```/.test(rawLine)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const line = rawLine.trim();
    const bold = /^\*\*(.+?)\*\*$/.exec(line) || /^__(.+?)__$/.exec(line);
    if (bold && isLooseTitle(bold[1])) {
      pushOutlineItem(items, used, 1, bold[1]);
      continue;
    }
    const numbered = /^(?:\d+[\.、．\)]\s+)(.+)$/.exec(line);
    if (numbered && isLooseTitle(numbered[1])) {
      pushOutlineItem(items, used, 2, numbered[1]);
    }
    if (items.length >= 24) break;
  }
  return items;
}

function pushOutlineItem(items: DocOutlineItem[], used: Set<string>, level: number, raw: string): void {
  const text = stripMarkdownInline(raw);
  if (!text) return;
  items.push({ id: slugifyHeading(text, used), level, text });
}

function isLooseTitle(text: string): boolean {
  const compact = text.replace(/\s+/g, " ").trim();
  if (compact.length < 2 || compact.length > 48) return false;
  if (/[.。:：]$/.test(compact) && compact.length > 28) return false;
  return true;
}

function isNumberedTitle(text: string): boolean {
  return /^\d+[\.、．\)]\s+\S/.test(text);
}

function stripMarkdownInline(text: string): string {
  return text
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`~]/g, "")
    .replace(/<[^>]+>/g, "")
    .trim();
}

function slugifyHeading(text: string, used: Set<string>): string {
  let base = text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 48);
  if (!base) base = "section";
  let id = base;
  let serial = 2;
  while (used.has(id)) {
    id = `${base}-${serial++}`;
  }
  used.add(id);
  return id;
}
