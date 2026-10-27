import { createManagedReviewCatalog, paginateManagedReviewCatalog, type ManagedReviewCatalog, type ManagedReviewSectionId } from "./managed-review-catalog.ts";

export interface ManagedReviewBrowser {
  show(report: Record<string, unknown>): void;
  clear(): void;
}

const PAGE_SIZE = 25;

function textElement(document: Document, tag: string, text: string): HTMLElement {
  const element = document.createElement(tag);
  element.textContent = text;
  return element;
}

function safeJson(report: unknown): string {
  try { return JSON.stringify(report, null, 2) ?? String(report); }
  catch { return "审查报告无法安全显示。"; }
}

export function mountManagedReviewBrowser(container: HTMLElement): ManagedReviewBrowser {
  const document = container.ownerDocument;
  let catalog: ManagedReviewCatalog | null = null;
  let sectionId: ManagedReviewSectionId | undefined;
  let query = "";
  let page = 1;
  let reportJson = "";
  let searchControl: HTMLInputElement | null = null;
  let sectionControl: HTMLSelectElement | null = null;
  let listHost: HTMLOListElement | null = null;
  let pagerHost: HTMLElement | null = null;

  const resetViewReferences = () => {
    searchControl = null;
    sectionControl = null;
    listHost = null;
    pagerHost = null;
  };

  const renderFallback = (report: unknown, error: unknown) => {
    catalog = null;
    resetViewReferences();
    container.replaceChildren();
    const message = textElement(document, "p", `只读审查目录暂不可用：${error instanceof Error ? error.message : "报告格式无效"}`);
    message.setAttribute("role", "status");
    const details = document.createElement("details");
    details.open = true;
    details.appendChild(textElement(document, "summary", "查看完整报告（原始 JSON）"));
    const pre = textElement(document, "pre", safeJson(report));
    pre.style.maxHeight = "min(28vh, 280px)";
    pre.style.overflow = "auto";
    pre.style.whiteSpace = "pre-wrap";
    pre.style.overflowWrap = "anywhere";
    details.appendChild(pre);
    container.append(message, details);
  };

  const renderResults = () => {
    if (!catalog || !listHost || !pagerHost || !sectionControl) return;
    const activeSection = catalog.sections.find((section) => section.id === sectionId) ?? catalog.sections[0];
    if (!activeSection) throw new Error("报告缺少审查分区");
    sectionId = activeSection.id;
    sectionControl.value = activeSection.id;
    const result = paginateManagedReviewCatalog(catalog, { sectionId, query, page, pageSize: PAGE_SIZE });
    page = result.page;

    const rowItems: HTMLElement[] = [];
    for (const row of result.rows) {
      const item = document.createElement("li");
      const button = textElement(document, "button", `${row.summary} — ${row.path}`) as HTMLButtonElement;
      button.type = "button";
      button.style.maxWidth = "100%";
      button.style.whiteSpace = "normal";
      button.style.textAlign = "left";
      button.setAttribute("aria-expanded", "false");
      const detail = textElement(document, "pre", `${row.key}\n${row.path}\n${row.details}`);
      detail.hidden = true;
      detail.style.whiteSpace = "pre-wrap";
      detail.style.overflowWrap = "anywhere";
      button.addEventListener("click", () => {
        detail.hidden = !detail.hidden;
        button.setAttribute("aria-expanded", String(!detail.hidden));
      });
      item.append(button, detail);
      rowItems.push(item);
    }
    listHost.setAttribute("aria-label", `${activeSection.label}条目`);
    listHost.replaceChildren(...rowItems);

    const previous = textElement(document, "button", "上一页") as HTMLButtonElement;
    previous.type = "button";
    previous.disabled = result.page <= 1;
    previous.addEventListener("click", () => { page = Math.max(1, result.page - 1); renderResults(); });
    const next = textElement(document, "button", "下一页") as HTMLButtonElement;
    next.type = "button";
    next.disabled = result.page >= result.pageCount;
    next.addEventListener("click", () => { page = Math.min(result.pageCount, result.page + 1); renderResults(); });
    const count = textElement(document, "span", `第 ${result.page}/${result.pageCount} 页 · ${result.total} 项`);
    pagerHost.setAttribute("aria-label", "审查分页");
    pagerHost.replaceChildren(previous, count, next);
  };

  const mountCatalogView = () => {
    if (!catalog) return;
    resetViewReferences();
    const heading = textElement(document, "h2", `只读源码审查 · ${catalog.reviewDigest}`);
    const flags = textElement(document, "p", "执行=false · 可合入=false · 可启动=false；仅供审查，不产生归属、同意或执行权。");
    flags.setAttribute("role", "note");

    const metadata = document.createElement("details");
    metadata.appendChild(textElement(document, "summary", "基线与限制（完整元数据）"));
    metadata.appendChild(textElement(document, "p", `基线：${catalog.baselines.join("；") || "未提供"}`));
    metadata.appendChild(textElement(document, "p", `限制：${catalog.limitations.join("；") || "未提供"}`));

    const sectionLabel = textElement(document, "label", "审查分区");
    const select = document.createElement("select");
    sectionControl = select;
    select.setAttribute("aria-label", "审查分区");
    for (const section of catalog.sections) {
      const option = textElement(document, "option", section.label) as HTMLOptionElement;
      option.value = section.id;
      option.selected = section.id === sectionId;
      select.appendChild(option);
    }
    select.value = sectionId ?? "";
    select.addEventListener("change", () => {
      sectionId = select.value as ManagedReviewSectionId;
      page = 1;
      renderResults();
    });
    sectionLabel.appendChild(select);

    const searchLabel = textElement(document, "label", "搜索路径或摘要");
    const search = document.createElement("input");
    searchControl = search;
    search.type = "search";
    search.setAttribute("aria-label", "搜索路径或摘要");
    search.addEventListener("input", () => { query = search.value; page = 1; renderResults(); });
    searchLabel.appendChild(search);

    listHost = document.createElement("ol");
    pagerHost = document.createElement("nav");
    const disclosure = document.createElement("details");
    disclosure.appendChild(textElement(document, "summary", "查看完整报告（原始 JSON）"));
    const pre = textElement(document, "pre", reportJson);
    pre.style.maxHeight = "min(28vh, 280px)";
    pre.style.overflow = "auto";
    pre.style.whiteSpace = "pre-wrap";
    pre.style.overflowWrap = "anywhere";
    disclosure.appendChild(pre);

    container.replaceChildren(heading, flags, metadata, sectionLabel, searchLabel, listHost, pagerHost, disclosure);
    renderResults();
  };

  return {
    show(report: Record<string, unknown>) {
      reportJson = safeJson(report);
      try {
        catalog = createManagedReviewCatalog(report);
        sectionId = catalog.sections[0]?.id;
        query = "";
        page = 1;
        mountCatalogView();
      } catch (error) {
        renderFallback(report, error);
      }
    },
    clear() {
      catalog = null;
      sectionId = undefined;
      query = "";
      page = 1;
      reportJson = "";
      resetViewReferences();
      container.replaceChildren();
    },
  };
}
