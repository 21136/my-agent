import assert from "node:assert/strict";
import test from "node:test";

import { createChatSession } from "../src/shells/chat-state.ts";

test("pre-turn work-record outcome notice renders immediately and survives turn.start", () => {
  const renderedSnapshots: string[][] = [];
  let chat: ReturnType<typeof createChatSession>;
  chat = createChatSession(
    { showProcess: true, confirmInBlocks: false },
    {
      onChange: () => {
        renderedSnapshots.push(
          chat.model.blocks
            .filter((block) => block.kind === "notice")
            .map((block) => block.text),
        );
      },
    },
  );

  chat.handleEvent({
    type: "turn.notice",
    text: "已按本次授权写入当前会话工作记录。",
  });

  assert.deepEqual(renderedSnapshots.at(-1), ["已按本次授权写入当前会话工作记录。"]);
  assert.equal(chat.model.turnActive, false);

  chat.handleEvent({
    type: "turn.start",
    intent: "execute",
    intent_label: "执行",
    run_id: "run-after-record",
  });

  assert.equal(chat.model.turnActive, true);
  assert.equal(
    chat.model.blocks.some(
      (block) => block.kind === "notice" && block.text === "已按本次授权写入当前会话工作记录。",
    ),
    true,
  );
});
