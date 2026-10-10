import assert from "node:assert/strict";
import test from "node:test";

import {
  filterProjectDocs,
  groupDocsByFolder,
  isInternalProjectDoc,
  renameDraftForDoc,
  renderDocCatalogHtml,
  renderDocumentReaderHtml,
  sortProjectDocs,
  standardRenameWarning,
} from "../src/shells/unified/doc-reading.ts";

const docs = [
  { path: "NOTES.md", name: "NOTES.md", size: 1, is_standard: false, mtime: 1_700_000_000 },
  { path: "PROJECT.md", name: "PROJECT.md", size: 1, is_standard: true, mtime: 1_700_000_100 },
  { path: "docs/guide.md", name: "guide.md", size: 1, is_standard: false, mtime: 1_700_000_200 },
  { path: ".plan-agent/secret.md", name: "secret.md", size: 1, is_standard: false },
  { path: ".agent/notes.md", name: "notes.md", size: 1, is_standard: false },
  { path: "RUNAWAY-PROGRESS.md", name: "RUNAWAY-PROGRESS.md", size: 1, is_standard: false },
];

test("document catalog hides agent bookkeeping", () => {
  assert.equal(isInternalProjectDoc(".plan-agent/manifest.json"), true);
  assert.equal(isInternalProjectDoc("notes/.plan-agent/secret.md"), true);
  assert.equal(isInternalProjectDoc("RUNAWAY-PROGRESS.md"), true);
  assert.equal(isInternalProjectDoc("NOTES.md"), false);

  const visible = sortProjectDocs(docs).map((doc) => doc.path);
  assert.deepEqual(visible, ["PROJECT.md", "docs/guide.md", "NOTES.md"]);

  const html = renderDocCatalogHtml(docs, "NOTES.md", {
    inputId: "new-doc",
    createAction: "overlay-new-doc",
  });
  assert.match(html, /NOTES\.md/);
  assert.equal(html.includes(".plan-agent"), false);
  assert.equal(html.includes("RUNAWAY-PROGRESS"), false);
  assert.equal(html.includes(".agent"), false);
});

test("catalog search, folder groups, recent badge, placeholder hint", () => {
  const filtered = filterProjectDocs(docs, "guide").map((d) => d.path);
  assert.deepEqual(filtered, ["docs/guide.md"]);
  const groups = groupDocsByFolder(sortProjectDocs(docs.filter((d) => !isInternalProjectDoc(d.path))));
  assert.ok(groups.some((g) => g.folder === "docs"));
  const html = renderDocCatalogHtml(docs, "NOTES.md", {
    inputId: "new-doc",
    createAction: "overlay-new-doc",
    searchQuery: "notes",
    recentlyChanged: ["NOTES.md"],
    placeholderCount: 3,
    showFolderField: true,
  });
  assert.match(html, /doc-catalog-search/);
  assert.match(html, /刚更新/);
  assert.match(html, /未填写模板 3/);
  assert.match(html, /让 AI 起草/);
  assert.match(html, /文件夹/);
});

test("standard rename draft uses Chinese title and warns", () => {
  assert.equal(renameDraftForDoc("PROJECT.md"), "项目说明");
  assert.match(standardRenameWarning("PROJECT.md"), /标准文档/);
  assert.equal(standardRenameWarning("NOTES.md"), "");
});

test("document reader exposes conflict, split toggle, and markdown toolbar", () => {
  const { html } = renderDocumentReaderHtml({
    docs,
    currentPath: "NOTES.md",
    currentContent: "# hi\n\nbody",
    renderedHtml: "<p>hi</p>",
    includeCatalog: false,
    includeBack: false,
    view: "edit",
    draft: "# hi\n\nbody",
    dirty: true,
    conflictPrompt: true,
    splitEditPreview: true,
    docsChatSplit: true,
  });
  assert.match(html, /document-conflict-reload/);
  assert.match(html, /仍要覆盖/);
  assert.match(html, /toggle-docs-chat-split/);
  assert.match(html, /doc-md-bold/);
  assert.match(html, /doc-selection-to-ai/);
  assert.match(html, /unified-document-split-edit/);
  assert.match(html, /data-live-preview/);
});

test("delete copy mentions recycle bin", async () => {
  const { deleteConfirmCopy } = await import("../src/shells/unified/doc-reading.ts");
  const copy = deleteConfirmCopy({ path: "NOTES.md", name: "NOTES.md", is_standard: false });
  assert.match(copy.body, /回收站/);
  assert.match(copy.confirmLabel, /回收站/);
});
