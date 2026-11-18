import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { renderShellDevPanel } from "../src/shells/unified/shell-dev-panel.ts";

class FakeButton {
  hidden = false;
  disabled = false;
  textContent = "";
  listeners = new Map<string, () => void>();
  addEventListener(type: string, listener: () => void) { this.listeners.set(type, listener); }
  click() { this.listeners.get("click")?.(); }
}


test("双车道菜单提供默认隐藏的自动改造入口", () => {
  const html = renderShellDevPanel("", "");
  assert.match(html, /data-self-evolution-auto-launch/);
  assert.match(html, /启动自动改造/);
  assert.match(html, /data-self-evolution-auto-launch[^>]*hidden/);
});

test("自动改造入口在没有桥接能力时保持隐藏且不可用", async () => {
  const { bindSelfEvolutionAutoLaunch } = await import("../src/shells/unified/shell-dev-panel.ts");
  const button = new FakeButton();
  const feedback: Array<[string, string]> = [];
  await bindSelfEvolutionAutoLaunch(button as never, undefined, (message, tone) => feedback.push([message, tone]));
  assert.equal(button.hidden, true);
  assert.equal(button.disabled, true);
  assert.equal(feedback.length, 0);
});

test("自动改造入口处理可用性、重复点击和复用反馈", async () => {
  const { bindSelfEvolutionAutoLaunch } = await import("../src/shells/unified/shell-dev-panel.ts");
  const button = new FakeButton();
  const feedback: Array<[string, string]> = [];
  let finish!: (result: { ok: true; reused: boolean }) => void;
  let calls = 0;
  const api = {
    launchSelfEvolutionAutoAvailable: async () => true,
    launchSelfEvolutionAuto: () => { calls++; return new Promise<{ ok: true; reused: boolean }>(resolve => { finish = resolve; }); },
  };
  await bindSelfEvolutionAutoLaunch(button as never, api, (message, tone) => feedback.push([message, tone]));
  assert.equal(button.hidden, false);
  assert.equal(button.disabled, false);
  button.click();
  button.click();
  assert.equal(calls, 1);
  assert.equal(button.disabled, true);
  finish({ ok: true, reused: true });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(button.disabled, false);
  assert.deepEqual(feedback.at(-1), ["已连接到正在运行的自动改造任务。", "ok"]);
});

test("自动改造失败展示中文错误，preload 使用约定 IPC channel", async () => {
  const { bindSelfEvolutionAutoLaunch } = await import("../src/shells/unified/shell-dev-panel.ts");
  const button = new FakeButton();
  const feedback: Array<[string, string]> = [];
  const api = {
    launchSelfEvolutionAuto: async () => ({ ok: false as const, message: "稳定区已有未处理变更" }),
  };
  await bindSelfEvolutionAutoLaunch(button as never, api, (message, tone) => feedback.push([message, tone]));
  button.click();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(feedback.at(-1), ["稳定区已有未处理变更", "error"]);

  const preload = readFileSync(new URL("../electron/preload.ts", import.meta.url), "utf8");
  assert.match(preload, /self-evolution-auto:launch/);
  assert.match(preload, /self-evolution-auto:available/);
});


test("显式不可用时入口仍隐藏", async () => {
  const { bindSelfEvolutionAutoLaunch } = await import("../src/shells/unified/shell-dev-panel.ts");
  const button = new FakeButton();
  const api = { launchSelfEvolutionAutoAvailable: async () => false, launchSelfEvolutionAuto: async () => ({ ok: true as const, reused: false }) };
  await bindSelfEvolutionAutoLaunch(button as never, api, () => assert.fail("不可用时不应触发反馈"));
  assert.equal(button.hidden, true);
  assert.equal(button.disabled, true);
});
