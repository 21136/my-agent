/**
 * Portable contracts for 当前工作 overlay item merge/split (independent session items).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const repo = path.resolve(desktop, "..");

test("work item merge/split wiring is present in overlay, ws client, and store", () => {
  const overview = readFileSync(path.join(desktop, "src/shells/unified/current-work-overview.ts"), "utf8");
  assert.match(overview, /data-cw-reorganize-bar/);
  assert.match(overview, /data-cw-merge-toggle/);
  assert.match(overview, /拆出为新事项/);
  assert.match(overview, /merge_independent_item|data-cw-merge-apply/);
  const shell = readFileSync(path.join(desktop, "src/shells/unified/index.ts"), "utf8");
  assert.match(shell, /merge_independent_item/);
  assert.match(shell, /applyIndependentItemMerge/);
  const ws = readFileSync(path.join(desktop, "src/api/ws.ts"), "utf8");
  assert.match(ws, /merge_independent_item/);
  assert.match(ws, /source_item_id/);
  const store = readFileSync(path.join(repo, "agent-core/work_records.py"), "utf8");
  assert.match(store, /def merge_independent_item_into/);
  const server = readFileSync(path.join(repo, "agent-core/server.py"), "utf8");
  assert.match(server, /merge_independent_item/);
  const css = readFileSync(path.join(desktop, "src/shells/unified/current-work-overview.css"), "utf8");
  assert.match(css, /unified-current-work-reorganize/);
});
