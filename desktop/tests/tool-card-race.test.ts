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

// ---- 过程 pill: toggle state, turn end, errors, restored history ----

let execSeq = 0;
function runToolTurn(chat: ReturnType<typeof session>, userText: string, runId: string, reply: string) {
  chat.pushUserMessage(userText);
  chat.handleEvent({ type: "execution.state", run_id: runId, state: "running", sequence: ++execSeq });
  chat.handleEvent({ type: "turn.start", intent: "execute", intent_label: "执行", run_id: runId });
  chat.handleEvent({ type: "tool.start", tool: "read_file", call_id: `${runId}-c1`, summary: "读取 README.md", run_id: runId });
  chat.handleEvent({ type: "tool.end", tool: "read_file", call_id: `${runId}-c1`, ok: true, summary: "完成", run_id: runId });
  chat.handleEvent({ type: "assistant.delta", text: reply, run_id: runId });
  chat.handleEvent({ type: "assistant.done", text: reply, run_id: runId });
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "stop", run_id: runId });
  chat.handleEvent({ type: "execution.state", run_id: runId, state: "idle", sequence: ++execSeq });
}

test("finished turn: 过程 is collapsed, toggles both ways and stays where it is in the model", () => {
  const chat = session();
  chat.handleEvent(banner());
  runToolTurn(chat, "读一下 README", "run-t1", "读完了。");
  const [proc] = processes(chat.model.blocks);
  assert.equal(proc.collapsed, true);
  const before = chat.model.blocks.map((block) => block.kind).join(",");
  chat.toggleProcessCollapsed(proc.turnKey);
  assert.equal(proc.collapsed, false);
  chat.toggleProcessCollapsed(proc.turnKey);
  assert.equal(proc.collapsed, true);
  assert.equal(chat.model.blocks.map((block) => block.kind).join(","), before);
  assert.equal(proc.llmPending ?? false, false);
});

test("turns toggle independently and a new turn keeps an older turn's expanded 过程", () => {
  const chat = session();
  chat.handleEvent(banner());
  runToolTurn(chat, "第一问", "run-a1", "第一答");
  const [first] = processes(chat.model.blocks);
  chat.toggleProcessCollapsed(first.turnKey);
  assert.equal(first.collapsed, false);
  runToolTurn(chat, "第二问", "run-a2", "第二答");
  const [p1, p2] = processes(chat.model.blocks);
  assert.equal(p1, first);
  assert.equal(p1.collapsed, false, "older turn keeps the user's expanded state");
  assert.equal(p2.collapsed, true, "the new turn folds its own 过程 when done");
  chat.toggleProcessCollapsed(p2.turnKey);
  assert.equal(p1.collapsed, false);
  assert.equal(p2.collapsed, false);
});

test("error event settles the 过程: no stuck 等待模型 and it can collapse", () => {
  const chat = session();
  chat.handleEvent(banner());
  chat.pushUserMessage("出错的一问");
  chat.handleEvent({ type: "turn.start", intent: "execute", intent_label: "执行", run_id: "run-e" });
  chat.handleEvent({ type: "llm.pending", run_id: "run-e" } as never);
  chat.handleEvent({ type: "tool.start", tool: "read_file", call_id: "e1", summary: "读取", run_id: "run-e" });
  chat.handleEvent({ type: "tool.end", tool: "read_file", call_id: "e1", ok: true, summary: "完成", run_id: "run-e" });
  chat.handleEvent({ type: "llm.pending", run_id: "run-e" } as never);
  chat.handleEvent({ type: "error", message: "LLM 请求失败" });
  const [proc] = processes(chat.model.blocks);
  assert.equal(proc.llmPending ?? false, false);
  assert.equal(proc.collapsed, true);
  assert.doesNotMatch(processPillLabel(proc), /等待模型/);
  chat.toggleProcessCollapsed(proc.turnKey);
  assert.equal(proc.collapsed, false, "toggle is not swallowed by a stale live-thinking flag");
});

test("multi-segment turn: every 过程 of the finished turn is folded at turn.end", () => {
  const chat = session();
  chat.handleEvent(banner());
  chat.pushUserMessage("分两段做");
  chat.handleEvent({ type: "turn.start", intent: "execute", intent_label: "执行", run_id: "run-m" });
  chat.handleEvent({ type: "tool.start", tool: "read_file", call_id: "m1", summary: "读取", run_id: "run-m" });
  chat.handleEvent({ type: "tool.end", tool: "read_file", call_id: "m1", ok: true, summary: "完成", run_id: "run-m" });
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "stop", run_id: "run-m" });
  // server-driven follow-up segment within the same user turn
  chat.handleEvent({ type: "turn.start", intent: "execute", intent_label: "执行", run_id: "run-m2" });
  chat.handleEvent({ type: "tool.start", tool: "read_file", call_id: "m2", summary: "再读", run_id: "run-m2" });
  chat.handleEvent({ type: "tool.end", tool: "read_file", call_id: "m2", ok: true, summary: "完成", run_id: "run-m2" });
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "stop", run_id: "run-m2" });
  const procs = processes(chat.model.blocks);
  assert.ok(procs.length >= 1);
  assert.ok(procs.every((proc) => proc.collapsed), "no segment of a finished turn is left open");
});

test("history reload keeps the live 过程 inside its own turn, not under the newest turn", () => {
  const chat = session();
  chat.handleEvent(banner());
  runToolTurn(chat, "带工具的一问", "run-h1", "有工具的回答");
  // second turn has no tools at all
  chat.pushUserMessage("纯聊天");
  chat.handleEvent({ type: "turn.start", intent: "chat", intent_label: "聊天", run_id: "run-h2" });
  chat.handleEvent({ type: "assistant.done", text: "纯回答", run_id: "run-h2" });
  chat.handleEvent({ type: "turn.end", ok: true, finish_reason: "stop", run_id: "run-h2" });
  chat.handleEvent({
    type: "session.history",
    session_id: "sess-1",
    items: [
      { role: "user", text: "带工具的一问" },
      { role: "assistant", text: "有工具的回答" },
      { role: "user", text: "纯聊天" },
      { role: "assistant", text: "纯回答" },
    ],
  });
  const kinds = chat.model.blocks.map((block) => block.kind);
  const procIdx = kinds.indexOf("process");
  const secondUserIdx = chat.model.blocks.findIndex(
    (block) => block.kind === "user" && block.text === "纯聊天",
  );
  assert.ok(procIdx >= 0, "the tooled process survives the reload");
  assert.ok(procIdx < secondUserIdx, `process must stay in turn 1 (got ${kinds.join(",")})`);
});
