import assert from "node:assert/strict";
import { test } from "node:test";
import { mainFocusForRail, isRailTab } from "../src/shells/unified/rail.ts";
import { renderShellDevPanel } from "../src/shells/unified/shell-dev-panel.ts";

test("shell-dev rail maps to dedicated main focus", () => {
  assert.equal(isRailTab("shell-dev"), true);
  assert.equal(mainFocusForRail("shell-dev"), "shell-dev");
});

test("shell dev panel exposes embedded web slot and dual lanes", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-shell-dev-web-slot/);
  assert.match(html, /data-shell-dev-main-url/);
  assert.match(html, /data-shell-dev-promote/);
  assert.match(html, /data-shell-dev-adopt-merge/);
  assert.match(html, /旧整支合入（仅隔离夹具）/);
  assert.match(html, /data-shell-dev-adopt-merge[^>]*title="[^"]*仅用于隔离夹具[^"]*E0 gate 拒绝[^"]*真实项目[^"]*源码选择与检查点原生入口/);
  assert.match(html, /普通项目由 E0 gate 拒绝/);
  assert.match(html, /所选源码合入≠build\/activate/);
  assert.match(html, /不会自动同步 sandbox 的 dirty 内容/);
  assert.doesNotMatch(html, /成功后自动同步实验版|从原入口重启/);
  assert.match(html, /壳开发预览/);
});
test("shell dev panel exposes current-session evolution controls without adding a chat overlay",()=>{
  const html=renderShellDevPanel("http://127.0.0.1:5173/","http://127.0.0.1:5174/");
  assert.match(html,/data-evolution-begin/);assert.match(html,/data-evolution-inspect/);assert.match(html,/data-evolution-stop/);assert.match(html,/data-evolution-status/);assert.match(html,/data-evolution-checks/);
  assert.match(html,/在候选中试用（旧入口）/);assert.doesNotMatch(html,/开实验进化会话/);
  assert.doesNotMatch(html,/evolution.*overlay|chat.*overlay/i);
});



test("shell dev panel exposes a bounded read-only managed review region", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-shell-dev-managed-review/);
  assert.match(html, /data-managed-review-output/);
  assert.match(html, /max-height: min\(42vh, 420px\)/);
  assert.doesNotMatch(html, /data-managed-review-(?:adopt|merge|execute)/);
});

test("shell dev panel mounts a bounded managed review browser separately from safe fallback text", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-managed-review-browser/);
  assert.match(html, /data-managed-review-output/);
  assert.doesNotMatch(html, /data-managed-review-(?:adopt|merge|execute)/);
});

test("shell dev panel reserves bounded explicit selection separate from readonly review", () => {
  const html = renderShellDevPanel("http://127.0.0.1:5173/", "http://127.0.0.1:5174/");
  assert.match(html, /data-managed-source-selection/);
  assert.match(html, /所选范围只生成提案，不执行合入/);
  assert.doesNotMatch(html, /data-managed-source-selection-(?:merge|execute|confirm)/);
});
