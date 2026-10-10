import assert from "node:assert/strict";
import test from "node:test";

import {
  isInternalProjectDoc,
  renderDocCatalogHtml,
  sortProjectDocs,
} from "../src/shells/unified/doc-reading.ts";

const docs = [
  { path: "NOTES.md", name: "NOTES.md", size: 1, is_standard: false },
  { path: "PROJECT.md", name: "PROJECT.md", size: 1, is_standard: true },
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
  assert.deepEqual(visible, ["PROJECT.md", "NOTES.md"]);

  const html = renderDocCatalogHtml(docs, "NOTES.md", {
    inputId: "new-doc",
    createAction: "overlay-new-doc",
  });
  assert.match(html, /NOTES\.md/);
  assert.equal(html.includes(".plan-agent"), false);
  assert.equal(html.includes("RUNAWAY-PROGRESS"), false);
  assert.equal(html.includes(".agent"), false);
});
