import { hydrateMermaid, renderMarkdown } from "./markdown";

export type LazyMarkdownLookup = (turnIndex: number) => string | null;

let lookup: LazyMarkdownLookup | null = null;
let observer: IntersectionObserver | null = null;
let observedRoot: HTMLElement | null = null;

/** Bind turn-index → assistant text for viewport upgrades (UI-5972 M4). */
export function bindLazyMarkdownLookup(fn: LazyMarkdownLookup): void {
  lookup = fn;
}

function upgradeElement(el: HTMLElement): void {
  if (!lookup || el.dataset.lazyMdDone === "1") return;
  const turnIndex = Number(el.dataset.lazyMd);
  if (!Number.isFinite(turnIndex)) return;
  const text = lookup(turnIndex);
  if (text === null) return;
  el.dataset.lazyMdDone = "1";
  el.classList.remove("unified-markdown-lazy");
  delete el.dataset.lazyMd;
  el.innerHTML = renderMarkdown(text);
  observer?.unobserve(el);
  void hydrateMermaid(el);
}

/** Observe off-viewport lazy markdown bodies inside the chat scroll root. */
export function observeLazyMarkdown(chatRoot: HTMLElement): void {
  if (!lookup) return;
  if (observer && observedRoot !== chatRoot) {
    observer.disconnect();
    observer = null;
  }
  if (!observer) {
    observedRoot = chatRoot;
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          upgradeElement(entry.target as HTMLElement);
        }
      },
      { root: chatRoot, threshold: 0, rootMargin: "280px 0px" },
    );
  }
  chatRoot
    .querySelectorAll<HTMLElement>(
      ".unified-markdown-lazy[data-lazy-md]:not([data-lazy-md-done])",
    )
    .forEach((el) => observer!.observe(el));
}

export function resetLazyMarkdownObserver(): void {
  observer?.disconnect();
  observer = null;
  observedRoot = null;
}
