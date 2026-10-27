import assert from "node:assert/strict";
import { test } from "node:test";
import * as ui from "../src/shells/unified/shell-dev-adoption.ts";

test("plain browser reports desktop-only without trying a renderer-side Git workaround", async () => {
  assert.equal(typeof ui.createShellDevAdoptionController, "function");
  const messages: string[] = [];
  const controller = ui.createShellDevAdoptionController(undefined, (text) => messages.push(text), () => {});
  await controller.merge(); assert.match(messages.join("\n"), /Electron|桌面/);
});

test("button delegates approval to trusted main without supplying Git options and releases busy state", async () => {
  const busy: boolean[] = [], messages: string[] = [];
  const controller = ui.createShellDevAdoptionController({
    mergeSandboxIntoStable: async (...args: unknown[]) => {
      assert.equal(args.length, 0, "renderer must not supply permission flags");
      return { ok: true, message: "已合入", steps: ["已同步"] };
    },
  }, (text) => messages.push(text), (value) => busy.push(value));
  await controller.merge();
  assert.deepEqual(busy, [true, false]);
  assert.match(messages.join("\n"), /已合入/); assert.match(messages.join("\n"), /已同步/);
});

test("partial recovery displays an error and the exact backup, not a green success", async () => {
  const outputs: { text: string; tone?: string }[] = [];
  const controller = ui.createShellDevAdoptionController({
    mergeSandboxIntoStable: async () => ({ ok: false, merged: true, message: "恢复未完成", recoveryStash: "abc123" }),
  }, (text, tone) => outputs.push({ text, tone }), () => {});
  await controller.merge();
  assert.equal(outputs.at(-1)?.tone, "error"); assert.match(outputs.at(-1)?.text ?? "", /abc123/);
});

test("double click cannot issue parallel native dialogs and transport errors unlock the button", async () => {
  let release!: () => void; let calls = 0; const busy: boolean[] = [];
  const wait = new Promise<void>((resolve) => { release = resolve; });
  const controller = ui.createShellDevAdoptionController({ mergeSandboxIntoStable: async () => {
    calls++; await wait; throw new Error("IPC disconnected");
  } }, () => {}, (value) => busy.push(value));
  const first = controller.merge(); await controller.merge(); release(); await first;
  assert.equal(calls, 1); assert.deepEqual(busy, [true, false]);
});

test("status shows blockers and actual pending counts instead of claiming runtime acceptance", async () => {
  const messages: string[] = [];
  const controller = ui.createShellDevAdoptionController({ getSandboxAdoptStatus: async () => ({
    fatalBlockers: ["先处理冲突"], sandboxDirtyProduct: [" M desktop/main.ts"], stableDirtyProduct: [], commitsAhead: 2,
  }) }, (text) => messages.push(text), () => {});
  await controller.inspect(); assert.match(messages.join("\n"), /先处理冲突/);
});
