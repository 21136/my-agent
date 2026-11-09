import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import test from "node:test";

const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();

function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", js)(exports, (id) => {
    const next = id.endsWith(".ts") ? id : `${id}.ts`;
    return load(new URL(next, url));
  });
  return exports;
}

const copy = load(new URL("../src/shells/unified/system-notice-copy.ts", import.meta.url));

test("polishSystemNoticeText silences routine save acks", () => {
  assert.equal(copy.polishSystemNoticeText("会话已保存"), null);
});

test("polishSystemNoticeText silences companion orchestration routing notices", () => {
  assert.equal(
    copy.polishSystemNoticeText(
      "并行多项目编排：主模型只路由；每个项目各开一个 workspace，对每个 project_id 调用 run_project_workers。",
    ),
    null,
  );
});

test("polishSystemNoticeText rewrites evolved allowance and strips internal prefixes", () => {
  assert.equal(
    copy.polishSystemNoticeText("本会话 workspace evolved 均已允许。"),
    "本会话扩展区写入均已放行。",
  );
  assert.match(copy.polishSystemNoticeText("[guard] 同类命令连续失败，已暂时禁止重复调用。"), /连续失败/);
  assert.doesNotMatch(copy.polishSystemNoticeText("[guard] 同类命令连续失败，已暂时禁止重复调用。"), /\[guard\]/);
});

test("polishToolProgressText humanizes tool ids in progress lines", () => {
  assert.match(copy.polishToolProgressText("run_command · 仍在执行… 5s"), /运行命令/);
  assert.doesNotMatch(copy.polishToolProgressText("run_command · 仍在执行… 5s"), /run_command/);
});

test("delivery and work-instruction labels hide success noise", () => {
  assert.equal(copy.workInstructionChatStatusLabel("delivered", "next_model_request_context"), null);
  assert.equal(copy.parallelChatDeliveryLabel("done"), null);
  assert.equal(copy.parallelChatUserFootnote("done"), null);
  assert.match(copy.workInstructionChatStatusLabel("undelivered", "范围不允许"), /未送达/);
});
