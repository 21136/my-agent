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

test("polishSystemNoticeText silences companion confirm and project-create automation", () => {
  assert.equal(copy.polishSystemNoticeText("需要在当前小魔王空间确认后继续；请查看聊天中的确认卡。"), null);
  assert.equal(copy.polishSystemNoticeText("已在主界面创建项目 workspace/random-number-4（未换线）"), null);
  assert.equal(copy.polishSystemNoticeText("[Goal] 多项目 worker 批次未逐项验收，父 Goal 保持未完成：a。"), null);
  assert.equal(
    copy.polishSystemNoticeText(
      "正在为 3 个项目准备 worker 派工；授权与写入确认只会在当前小魔王空间出现，请留意聊天确认卡。",
    ),
    null,
  );
  assert.equal(copy.polishSystemNoticeText("忽略过期确认（请点最新一张工具确认卡）"), null);
  assert.equal(
    copy.polishSystemNoticeText("另有一项授权确认正在进行；请先在输入区下方的确认条完成现有授权。"),
    null,
  );
});

test("stripCompanionOrchestrationProse keeps user-readable summary", () => {
  const raw = [
    "需要在当前小魔王空间确认后继续；请查看聊天中的确认卡。",
    "已在主界面创建项目 workspace/random-number-5（未换线）",
    "",
    "三个旧项目已软删除并重新创建，页面已写入。",
  ].join("\n");
  const stripped = copy.stripCompanionOrchestrationProse(raw);
  assert.doesNotMatch(stripped, /确认卡/);
  assert.match(stripped, /软删除并重新创建/);
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
