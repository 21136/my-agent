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

export function sortProjectDocs(docs: ProjectDocItem[]): ProjectDocItem[] {
  const rank = new Map<string, number>(STANDARD_DOC_ORDER.map((file, index) => [file, index]));
  return [...docs].sort((a, b) => {
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
    currentContent?: string;
    inputId: string;
    createAction: string;
    renamingDocPath?: string;
    renameDraft?: string;
    deleteConfirmPath?: string;
  } = {
    inputId: "overlay-new-doc-input",
    createAction: "overlay-new-doc",
  },
): string {
  const newValue = options.newDocName || "";
  let html = `<div class="doc-catalog-new">
    <input type="text" class="overlay-search-input doc-catalog-new-input" id="${escapeHtml(options.inputId)}" placeholder="文档标题，例如：需求分析" value="${escapeHtml(newValue)}">
    <button type="button" class="unified-btn unified-btn-accent doc-catalog-new-btn" data-action="${escapeHtml(options.createAction)}">新建</button>
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
  if (!docs.length) {
    html += `<p class="doc-catalog-empty">还没有文档。输入标题后点新建。</p>`;
    return html;
  }
  html += `<nav class="doc-catalog-list" aria-label="文档目录">`;
  for (const doc of sortProjectDocs(docs)) {
    const content = doc.path === currentPath ? options.currentContent : undefined;
    const title = humanDocTitle(doc.path, doc.name, content);
    const active = doc.path === currentPath ? " is-current" : "";
    const renaming = options.renamingDocPath === doc.path;
    if (renaming) {
      html += `<div class="doc-catalog-item overlay-doc-item is-renaming${active}" data-doc-path="${escapeHtml(doc.path)}">
        <input type="text" class="overlay-doc-rename-input" id="overlay-doc-rename-input" value="${escapeHtml(options.renameDraft || "")}" aria-label="重命名文档">
      </div>`;
      continue;
    }
    html += `<div class="doc-catalog-item overlay-doc-item${active}" role="button" tabindex="0" data-doc-path="${escapeHtml(doc.path)}" title="${escapeHtml(doc.path)}">
      <span class="doc-catalog-item-copy">
        <span class="doc-catalog-item-title">${escapeHtml(title)}</span>
        <span class="doc-catalog-item-path">${escapeHtml(doc.path)}</span>
      </span>
      <button type="button" class="overlay-doc-item-more" data-doc-more="${escapeHtml(doc.path)}" aria-label="文档操作" title="文档操作">•••</button>
    </div>`;
  }
  html += `</nav>`;
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
  const editor = view === "edit"
    ? `<textarea class="unified-document-editor" spellcheck="false" aria-label="文档正文">${escapeHtml(args.draft || "")}</textarea>`
    : `<div class="unified-document-content unified-markdown">${
      args.currentPath
        ? (args.currentContent
          ? args.renderedHtml
          : `<p class="doc-reader-empty">正在打开…</p>`)
        : `<p class="doc-reader-empty">从左侧打开一篇文档，在这里阅读全文。</p>`
    }</div>`;
  const back = includeBack
    ? `<button type="button" class="unified-btn" data-action="document-back">← 返回聊天</button>`
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
      currentContent: args.currentContent,
      inputId: "doc-reader-new-input",
      createAction: "create-doc",
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
        ${viewToggle}
        ${editActions}
      </div>
    </header>
    ${leaveBanner}
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
