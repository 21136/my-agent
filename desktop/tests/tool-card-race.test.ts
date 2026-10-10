import assert from "node:assert/strict";
import test from "node:test";

import { createChatSession, type ChatBlock } from "../src/shells/chat-state.ts";
import {
  lastFailedTool,
  processPillLabel,
  renderToolFailAlert,
} from "../src/shells/unified/output-display.ts";

function session() {
  return createChatSession({ showProcess: true, confirmInBlocks: false });
}

function banner(sessionId = "sess-1") {
  return {
    type: "session.banner" as const,
    session_id: sessionId,
    topics: [] as string[],
    turn_mode: "agent",
    turn_mode_label: "动手",
    phase: "S4",
  };
}

function processes(blocks: ChatBlock[]) {
  return blocks.filter((block): block is ChatBlock & { kind: "process" } => block.kind === "process");
}

function failTools(block: ChatBlock & { kind: "process" }) {
  return (block.tools ?? []).filter((tool) => tool.status === "fail");
}

test("history during a failed patch keeps the step and the reply", () => {
  const chat = session();
  chat.handleEvent(banner());
  chat.pushUserMessage("把 README 简介改成英文");
  chat.handleEvent({
    type: "execution.state",
    run_id: "run-a",
    state: "running",
    sequence: 1,
  });
  chat.handleEvent({
    type: "turn.start",
    intent: "execute",
    intent_label: "执行",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.start",
    tool: "patch_file",
    call_id: "patch-1",
    summary: "修补 README.md",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "session.history",
    session_id: "sess-1",
    items: [
      { role: "user", text: "先写一句简介" },
      { role: "assistant", text: "已经写好了。" },
    ],
  });
  chat.handleEvent({
    type: "tool.end",
    tool: "patch_file",
    call_id: "patch-1",
    ok: false,
    summary: "没有找到要替换的原文",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "assistant.done",
    text: "这次没有改成，原文对不上。",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "turn.end",
    ok: true,
    finish_reason: "stop",
    run_id: "run-a",
  });

  const cards = processes(chat.model.blocks);
  assert.equal(cards.length, 1);
  const failed = failTools(cards[0]);
  assert.equal(failed.length, 1);
  assert.match(processPillLabel(cards[0]), /1 步/);
  assert.match(processPillLabel(cards[0]), /失败/);
  assert.match(renderToolFailAlert(lastFailedTool(cards[0].tools)), /修补未成功/);
  assert.equal(
    chat.model.blocks.some(
      (block) => block.kind === "assistant" && block.text.includes("原文对不上"),
    ),
    true,
  );
  assert.equal(
    chat.model.blocks.some(
      (block) => block.kind === "user" && block.text.includes("改成英文"),
    ),
    true,
  );
});

test("a stale history snapshot after the turn cannot wipe the failed step", () => {
  const chat = session();
  chat.handleEvent(banner());
  chat.pushUserMessage("把 README 简介改成英文");
  chat.handleEvent({
    type: "execution.state",
    run_id: "run-a",
    state: "running",
    sequence: 1,
  });
  chat.handleEvent({
    type: "turn.start",
    intent: "execute",
    intent_label: "执行",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.start",
    tool: "patch_file",
    call_id: "patch-2",
    summary: "修补 README.md",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.end",
    tool: "patch_file",
    call_id: "patch-2",
    ok: false,
    summary: "没有找到要替换的原文",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "assistant.done",
    text: "修补没有对上原文。",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "execution.state",
    run_id: "run-a",
    state: "settled",
    sequence: 2,
    finish_reason: "stop",
    ok: true,
  });
  chat.handleEvent({
    type: "turn.end",
    ok: true,
    finish_reason: "stop",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "session.history",
    session_id: "sess-1",
    items: [{ role: "user", text: "把 README 简介改成英文" }],
  });

  const cards = processes(chat.model.blocks);
  assert.equal(cards.length, 1);
  assert.equal(failTools(cards[0]).length, 1);
  assert.match(processPillLabel(cards[0]), /1 步/);
  assert.equal(
    chat.model.blocks.some(
      (block) => block.kind === "assistant" && block.text.includes("没有对上原文"),
    ),
    true,
  );
});

test("a second turn.start reuses the process and a late tool.end still fails it", () => {
  const chat = session();
  chat.handleEvent(banner());
  chat.pushUserMessage("再改一次");
  chat.handleEvent({
    type: "execution.state",
    run_id: "run-a",
    state: "running",
    sequence: 1,
  });
  chat.handleEvent({
    type: "turn.start",
    intent: "execute",
    intent_label: "执行",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.start",
    tool: "patch_file",
    call_id: "patch-3",
    summary: "修补 README.md",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "turn.start",
    intent: "execute",
    intent_label: "执行",
    run_id: "run-b",
  });
  chat.handleEvent({
    type: "execution.state",
    run_id: "run-b",
    state: "running",
    sequence: 2,
  });
  chat.handleEvent({
    type: "tool.end",
    tool: "patch_file",
    call_id: "patch-3",
    ok: false,
    summary: "没有找到要替换的原文",
    run_id: "run-a",
  });

  const cards = processes(chat.model.blocks);
  assert.equal(cards.length, 1);
  assert.equal(failTools(cards[0]).length, 1);
  assert.match(processPillLabel(cards[0]), /1 步/);
  assert.equal(cards[0].tools?.some((tool) => tool.status === "running"), false);
});

test("tool.end before tool.start stays a single failed step", () => {
  const chat = session();
  chat.handleEvent(banner());
  chat.pushUserMessage("再改一次");
  chat.handleEvent({
    type: "execution.state",
    run_id: "run-a",
    state: "running",
    sequence: 1,
  });
  chat.handleEvent({
    type: "turn.start",
    intent: "execute",
    intent_label: "执行",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.end",
    tool: "patch_file",
    call_id: "patch-4",
    ok: false,
    summary: "没有找到要替换的原文",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.start",
    tool: "patch_file",
    call_id: "patch-4",
    summary: "修补 README.md",
    run_id: "run-a",
  });

  const cards = processes(chat.model.blocks);
  assert.equal(cards.length, 1);
  assert.equal(cards[0].tools?.length, 1);
  assert.equal(cards[0].tools?.[0]?.status, "fail");
});

test("a same-session banner during the turn does not drop the tool result", () => {
  const chat = session();
  chat.handleEvent(banner());
  chat.pushUserMessage("再改一次");
  chat.handleEvent({
    type: "execution.state",
    run_id: "run-a",
    state: "running",
    sequence: 1,
  });
  chat.handleEvent({
    type: "turn.start",
    intent: "execute",
    intent_label: "执行",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.start",
    tool: "patch_file",
    call_id: "patch-5",
    summary: "修补 README.md",
    run_id: "run-a",
  });
  chat.handleEvent(banner());
  assert.equal(chat.model.executionRunId, "run-a");
  chat.handleEvent({
    type: "tool.end",
    tool: "patch_file",
    call_id: "patch-5",
    ok: false,
    summary: "没有找到要替换的原文",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "assistant.done",
    text: "还是没对上。",
    run_id: "run-a",
  });

  const cards = processes(chat.model.blocks);
  assert.equal(failTools(cards[0]).length, 1);
  assert.equal(
    chat.model.blocks.some((block) => block.kind === "assistant" && block.text.includes("没对上")),
    true,
  );
});

test("a probe read of a missing file is not a failed step", () => {
  const chat = session();
  chat.handleEvent(banner());
  chat.pushUserMessage("帮我新建一个 README");
  chat.handleEvent({
    type: "execution.state",
    run_id: "run-a",
    state: "running",
    sequence: 1,
  });
  chat.handleEvent({
    type: "turn.start",
    intent: "execute",
    intent_label: "执行",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.start",
    tool: "read_file",
    call_id: "read-1",
    summary: "workspace/scratch-readme/README.md",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.end",
    tool: "read_file",
    call_id: "read-1",
    ok: true,
    summary: "文件还不存在",
    note: "absent",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.start",
    tool: "write_text",
    call_id: "write-1",
    summary: "workspace/scratch-readme/README.md",
    run_id: "run-a",
  });
  chat.handleEvent({
    type: "tool.end",
    tool: "write_text",
    call_id: "write-1",
    ok: true,
    summary: "workspace/scratch-readme/README.md · 写入 1 行：一句项目简介",
    run_id: "run-a",
  });

  const cards = processes(chat.model.blocks);
  assert.equal(cards.length, 1);
  const tools = cards[0].tools ?? [];
  assert.equal(tools[0]?.status, "note");
  assert.equal(tools[0]?.endSummary, "文件还不存在");
  assert.equal(tools[1]?.status, "ok");
  assert.equal(failTools(cards[0]).length, 0);
  assert.equal(processPillLabel(cards[0]).includes("失败"), false);
  assert.equal(renderToolFailAlert(lastFailedTool(cards[0].tools)), "");
});
