/**
 * Companion 小魔王空间: single composer confirm, no system话术, no live project-scoped snapshots in chat.
 */
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

const ts = createRequire(import.meta.url)("typescript");
const cache = new Map();
function load(url) {
  if (cache.has(url.href)) return cache.get(url.href);
  const exports = {};
  cache.set(url.href, exports);
  const js = ts.transpileModule(readFileSync(url, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  new Function("exports", "require", js)(exports, (id) => load(new URL(id.endsWith(".ts") ? id : `${id}.ts`, url)));
  return exports;
}

const chatState = load(new URL("../src/shells/chat-state.ts", import.meta.url));
const copy = load(new URL("../src/shells/unified/system-notice-copy.ts", import.meta.url));
const workCards = load(new URL("../src/shells/unified/work-card-presentation.ts", import.meta.url));
const preview = load(new URL("../src/shells/unified/confirm-preview.ts", import.meta.url));

const {
  companionHomeComposerOwnsOrdinaryConfirm,
  composerShouldShowOrdinaryConfirmActions,
  createChatSession,
  ordinaryConfirmWouldRenderInChat,
} = chatState;

function session() {
  const chat = createChatSession({ showProcess: true, confirmInBlocks: true });
  chat.handleEvent({
    type: "session.banner",
    session_id: "companion-home",
    topics: [],
    turn_mode: "agent",
    turn_mode_label: "动手",
    phase: "S4",
  });
  return chat;
}

test("exactly one actionable confirm: companion composer on, in-chat card off", () => {
  const chat = session();
  const ui = { mainFocus: "chat", perspective: "project" };
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-a",
    request_id: "batch-auth",
    preview: "软删除到 _trash workspace 项目\nids=random-number-test",
    allow_approve_all: false,
  });
  assert.equal(ordinaryConfirmWouldRenderInChat(chat.model, { companionComposerPrimary: true }), false);
  assert.equal(
    composerShouldShowOrdinaryConfirmActions(chat.model, {
      chatRendersOrdinaryConfirm: false,
      overlayRendersOrdinaryConfirm: false,
      companionComposerPrimary: companionHomeComposerOwnsOrdinaryConfirm(ui),
    }),
    true,
  );
  const compact = preview.renderComposerOrdinaryConfirmDetailHtml(chat.model.confirmOverlay?.preview ?? "");
  assert.match(compact, /软删除/);
  assert.doesNotMatch(compact, /unified-confirm-kicker/);
  assert.doesNotMatch(compact, /需要确认/);
});

test("system orchestration lines never become chat notices", () => {
  const samples = [
    "需要在当前小魔王空间确认后继续；请查看聊天中的确认卡。",
    "已在主界面创建项目 workspace/random-number-4（未换线）",
    "[Goal] 多项目 worker 批次未逐项验收，父 Goal 保持未完成：a、b。",
  ];
  const chat = session();
  for (const text of samples) {
    assert.equal(copy.polishSystemNoticeText(text), null);
    chat.handleEvent({ type: "turn.notice", text });
  }
  assert.equal(chat.model.blocks.some((block) => block.kind === "notice"), false);
});

test("orchestration prose is stripped from assistant bubbles but summaries remain", () => {
  const chat = session();
  const noisy = [
    "需要在当前小魔王空间确认后继续；请查看聊天中的确认卡。",
    "已在主界面创建项目 workspace/random-number-test（未换线）",
    "",
    "三个项目页面已写好，请在本机打开 index.html 验收。",
  ].join("\n");
  chat.handleEvent({ type: "turn.start", run_id: "run-a", intent: "execute", intent_label: "后台工作" });
  chat.handleEvent({ type: "assistant.done", text: noisy });
  const assistant = chat.model.blocks.find((block) => block.kind === "assistant");
  assert.ok(assistant);
  assert.doesNotMatch(assistant.text, /小魔王空间确认/);
  assert.doesNotMatch(assistant.text, /未换线/);
  assert.match(assistant.text, /三个项目页面已写好/);
});

test("confirm.done terminal choice clears composer pending strip", () => {
  const chat = session();
  chat.handleEvent({
    type: "confirm.request",
    session_id: "companion-home",
    run_id: "run-done",
    request_id: "trash-done",
    preview: "软删除到 _trash workspace 项目\nids=alpha",
    allow_approve_all: false,
  });
  assert.equal(composerShouldShowOrdinaryConfirmActions(chat.model, {
    chatRendersOrdinaryConfirm: false,
    overlayRendersOrdinaryConfirm: false,
    companionComposerPrimary: true,
  }), true);
  chat.handleEvent({
    type: "confirm.done",
    session_id: "companion-home",
    request_id: "trash-done",
    choice: "n",
  });
  assert.equal(chat.model.confirmOverlay, null);
  assert.equal(
    composerShouldShowOrdinaryConfirmActions(chat.model, {
      chatRendersOrdinaryConfirm: false,
      overlayRendersOrdinaryConfirm: false,
      companionComposerPrimary: true,
    }),
    false,
  );
});

test("companion home defers project-scoped work-card.present from chat blocks", () => {
  const card = {
    card_id: "wc-" + "a".repeat(64),
    saved_at: "2026-01-01T00:00:00Z",
    viewed_at: null,
    snapshot: {
      kind: "project_scoped",
      session_id: "companion-home",
      project_id: "random-number-test",
      completeness: "complete",
      item_count: 1,
      requirement_count: 1,
      result_count: 1,
      cards: [{ title: "多项目任务批次", status: "open", requirements: [], source_refs: [{ kind: "project", id: "random-number-test" }] }],
    },
  };
  assert.equal(workCards.companionHomeDefersWorkCardChatPresentation("chat", card), true);
  assert.equal(workCards.companionHomeDefersWorkCardChatPresentation("current-work", card), false);
});
