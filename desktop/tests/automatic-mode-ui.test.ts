import assert from "node:assert/strict";
import test from "node:test";

import { mountAppChrome } from "../src/app-chrome.ts";
import { RUNAWAY_BLOCKER, RUNAWAY_STAGE, RUNAWAY_STATUS } from "../src/copy/user-messages.ts";

function fakeElement() {
  const attributes = new Map<string, string>();
  return {
    value: "",
    innerHTML: "",
    disabled: false,
    title: "",
    attributes,
    classList: { add() {}, remove() {}, toggle() {} },
    querySelectorAll() { return []; },
    addEventListener() {},
    setAttribute(name: string, value: string) { attributes.set(name, value); },
    append() {},
  };
}

test("automatic mode switch uses neutral visible and accessible copy", () => {
  const selectors = [
    "#chrome-theme", "#chrome-model", "#chrome-cli", "#chrome-pet",
    "#chrome-route-notice", "#chrome-runaway", "#chrome-runaway-state",
    ".app-chrome-menu",
  ];
  const elements = new Map(selectors.map((selector) => [selector, fakeElement()]));
  const root = {
    innerHTML: "",
    querySelector(selector: string) { return elements.get(selector) ?? null; },
  };
  const subscribers: Array<(event: unknown) => void> = [];
  const client = {
    onEvent(callback: (event: unknown) => void) { subscribers.push(callback); return () => {}; },
    listModels() {},
  };
  const oldWindow = Object.getOwnPropertyDescriptor(globalThis, "window");
  const oldStorage = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: { setTimeout: () => 1, clearTimeout() {} },
  });
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: { getItem: () => null, setItem() {} },
  });

  try {
    mountAppChrome(root as unknown as HTMLElement, {
      onSwitchToCli: async () => {},
      client: client as never,
    });
    assert.match(root.innerHTML, /<span class="app-chrome-runaway-name">自动<\/span>/);

    for (const notify of subscribers) {
      notify({ type: "project.state", project_id: "demo", runaway_enabled: true });
    }
    const button = elements.get("#chrome-runaway")!;
    assert.equal(button.attributes.get("aria-label"), "自动模式 开");
    assert.equal(button.title, "自动模式已开启；点击暂停");
    assert.equal(button.attributes.get("aria-pressed"), "true");
  } finally {
    if (oldWindow) Object.defineProperty(globalThis, "window", oldWindow);
    else Reflect.deleteProperty(globalThis, "window");
    if (oldStorage) Object.defineProperty(globalThis, "localStorage", oldStorage);
    else Reflect.deleteProperty(globalThis, "localStorage");
  }
});

test("shared mode, status, and recovery copy avoids the old mode name", () => {
  const visibleCopy = [
    ...Object.values(RUNAWAY_BLOCKER),
    ...Object.values(RUNAWAY_STATUS),
    ...Object.values(RUNAWAY_STAGE),
  ];
  assert.ok(visibleCopy.every((value) => !value.includes("狂奔")), visibleCopy.join("\n"));
});