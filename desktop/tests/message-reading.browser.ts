/** Browser-only DOM regression suite; run in a renderer fixture, no sidecar required. */
import { prepareReadingBody, readingBodyHtml, prepareLazyReadingBody } from "../src/shells/unified/message-reading";
import { renderMarkdown } from "../src/markdown";
import { bindLazyMarkdownLookup, observeLazyMarkdown, resetLazyMarkdownObserver } from "../src/lazy-markdown";
import { parseUserMessage, formatUserMessageHtml } from "../src/user-message";

export async function runMessageReadingDomTests(): Promise<string[]> {
  const results: string[] = [];
  function check(condition: unknown, label: string): void {
    if (!condition) throw new Error(label);
    results.push(label);
  }
  const body = document.createElement("div");
  body.innerHTML = '\n<p>原有<strong>首段</strong> <a href="#kept">原链接</a></p>\n<h3>标题</h3>\n<p>全部正文</p>\n<pre><code>const x = 1;\n</code></pre>\n<table><tbody><tr><td>原表格</td></tr></tbody></table>\n<div class="mermaid-placeholder" data-mermaid-source="graph%20TD%3BA--%3EB">图表渲染中…</div>\n';
  const before = body.textContent;
  const nodes = Array.from(body.childNodes);
  const link = body.querySelector("a")!;
  let clicked = false;
  link.addEventListener("click", (event) => { event.preventDefault(); clicked = true; });
  prepareReadingBody(body);
  check(body.textContent === before, "文本与空白逐字保留");
  check(nodes.every((node, index) => body.contains(node) && (index === 0 || Boolean(nodes[index - 1].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING))), "原节点和顺序保留");
  check(body.querySelector(".unified-reading-opener p strong")?.textContent === "首段", "原首段完整进入开场气泡");
  link.click();
  check(clicked && link.getAttribute("href") === "#kept", "链接与原事件处理保留");
  check(body.querySelector(".mermaid-placeholder")?.getAttribute("data-mermaid-source") === "graph%20TD%3BA--%3EB", "Mermaid 源占位保留");
  check(body.querySelector("pre")?.tabIndex === 0 && body.querySelector<HTMLElement>(".unified-reading-table")?.tabIndex === 0, "代码与表格支持键盘聚焦");
  const once = body.innerHTML;
  prepareReadingBody(body);
  check(body.innerHTML === once, "重复调用不会嵌套包装");
  const longFirst = document.createElement("div");
  longFirst.innerHTML = `<p>${"长文".repeat(100)}</p><p>后续正文</p>`;
  prepareReadingBody(longFirst);
  check(!longFirst.querySelector(".unified-reading-opener"), "长首段不被塞入大气泡");
  const technical = renderMarkdown("这是原首段。\n\n### 标题\n\n- 一项\n- 两项\n\n> 原引用\n\n```ts\nconst answer = 42;\n```\n\n| A | B |\n| --- | --- |\n| 一 | 二 |");
  const baseline = document.createElement("div");
  baseline.innerHTML = technical;
  const transformed = document.createElement("div");
  transformed.innerHTML = readingBodyHtml(technical);
  check(baseline.textContent === transformed.textContent && transformed.querySelectorAll("h3,li,blockquote,pre,table").length === 6, "真实 Markdown 的标题列表引用代码表格保留");
  const other = document.createElement("div");
  other.innerHTML = technical;
  prepareLazyReadingBody(other);
  check(other.innerHTML === technical, "非项目阅读消息不被改写");
  const scrollFixture = document.createElement("div");
  scrollFixture.innerHTML = `<div class="unified-shell" data-perspective="project" style="display:block;width:300px;height:auto"><article class="unified-turn-assistant unified-message-reading"><div class="unified-turn-body unified-markdown">${readingBodyHtml(renderMarkdown("```ts\nconst example = '" + "long_".repeat(80) + "';\n```"))}</div></article></div>`;
  scrollFixture.style.cssText = "position:fixed;left:0;top:0;opacity:0;pointer-events:none";
  document.body.append(scrollFixture);
  try {
    const pre = scrollFixture.querySelector("pre")!;
    check(pre.scrollWidth > pre.clientWidth && getComputedStyle(pre.querySelector("code")!).overflowX === "visible", "横向溢出由可聚焦代码区承接而非内部高亮层");
  } finally { scrollFixture.remove(); }
  const attached = "[附件]\n- 示例.txt → workspace/example.txt (text/plain)\n\n只看这个附件。";
  check(parseUserMessage(attached).body === "只看这个附件。" && formatUserMessageHtml(attached).includes("history-attachment-chip"), "附件元数据不参与正文长度且附件标签保留");

  const root = document.createElement("div");
  root.style.cssText = "position:fixed;top:0;left:0;width:10px;height:10px;overflow:auto;opacity:0;pointer-events:none";
  root.innerHTML = '<article class="unified-turn-assistant unified-message-reading"><div class="unified-turn-body unified-markdown unified-markdown-lazy" data-lazy-md="777"></div></article>';
  const lazy = root.querySelector<HTMLElement>("[data-lazy-md]")!;
  lazy.textContent = "历史长回复。\n\n" + "完整保留。".repeat(70);
  const source = lazy.textContent;
  const expected = document.createElement("div");
  expected.innerHTML = renderMarkdown(source);
  document.body.append(root);
  try {
    bindLazyMarkdownLookup((index) => index === 777 ? source : null);
    await new Promise<void>((resolve, reject) => {
      const timeout = setTimeout(() => { mutation.disconnect(); reject(new Error("懒加载未升级")); }, 4000);
      const mutation = new MutationObserver(() => {
        if (lazy.querySelector(".unified-reading-prose")) { clearTimeout(timeout); mutation.disconnect(); resolve(); }
      });
      mutation.observe(lazy, { childList: true, subtree: true });
      observeLazyMarkdown(root, prepareLazyReadingBody);
    });
    check(lazy.dataset.lazyMdDone === "1" && lazy.textContent === expected.textContent && Boolean(lazy.querySelector(".unified-reading-opener")), "真实 IntersectionObserver 历史升级走同一阅读排版");
  } finally {
    resetLazyMarkdownObserver();
    root.remove();
  }
  return results;
}
