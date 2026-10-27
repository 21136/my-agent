import assert from "node:assert/strict";
import { test } from "node:test";
import { mountManagedReviewBrowser } from "../src/shells/unified/managed-review-browser.ts";

class ElementPort {
  tagName: string; textContent = ""; value = ""; hidden = false; open = false; disabled = false;
  type = ""; selectionStart: number | null = 0; focused = false;
  style: Record<string, string> = {};
  attributes = new Map<string, string>(); children: ElementPort[] = []; listeners = new Map<string, (event?: any) => void>();
  constructor(tag: string) { this.tagName = tag; }
  setAttribute(key: string, value: string) { this.attributes.set(key, value); }
  appendChild(child: ElementPort) { this.children.push(child); return child; }
  append(...children: ElementPort[]) { this.children.push(...children); }
  focus() { this.focused = true; }
  setSelectionRange(start: number) { this.selectionStart = start; }
  replaceChildren(...children: ElementPort[]) { this.children = children; }
  addEventListener(type: string, listener: (event?: any) => void) { this.listeners.set(type, listener); }
  click() { this.listeners.get("click")?.({ target: this }); }
  dispatch(type: string) { if (type === "input") this.focused = true; this.listeners.get(type)?.({ target: this }); }
  getAttribute(key: string) { return this.attributes.get(key) ?? null; }
  all(): ElementPort[] { return [this, ...this.children.flatMap((child) => child.all())]; }
}
const doc = { createElement: (tag: string) => new ElementPort(tag) };
const report = {
  format: 1, mode: "managed_source_review_v1", review_digest: "f".repeat(64), execution: false, readyForMerge: false, readyForLaunch: false,
  stable: { head: "stable-head", branch: "main", indexDigest: "i", identity: { worktreeRoot: "stable" }, inventory: { entries: [{ path: "src/unsafe<script>.ts", state: "raw_diverged", rawDiverged: true, staged: false, modeDiverged: false }], excluded: [], limitations: [] } },
  sandbox: { head: "sandbox-head", branch: "experiment/sandbox", indexDigest: "j", identity: { worktreeRoot: "sandbox" }, inventory: { entries: [], excluded: [], limitations: [] } },
  history: { paths: [], commits: [{ oid: "1".repeat(40), parents: ["2".repeat(40)], tree: "3".repeat(40), subject: "full hash commit" }] }, effectiveDelta: { basis: "merge_base_to_sandbox_head", paths: [] }, blockers: [], limitations: [],
};

test("browser exposes safe semantic controls, full path and digest, collapsed metadata and full JSON", () => {
  const root = new ElementPort("section") as any; root.ownerDocument = doc;
  const browser = mountManagedReviewBrowser(root);
  browser.show(report);
  const nodes = root.all();
  assert.ok(nodes.some((n) => n.attributes.get("aria-label") === "审查分区"));
  assert.ok(nodes.some((n) => n.attributes.get("aria-label") === "搜索路径或摘要"));
  assert.ok(nodes.some((n) => n.textContent.includes("src/unsafe<script>.ts")));
  assert.ok(nodes.some((n) => n.textContent.includes("f".repeat(64))));
  assert.ok(nodes.some((n) => n.tagName === "summary" && n.textContent.includes("基线与限制")));
  const disclosures = nodes.filter((n) => n.tagName === "details");
  assert.equal(disclosures.at(-1)?.open, false);
  assert.ok(disclosures.at(-1)!.all().some((n) => n.tagName === "pre" && n.textContent.includes('"review_digest"')));
  const rowButton = nodes.find((n) => n.tagName === "button" && n.textContent.includes("src/unsafe"));
  assert.equal(rowButton?.style.whiteSpace, "normal");
  const sectionSelect = nodes.find((n) => n.attributes.get("aria-label") === "审查分区");
  sectionSelect!.value = "commits"; sectionSelect!.dispatch("change");
  assert.strictEqual(root.all().find((n) => n.attributes.get("aria-label") === "审查分区"), sectionSelect, "section selection does not remount controls");
  const commitButton = root.all().find((n) => n.tagName === "button" && n.textContent.includes("full hash commit"));
  assert.ok(commitButton);
  commitButton!.click();
  const commitDetail = root.all().find((n) => n.tagName === "pre" && n.textContent.includes("1".repeat(40)));
  assert.equal(commitDetail?.hidden, false);
  assert.ok(nodes.every((n) => !("innerHTML" in n)));
});

test("multi-character search retains focus and filter; clear removes rows but no control can authorize", () => {
  const root = new ElementPort("section") as any; root.ownerDocument = doc;
  const browser = mountManagedReviewBrowser(root);
  browser.show(report);
  const search = root.all().find((n) => n.attributes.get("aria-label") === "搜索路径或摘要");
  const sectionSelect = root.all().find((n) => n.attributes.get("aria-label") === "审查分区");
  assert.ok(search);
  search!.value = "src/"; search!.selectionStart = 4; search!.dispatch("input");
  const secondSearch = root.all().find((n) => n.attributes.get("aria-label") === "搜索路径或摘要");
  assert.ok(secondSearch?.focused, "search stays focused after rerender");
  assert.strictEqual(secondSearch, search, "the same search input node survives rerender");
  assert.strictEqual(root.all().find((n) => n.attributes.get("aria-label") === "审查分区"), sectionSelect, "the same section select survives search updates");
  secondSearch!.value = "src/unsafe"; secondSearch!.selectionStart = 10; secondSearch!.dispatch("input");
  const after = root.all();
  assert.equal(after.find((n) => n.attributes.get("aria-label") === "搜索路径或摘要")?.value, "src/unsafe");
  assert.ok(after.some((n) => n.textContent.includes("src/unsafe<script>.ts")));
  assert.ok(after.every((n) => !/adopt|merge|execute/i.test(n.attributes.get("data-action") ?? "")));
  browser.clear();
  assert.equal(root.all().filter((n) => n.tagName === "button").some((n) => n.textContent.includes("src/unsafe")), false);
});

test("malformed report replaces prior catalog with visible safe full-text fallback", () => {
  const root = new ElementPort("section") as any; root.ownerDocument = doc;
  const browser = mountManagedReviewBrowser(root);
  browser.show(report);
  browser.show({ ...report, review_digest: "invalid" });
  assert.equal(root.all().filter((n) => n.tagName === "button").some((n) => n.textContent.includes("src/unsafe")), false);
  assert.ok(root.all().some((n) => n.tagName === "details" && n.open));
  assert.match(root.all().map((n) => n.textContent).join("\n"), /"review_digest": "invalid"/);
});


test("pagination button reaches every row after page one", () => {
  const manyRows = {
    ...report,
    stable: { ...report.stable, inventory: { ...report.stable.inventory, entries: Array.from({ length: 31 }, (_, index) => ({ path: `src/file-${index}.ts`, state: "raw_diverged", rawDiverged: true, staged: false, modeDiverged: false })) } },
  };
  const root = new ElementPort("section") as any; root.ownerDocument = doc;
  mountManagedReviewBrowser(root).show(manyRows);
  const stableSearch = root.all().find((n) => n.attributes.get("aria-label") === "搜索路径或摘要");
  const stableSelect = root.all().find((n) => n.attributes.get("aria-label") === "审查分区");
  const buttons = root.all().filter((n) => n.tagName === "button");
  assert.ok(root.all().some((n) => n.textContent === "第 1/2 页 · 31 项"));
  buttons.find((n) => n.textContent === "下一页")!.click();
  assert.ok(root.all().some((n) => n.textContent === "第 2/2 页 · 31 项"));
  assert.strictEqual(root.all().find((n) => n.attributes.get("aria-label") === "搜索路径或摘要"), stableSearch);
  assert.strictEqual(root.all().find((n) => n.attributes.get("aria-label") === "审查分区"), stableSelect);
  assert.ok(root.all().some((n) => n.textContent.includes("src/file-30.ts")));
});





