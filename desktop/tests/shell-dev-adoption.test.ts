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

test("one-click surfaces fatal blockers without opening merge", async () => {
  const outputs: { text: string; tone?: string }[] = [];
  let mergeCalls = 0;
  const controller = ui.createShellDevAdoptionController({
    getSandboxAdoptStatus: async () => ({ fatalBlockers: ["稳定区有未保存冲突"] }),
    mergeSandboxIntoStable: async () => { mergeCalls++; return { ok: true, message: "不应到达" }; },
  }, (text, tone) => outputs.push({ text, tone }), () => {});
  await controller.oneClick();
  assert.equal(mergeCalls, 0);
  assert.equal(outputs.at(-1)?.tone, "error");
  assert.match(outputs.at(-1)?.text ?? "", /稳定区有未保存冲突/);
});

test("one-click refreshes status then delegates to the same native merge path", async () => {
  const busy: boolean[] = [];
  const messages: string[] = [];
  let statusCalls = 0;
  let mergeCalls = 0;
  const controller = ui.createShellDevAdoptionController({
    getSandboxAdoptStatus: async () => { statusCalls++; return { fatalBlockers: [], commitsAhead: 1 }; },
    mergeSandboxIntoStable: async (...args: unknown[]) => {
      mergeCalls++;
      assert.equal(args.length, 0);
      return { ok: true, message: "已合入稳定版", steps: ["已同步"] };
    },
  }, (text) => messages.push(text), (value) => busy.push(value));
  await controller.oneClick();
  assert.equal(statusCalls, 2); // preflight blockers check + post-merge strip refresh
  assert.equal(mergeCalls, 1);
  assert.deepEqual(busy, [true, false]);
  assert.match(messages.join("\n"), /已合入稳定版/);
});

test("one-click desktop-only without api matches merge messaging", async () => {
  const messages: string[] = [];
  const controller = ui.createShellDevAdoptionController(undefined, (text) => messages.push(text), () => {});
  await controller.oneClick();
  assert.match(messages.join("\n"), /Electron|桌面/);
});

test("formatDualLaneStrip surfaces stableAhead without legacy gate fields", () => {
  const strip = ui.formatDualLaneStrip({
    stableHead: "abcdef012345",
    sandboxHead: "1234567890ab",
    commitsAhead: 0,
    stableAheadOfSandbox: 2,
    needsSandboxSync: true,
    sandboxDirtyProduct: [],
    stableDirtyProduct: [" M desktop/x.ts"],
    fatalBlockers: ["没有待合入的实验改动。"],
    syncBlockers: [],
  });
  assert.equal(strip.stableHead, "abcdef0");
  assert.equal(strip.sandboxHead, "1234567");
  assert.equal(strip.stableAhead, "2");
  assert.match(strip.hint, /稳定分支比实验树新 2/);
  assert.equal(strip.showBlockers, false);
});

test("formatDualLaneStrip lists sync blockers in the strip", () => {
  const strip = ui.formatDualLaneStrip({
    stableHead: "aaa",
    sandboxHead: "bbb",
    commitsAhead: 0,
    stableAheadOfSandbox: 1,
    needsSandboxSync: true,
    sandboxDirtyProduct: [" M desktop/a.ts"],
    stableDirtyProduct: [],
    fatalBlockers: [],
    syncBlockers: ["实验区有未提交 product 改动"],
  });
  assert.equal(strip.showBlockers, true);
  assert.match(strip.blockers, /未提交/);
  assert.match(strip.hint, /未提交/);
});

test("sync refuses when syncBlockers are hard and surfaces them", async () => {
  const outputs: { text: string; tone?: string }[] = [];
  let syncCalls = 0;
  const controller = ui.createShellDevAdoptionController({
    getSandboxAdoptStatus: async () => ({
      syncBlockers: ["实验区有未提交 product 改动，刷入稳定版前请先提交或清理： M desktop/a.ts"],
      needsSandboxSync: true,
      stableAheadOfSandbox: 1,
      stableHead: "1111111",
      sandboxHead: "2222222",
    }),
    syncSandboxWorktree: async () => { syncCalls++; return { ok: true, message: "不应到达" }; },
  }, (text, tone) => outputs.push({ text, tone }), () => {});
  await controller.sync();
  assert.equal(syncCalls, 0);
  assert.equal(outputs.at(-1)?.tone, "error");
  assert.match(outputs.at(-1)?.text ?? "", /未提交/);
});

test("sync success reports before to after heads and refreshes status", async () => {
  const outputs: { text: string; tone?: string }[] = [];
  let statusCalls = 0;
  const statuses: Array<Record<string, unknown> | undefined> = [];
  const controller = ui.createShellDevAdoptionController({
    getSandboxAdoptStatus: async () => {
      statusCalls++;
      return {
        syncBlockers: [],
        needsSandboxSync: true,
        stableAheadOfSandbox: 1,
        stableHead: "aaaaaaa111",
        sandboxHead: "bbbbbbb222",
        fatalBlockers: [],
        commitsAhead: 0,
        sandboxDirtyProduct: [],
        stableDirtyProduct: [],
      };
    },
    syncSandboxWorktree: async (options) => {
      assert.equal(options?.allowTheirsMerge, undefined);
      return {
        ok: true,
        synced: true,
        message: "已用稳定版刷入实验树：bbbbbbb → aaaaaaa",
        beforeStableHead: "aaaaaaa111",
        beforeSandboxHead: "bbbbbbb222",
        afterSandboxHead: "aaaaaaa111",
      };
    },
  }, (text, tone) => outputs.push({ text, tone }), () => {}, (st) => statuses.push(st));
  await controller.sync();
  assert.ok(statusCalls >= 2);
  assert.equal(outputs.at(-1)?.tone, "ok");
  assert.match(outputs.at(-1)?.text ?? "", /aaaaaaa/);
  assert.ok(statuses.length >= 1);
});
