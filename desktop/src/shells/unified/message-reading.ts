/** Presentation policy only: never summarizes, truncates, or rewrites a reply. */
export type MessageReadingLayout = "bubble" | "reading";

export function messageReadingLayout(text: string): MessageReadingLayout {
  const source = text.trim();
  if (!source) return "bubble";
  if (
    source.length >= 260 ||
    source.split(/\r?\n/).length >= 8 ||
    source.split(/\r?\n\s*\r?\n/).length >= 5 ||
    /^ {0,3}(?:`{3,}|~{3,})/m.test(source) ||
    /^\s*\|?.+\|.+\r?\n\s*\|?\s*:?-{3,}:?\s*\|/m.test(source) ||
    /<(?:pre|table)\b/i.test(source)
  ) return "reading";
  return "bubble";
}

export function canUseReadingOpener(text: string, hasFollowingBlocks: boolean): boolean {
  const paragraph = text.trim();
  return hasFollowingBlocks && paragraph.length > 0 && paragraph.length <= 160;
}

/** Move existing nodes, preserving whitespace, markup, links, and source order.
 * Used by both eager rendering and the lazy-history upgrade path. */
export function prepareReadingBody(body: HTMLElement): void {
  if (body.querySelector(":scope > .unified-reading-prose")) return;
  const doc = body.ownerDocument;
  const first = body.firstElementChild;
  const hasFollowingBlocks = Boolean(first?.nextElementSibling);
  const prose = doc.createElement("div");
  prose.className = "unified-reading-prose";
  const leading = doc.createDocumentFragment();
  let opener: HTMLDivElement | null = null;
  if (first?.tagName === "P" && canUseReadingOpener(first.textContent ?? "", hasFollowingBlocks)) {
    while (body.firstChild && body.firstChild !== first) leading.append(body.firstChild);
    opener = doc.createElement("div");
    opener.className = "unified-reading-opener";
    opener.append(first);
  }
  while (body.firstChild) prose.append(body.firstChild);
  body.append(leading);
  if (opener) body.append(opener);
  body.append(prose);

  for (const pre of body.querySelectorAll<HTMLPreElement>("pre")) {
    pre.tabIndex = 0;
    if (!pre.hasAttribute("role")) pre.setAttribute("role", "region");
    if (!pre.hasAttribute("aria-label")) pre.setAttribute("aria-label", "代码，可横向滚动");
  }
  for (const table of body.querySelectorAll<HTMLTableElement>("table")) {
    if (table.parentElement?.classList.contains("unified-reading-table")) continue;
    const scroll = doc.createElement("div");
    scroll.className = "unified-reading-table";
    scroll.tabIndex = 0;
    scroll.setAttribute("role", "region");
    scroll.setAttribute("aria-label", "表格，可横向滚动");
    table.before(scroll);
    scroll.append(table);
  }
}

export function readingBodyHtml(html: string): string {
  const body = document.createElement("div");
  body.innerHTML = html;
  prepareReadingBody(body);
  return body.innerHTML;
}

/** Lazy history arrives as escaped text first; only reshape it after Markdown
 * has actually been upgraded. Non-project/other document surfaces stay intact. */
export function prepareLazyReadingBody(body: HTMLElement): void {
  if (body.closest(".unified-turn-assistant.unified-message-reading")) prepareReadingBody(body);
}
