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

export function docBasename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  const slash = normalized.lastIndexOf("/");
  return slash >= 0 ? normalized.slice(slash + 1) : normalized;
}

export function prettifyDocName(name: string): string {
  const base = name.replace(/\.md$/i, "").replace(/[_-]+/g, " ").trim();
  if (!base) return name;
  if (/^[\x00-\x7F]+$/.test(base)) {
    return base.replace(/\b[a-zA-Z]/g, (ch) => ch.toUpperCase());
  }
  return base;
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
    return (a.name || aKey).localeCompare(b.name || bKey, "zh");
  });
}

export function extractDocOutline(markdown: string): DocOutlineItem[] {
  if (!markdown.trim()) return [];
  const items: DocOutlineItem[] = [];
  const used = new Set<string>();
  let inFence = false;
  for (const rawLine of markdown.replace(/\r\n/g, "\n").split("\n")) {
    const line = rawLine.trimEnd();
    if (/^\s*```/.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const atx = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
    if (!atx) continue;
    const text = stripMarkdownInline(atx[2]);
    if (!text) continue;
    items.push({
      id: slugifyHeading(text, used),
      level: atx[1].length,
      text,
    });
  }
  return items;
}

export function applyHeadingIds(root: ParentNode, outline: DocOutlineItem[]): void {
  const headings = Array.from(root.querySelectorAll("h1, h2, h3, h4, h5, h6"));
  outline.forEach((item, index) => {
    const heading = headings[index];
    if (heading) heading.id = item.id;
  });
}

export function renderDocCatalogHtml(
  docs: ProjectDocItem[],
  currentPath: string,
  options: { newDocName?: string; currentContent?: string; inputId: string; createAction: string } = {
    inputId: "overlay-new-doc-input",
    createAction: "overlay-new-doc",
  },
): string {
  const newValue = options.newDocName || "";
  let html = `<div class="doc-catalog-new">
    <input type="text" class="overlay-search-input doc-catalog-new-input" id="${escapeHtml(options.inputId)}" placeholder="新建文档（如 需求分析.md）…" value="${escapeHtml(newValue)}">
    <button type="button" class="unified-btn unified-btn-accent doc-catalog-new-btn" data-action="${escapeHtml(options.createAction)}">新建</button>
  </div>`;
  if (!docs.length) {
    html += `<p class="overlay-empty">暂无文档</p>`;
    return html;
  }
  html += `<div class="doc-catalog-label">项目文档</div>`;
  html += `<nav class="doc-catalog-list" aria-label="项目文档">`;
  for (const doc of sortProjectDocs(docs)) {
    const content = doc.path === currentPath ? options.currentContent : undefined;
    const title = humanDocTitle(doc.path, doc.name, content);
    const active = doc.path === currentPath ? " is-current" : "";
    html += `<button type="button" class="doc-catalog-item overlay-doc-item${active}" data-doc-path="${escapeHtml(doc.path)}" title="${escapeHtml(doc.path)}">
      <span class="doc-catalog-item-title">${escapeHtml(title)}</span>
      <span class="doc-catalog-item-path">${escapeHtml(doc.path)}</span>
    </button>`;
  }
  html += `</nav>`;
  return html;
}

export function renderDocOutlineHtml(outline: DocOutlineItem[]): string {
  if (!outline.length) {
    return `<p class="doc-outline-empty">本页暂无标题</p>`;
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
}): { html: string; outline: DocOutlineItem[] } {
  const outline = extractDocOutline(args.currentContent);
  const title = args.currentPath
    ? humanDocTitle(args.currentPath, docBasename(args.currentPath), args.currentContent)
    : "文档";
  const pathLine = args.currentPath
    ? `<div class="unified-document-path" title="${escapeHtml(args.currentPath)}">${escapeHtml(args.currentPath)}</div>`
    : "";
  const body = args.currentPath
    ? (args.currentContent
      ? args.renderedHtml
      : `<p class="overlay-empty">加载中…</p>`)
    : `<p class="overlay-empty">从左侧选择一篇文档</p>`;
  const html = `<div class="unified-document-inner">
    <header class="unified-document-header">
      <button type="button" class="unified-btn" data-action="document-back">← 返回聊天</button>
      <div class="unified-document-heading">
        <div class="unified-document-kicker">项目文档</div>
        <h1>${escapeHtml(title)}</h1>
        ${pathLine}
      </div>
      <button type="button" class="unified-btn" data-action="document-list">文档列表</button>
    </header>
    <div class="unified-document-shell">
      <aside class="unified-document-catalog">
        ${renderDocCatalogHtml(args.docs, args.currentPath, {
          newDocName: args.newDocName,
          currentContent: args.currentContent,
          inputId: "doc-reader-new-input",
          createAction: "create-doc",
        })}
      </aside>
      <article class="unified-document-reader">
        <div class="unified-document-content unified-markdown">${body}</div>
      </article>
      <aside class="unified-document-outline">
        <div class="doc-outline-label">本页目录</div>
        ${renderDocOutlineHtml(outline)}
      </aside>
    </div>
  </div>`;
  return { html, outline };
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
