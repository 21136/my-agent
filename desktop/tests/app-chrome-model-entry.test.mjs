import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = (name) => readFileSync(new URL(`../src/${name}`, import.meta.url), "utf8");

test("legacy app chrome has no model selector or model-list websocket wiring", () => {
  const chrome = source("app-chrome.ts");
  assert.doesNotMatch(chrome, /id="chrome-model"|app-chrome-field-model|session\.models|session\.banner|listModels|setSessionModel|setModel/);
  assert.match(chrome, /onOpenSettings\?: \(\) => void/);
  assert.match(chrome, /onOpenModelKeys\?: \(\) => void/);
});

test("busy state continues to synchronize the app frame without controlling a model selector", () => {
  const busy = source("agent-busy.ts");
  assert.match(busy, /frame\.classList\.toggle\("is-agent-busy", busy\)/);
  assert.doesNotMatch(busy, /chrome-model|modelSelect/);
});

test("main keeps settings model entry points and does not pass the websocket client to app chrome", () => {
  const main = source("main.ts");
  assert.match(main, /const modelSettings = mountModelSettings\(client\)/);
  assert.match(main, /modelSettings\.openSettings\(\)/);
  assert.doesNotMatch(main, /mountAppChrome\(chromeRoot,\s*\{[\s\S]*?client:/);
});

