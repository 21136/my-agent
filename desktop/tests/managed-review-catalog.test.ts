import assert from "node:assert/strict";
import { test } from "node:test";
import { createManagedReviewCatalog, paginateManagedReviewCatalog } from "../src/shells/unified/managed-review-catalog.ts";

const h = (n: string) => n.repeat(40);
const report = () => ({
  format: 1, mode: "managed_source_review_v1", review_digest: "d".repeat(64),
  stable: { head: h("e"), branch: "main", indexDigest: "f".repeat(64), identity: { worktreeRoot: "C:/project" }, inventory: { entries: [
    { path: "desktop/a.ts", state: "clean", staged: false, rawDiverged: false, modeDiverged: false },
    { path: "desktop/b.ts", headOid: h("1"), indexOid: h("2"), workingOid: h("3"), workingSha256: "4".repeat(64), headMode: "100644", indexMode: "100755", mode: "100755", size: 321, state: "raw_diverged", staged: true, rawDiverged: true, modeDiverged: false },
  ], excluded: [{ path: "data/private.json", classification: "protected_or_out_of_scope", workingSha256: "SECRET" }], limitations: ["stable limitation"] } },
  sandbox: { head: h("a"), branch: "experiment/sandbox", indexDigest: "e".repeat(64), identity: { worktreeRoot: "C:/project/.worktrees/sandbox" }, inventory: { entries: [
    { path: "desktop/a.ts", state: "staged", staged: true, rawDiverged: false, modeDiverged: true },
    { path: "desktop/c.ts", state: "untracked", staged: false, rawDiverged: false, modeDiverged: false },
    { path: "desktop/d.ts", headOid: h("5"), indexOid: h("5"), workingOid: null, workingSha256: null, headMode: "100644", indexMode: "100644", state: "missing_working_file", staged: false, rawDiverged: false, modeDiverged: false },
  ], excluded: [{ path: ".env", classification: "protected_or_out_of_scope", content: "SECRET" }], limitations: ["sandbox limitation"] } },
  history: { mergeBase: h("a"), commits: [{ oid: h("b"), parents: [h("a")], tree: h("c"), subject: "full commit" }], paths: [{ path: "desktop/a.ts", commits: [{ oid: h("b"), status: "M" }] }] },
  effectiveDelta: { basis: "merge_base_to_sandbox_head", base: h("a"), head: h("b"), paths: [{ status: "M", path: "desktop/a.ts", oldOid: h("c"), newOid: h("d") }] },
  blockers: [{ code: "overlap", paths: ["desktop/a.ts"] }, { code: "overlap-2", paths: ["desktop/a.ts"] }],
  limitations: ["report limitation"], execution: false, readyForMerge: false, readyForLaunch: false,
});

test("catalog retains full hashes and commit metadata, and exposes overlapping blockers without deduplication", () => {
  const catalog = createManagedReviewCatalog(report());
  assert.equal(catalog.reviewDigest, "d".repeat(64));
  assert.equal(catalog.execution, false); assert.equal(catalog.readyForMerge, false); assert.equal(catalog.readyForLaunch, false);
  const history = catalog.sections.find(x => x.id === "history")!;
  assert.equal(history.rows[0].key, "desktop/a.ts");
  const commit = catalog.sections.find(x => x.id === "commits")!.rows[0];
  assert.equal(commit.key, h("b")); assert.match(commit.details, new RegExp(h("a"))); assert.match(commit.details, new RegExp(h("c")));
  assert.equal(catalog.sections.find(x => x.id === "blockers")!.rows.length, 2);
  assert.equal(catalog.sections.find(x => x.id === "effective-delta")!.rows[0].summary.includes("merge-base to sandbox HEAD"), true);
});

test("inventories include every nonclean or flagged path and missing inventories fail closed", () => {
  const data = report(); const catalog = createManagedReviewCatalog(data);
  const paths = catalog.sections.filter(x => x.id === "stable-preservation" || x.id === "sandbox-ownership").flatMap(x => x.rows.map(r => r.path));
  for (const p of ["desktop/b.ts", "desktop/a.ts", "desktop/c.ts", "desktop/d.ts"]) assert.ok(paths.includes(p), p);
  assert.throws(() => createManagedReviewCatalog({ ...data, stable: {} }), /inventory/i);
});

test("excluded rows expose path and classification only, never contents or hashes", () => {
  const catalog = createManagedReviewCatalog(report());
  const text = JSON.stringify(catalog.sections.find(x => x.id === "excluded"));
  assert.match(text, /data\/private\.json/); assert.match(text, /protected_or_out_of_scope/);
  assert.doesNotMatch(text, /SECRET|workingSha256|content/);
});

test("strictly rejects malformed identity and readiness packets without mutating input", () => {
  const data = report(); const original = JSON.stringify(data);
  assert.throws(() => createManagedReviewCatalog({ ...data, mode: "other" }), /report|packet|mode/i);
  assert.throws(() => createManagedReviewCatalog({ ...data, readyForMerge: true }), /report|packet|ready|safety|readiness/i);
  createManagedReviewCatalog(data);
  assert.equal(JSON.stringify(data), original);
});

test("pagination uses literal case-insensitive query, clamps pages, and makes the final row reachable", () => {
  const catalog = createManagedReviewCatalog(report());
  const sectionId = "sandbox-ownership" as const;
  const page1 = paginateManagedReviewCatalog(catalog, { sectionId, query: "DESKTOP/", page: 1, pageSize: 1 });
  const page2 = paginateManagedReviewCatalog(catalog, { sectionId, query: "desktop/", page: 999, pageSize: 1 });
  assert.equal(page1.total, 3); assert.equal(page1.pageCount, 3); assert.equal(page1.rows.length, 1);
  assert.equal(page2.page, 3); assert.equal(page2.rows.length, 1); assert.equal(page1.rows[0].path, "desktop/a.ts");
  assert.equal(paginateManagedReviewCatalog(catalog, { sectionId, query: "[", page: 1, pageSize: 20 }).total, 0);
});


test("blocker row details grow linearly with path metadata and baselines label each identity field", () => {
  const source = report();
  const paths = Array.from({ length: 964 }, (_, i) => `desktop/generated/path-${String(i).padStart(4, "0")}.ts`);
  source.blockers = [{ code: "large_blocker", paths }];
  const catalog = createManagedReviewCatalog(source);
  const blockerRows = catalog.sections.find(x => x.id === "blockers")!.rows;
  const aggregateDetailsLength = blockerRows.reduce((sum, item) => sum + item.details.length, 0);
  const pathBytes = paths.reduce((sum, path) => sum + path.length, 0);
  assert.equal(blockerRows.length, paths.length);
  assert.ok(aggregateDetailsLength <= pathBytes * 4 + paths.length * 100, `details=${aggregateDetailsLength}, pathBytes=${pathBytes}`);
  const metadata = JSON.parse(blockerRows[0].details);
  assert.deepEqual(metadata, { code: "large_blocker", path: paths[0], blockerIndex: 0, totalPaths: paths.length });
  assert.ok(catalog.baselines.includes(`stable HEAD: ${h("e")}`));
  assert.ok(catalog.baselines.includes("stable branch: main"));
  assert.ok(catalog.baselines.includes(`stable index SHA256: ${"f".repeat(64)}`));
  assert.ok(catalog.baselines.includes("stable root: C:/project"));
  assert.ok(catalog.baselines.includes(`sandbox HEAD: ${h("a")}`));
});

test("inventory details retain full source identities, raw SHA-256 and deletion nulls with explicit raw-local distinction", () => {
  const catalog = createManagedReviewCatalog(report());
  const entries = catalog.sections.find(x => x.id === "stable-preservation")!.rows;
  const changed = entries.find(x => x.path === "desktop/b.ts")!;
  const details = JSON.parse(changed.details);
  assert.equal(details.headOid, h("1"));
  assert.equal(details.indexOid, h("2"));
  assert.equal(details.workingOid, h("3"));
  assert.equal(details.workingSha256, "4".repeat(64));
  assert.equal(details.headMode, "100644"); assert.equal(details.indexMode, "100755");
  assert.equal(details.mode, "100755"); assert.equal(details.size, 321);
  assert.match(changed.summary, /raw local bytes differ/i);
  assert.match(changed.summary, /Git blob identity/i);
  const deletion = catalog.sections.find(x => x.id === "sandbox-ownership")!.rows.find(x => x.path === "desktop/d.ts")!;
  const deletedDetails = JSON.parse(deletion.details);
  assert.equal(deletedDetails.workingSha256, null);
  assert.equal(deletedDetails.workingOid, null);
  assert.equal(deletedDetails.headOid, h("5"));
});

test("cross-lane is a sorted full-inventory union and distinguishes equal, different, missing, absent, and unknown hashes", () => {
  const source = report();
  const sha = (char: string) => char.repeat(64);
  const entry = (path: string, workingSha256: string | null | undefined, extra: Record<string, unknown> = {}) => ({ path, state: "raw_diverged", staged: false, rawDiverged: true, modeDiverged: false, ...(workingSha256 === undefined ? {} : { workingSha256 }), ...extra });
  source.stable.inventory.entries = [
    entry("equal.ts", sha("a"), { headOid: h("1"), indexOid: h("2"), workingOid: h("3"), staged: true }),
    entry("different.ts", sha("b"), { indexOid: h("4") }),
    entry("bad-hash.ts", "not-a-sha"),
    entry("both-null.ts", null, { state: "missing_working_file" }),
    entry("stable-only.ts", sha("c")),
    entry("unknown-hash.ts", undefined),
  ];
  source.sandbox.inventory.entries = [
    entry("equal.ts", sha("a"), { headOid: h("5"), indexOid: h("6"), staged: false }),
    entry("different.ts", sha("d")),
    entry("bad-hash.ts", sha("e")),
    entry("both-null.ts", null, { state: "missing_working_file" }),
    entry("sandbox-missing.ts", null, { state: "missing_working_file" }),
    entry("unknown-hash.ts", sha("f")),
  ];
  const section = createManagedReviewCatalog(source).sections.find(x => x.id === "cross-lane")!;
  assert.equal(section.label, "两区逐路径对照");
  assert.deepEqual(section.rows.map(row => row.path), ["bad-hash.ts", "both-null.ts", "different.ts", "equal.ts", "sandbox-missing.ts", "stable-only.ts", "unknown-hash.ts"]);
  const byPath = new Map(section.rows.map(row => [row.path, row]));
  const equal = byPath.get("equal.ts")!;
  assert.match(equal.summary, /原始字节相同/);
  assert.equal(JSON.parse(equal.details).comparison, "same");
  assert.equal(JSON.parse(equal.details).stable.indexOid, h("2"));
  assert.equal(JSON.parse(equal.details).sandbox.indexOid, h("6"));
  assert.equal(JSON.parse(byPath.get("different.ts")!.details).comparison, "different");
  assert.match(byPath.get("different.ts")!.summary, /原始字节不同/);
  for (const path of ["bad-hash.ts", "both-null.ts", "unknown-hash.ts"]) {
    assert.equal(JSON.parse(byPath.get(path)!.details).comparison, "unknown", path);
    assert.doesNotMatch(byPath.get(path)!.summary, /原始字节相同|原始字节不同/);
  }
  assert.match(byPath.get("both-null.ts")!.summary, /stable工作文件缺失/);
  assert.match(byPath.get("both-null.ts")!.summary, /sandbox工作文件缺失/);
  assert.match(byPath.get("stable-only.ts")!.summary, /sandbox清单无此条目/);
  assert.match(byPath.get("sandbox-missing.ts")!.summary, /stable清单无此条目/);
  assert.match(byPath.get("sandbox-missing.ts")!.summary, /sandbox工作文件缺失/);
});
