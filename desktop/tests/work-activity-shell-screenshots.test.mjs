import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edgePath = process.env.ISOLATED_TEST_BROWSER || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const syntheticReply = ["## 隔离预览：长回复", "", ...Array.from({ length: 30 }, (_, i) => `这是第 ${i + 1} 段用于检查聊天阅读节奏的示例文本。内容为合成文本，不代表真实任务结果。`)].join("\n\n");

test("real unified shell mount keeps work activity in left whitespace without changing chat geometry", {
  skip: !existsSync(playwrightPath) || !existsSync(edgePath), timeout: 60000,
}, async () => {
  const bundle = await esbuild.build({
    entryPoints: [path.join(desktopRoot, "src/shells/unified/index.ts")], bundle: true, write: false,
    platform: "browser", format: "iife", globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"https://isolated.invalid/entry.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "no-css", setup(build) { build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
  const themeCss = readFileSync(path.join(desktopRoot, "src/styles/theme.css"), "utf8");
  const shellCss = ["unified.css", "neon-twin.css", "sidebar-v2.css", "single-chat-tools.css", "composer-companion.css", "current-work-overview.css"]
    .map(file => readFileSync(path.join(desktopRoot, "src/shells/unified", file), "utf8")).join("\n");
  const candidate = readFileSync(path.join(desktopRoot, "src/shells/unified/assets/chat-background-candidate.png")).toString("base64");
  const css = `${themeCss}\n${shellCss.replaceAll('url("./assets/chat-background-candidate.png")', `url("data:image/png;base64,${candidate}")`)}`;
  const browser = await require(playwrightPath).chromium.launch({ headless: true, executablePath: edgePath, args: ["--disable-background-networking", "--no-first-run"] });
  const output = path.join(os.tmpdir(), `work-activity-mounted-${process.pid}`);
  const screens = [["wide", 1920, 1080], ["ordinary", 960, 720], ["narrow", 720, 480], ["tight", 640, 480]];
  try {
    for (const [label, width, height] of screens) {
      const context = await browser.newContext({ serviceWorkers: "block", viewport: { width, height } });
      const errors = [];
      const requests = [];
      await context.route("**/*", route => {
        requests.push(route.request().url());
        return route.request().url() === "https://isolated.invalid/"
          ? route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><html data-theme='light'><div id='root'></div></html>" })
          : route.abort();
      });
      const page = await context.newPage();
      page.on("pageerror", error => errors.push(error.message));
      await page.goto("https://isolated.invalid/");
      await page.addStyleTag({ content: `html,body,#root{height:100%;margin:0}${css}` });
      await page.addScriptTag({ content: bundle.outputFiles[0].text });
      await page.evaluate(() => {
        document.documentElement.dataset.theme = "light";
        window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
        const handlers = new Set();
        const client = new Proxy({ onEvent(handler) { handlers.add(handler); return () => handlers.delete(handler); }, listSessionsMerged: async () => [] }, {
          get: (target, key) => key in target ? target[key] : (..._args) => String(key).startsWith("on") ? () => {} : undefined,
        });
        window.emitSynthetic = event => { for (const handler of handlers) handler(event); };
        window.disposeUnified = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), client);
      });
      assert.equal(await page.locator("#unified-sidebar").isVisible(), false);
      assert.equal(await page.locator("#sidebar-new-chat").isVisible(), false);
      await page.evaluate(({ label, text }) => {
        window.emitSynthetic({ type: "session.banner", session_id: `isolated-${label}`, project_id: "fixture", project_plan_status: "draft", topics: [], turn_mode: "agent", turn_mode_label: "", phase: "S4" });
        const run = `fixture-run-${label}`;
        window.emitSynthetic({ type: "turn.start", run_id: run, intent: "execute", intent_label: "执行" });
        for (let i = 0; i < 7; i++) {
          window.emitSynthetic({ type: "tool.start", run_id: run, call_id: `fixture-call-${i}`, tool: "file_read", summary: `读取历史动作 ${i}` });
          window.emitSynthetic({ type: "tool.end", run_id: run, call_id: `fixture-call-${i}`, tool: "file_read", ok: true, summary: `读取历史动作 ${i}`, path: i === 6 ? "desktop/src/shells/unified/index.ts" : undefined });
        }
        window.emitSynthetic({ type: "tool.start", run_id: run, call_id: "fixture-fail", tool: "file_write", summary: "保存文件" });
        window.emitSynthetic({ type: "tool.end", run_id: run, call_id: "fixture-fail", tool: "file_write", ok: false, summary: "权限拒绝：合成失败详情", logs_tail: "fixture permission denied" });
        window.emitSynthetic({ type: "assistant.delta", run_id: run, text });
        window.emitSynthetic({ type: "assistant.done", run_id: run, text });
        window.emitSynthetic({ type: "turn.end", run_id: run, ok: true, finish_reason: "stop" });
      }, { label, text: syntheticReply });
      const rail = page.locator("#work-activity-rail");
      const shouldShow = label === "wide";
      assert.equal(await rail.isVisible(), shouldShow, "rail appears only when the measured left gutter can contain it");
      const baseline = await page.evaluate(() => {
        const rail = document.querySelector("#work-activity-rail");
        const wasHidden = rail.hidden;
        rail.hidden = true;
        const geometry = {
          chat: document.querySelector("#unified-chat").getBoundingClientRect().toJSON(),
          composer: document.querySelector("#unified-composer-dock").getBoundingClientRect().toJSON(),
        };
        rail.hidden = wasHidden;
        return geometry;
      });
      const occupied = await page.evaluate(() => ({
        chat: document.querySelector("#unified-chat").getBoundingClientRect().toJSON(),
        composer: document.querySelector("#unified-composer-dock").getBoundingClientRect().toJSON(),
      }));
      assert.deepEqual(occupied, baseline, "activity never changes chat viewport/composer geometry");
      assert.equal(await page.locator('[data-tool-call^="fixture-call-"]').count(), 0, "routine actions are not duplicated as process cards in chat");
      assert.equal(await page.locator('[data-tool-call="fixture-fail"]').count(), 0, "failures are not duplicated as process cards in chat");
      assert.doesNotMatch(await page.locator("#unified-chat").innerText(), /权限拒绝：合成失败详情/);
      assert.equal(await page.locator("#unified-chat [data-process-toggle]").count(), 0, "process cards are removed from the chat lane");
      const historyEntry = label === "wide" ? rail.locator("#work-activity-toggle") : page.locator("#work-activity-compact-toggle");
      assert.ok(await historyEntry.isVisible(), "activity history has a non-layout entry at every viewport size");
      const chatBeforeHistory = await page.evaluate(() => ({
        chat: document.querySelector("#unified-chat").getBoundingClientRect().toJSON(),
        composer: document.querySelector("#unified-composer-dock").getBoundingClientRect().toJSON(),
      }));
      await historyEntry.click();
      const history = page.locator("#work-activity-history");
      assert.ok(await history.isVisible(), "click opens the temporary complete-history view");
      assert.equal(await history.locator('[data-tool-call^="fixture-call-"]').count(), 7, "complete routine history remains available");
      assert.equal(await history.locator('[data-tool-call="fixture-fail"]').count(), 1, "failed action detail remains available");
      await history.locator(".unified-tool-logs-inline summary").click();
      assert.match(await history.innerText(), /fixture permission denied/);
      assert.match(await history.innerText(), /权限拒绝：合成失败详情/);
      const chatWithHistory = await page.evaluate(() => ({
        chat: document.querySelector("#unified-chat").getBoundingClientRect().toJSON(),
        composer: document.querySelector("#unified-composer-dock").getBoundingClientRect().toJSON(),
      }));
      assert.deepEqual(chatWithHistory, chatBeforeHistory, "temporary history does not compress chat or composer");
      if (label === "wide") await page.screenshot({ path: `${output}-history.png` });
      await page.keyboard.press("Escape");
      assert.equal(await history.isVisible(), false, "Escape closes the temporary history");
      await historyEntry.click();
      await page.locator("#work-activity-history-close").click();
      assert.equal(await history.isVisible(), false, "close button dismisses the temporary history");
      if (shouldShow) {
        const card = await rail.boundingBox();
        const text = await page.locator("#unified-chat .unified-turn-assistant").first().boundingBox();
        assert.ok(card && text && card.x + card.width <= text.x, "rail stays entirely in measured left whitespace and clear of message text");
        await page.setViewportSize({ width: 960, height: 720 });
        await page.waitForTimeout(50);
        assert.equal(await rail.isVisible(), false, "resizing to an ordinary window hides the rail when left whitespace is insufficient");
        assert.ok(await page.locator("#work-activity-compact-toggle").isVisible(), "hidden rail has an overlay entry without changing chat geometry");
      }
      assert.ok(await page.locator("#unified-chat .unified-markdown p").count() >= 10, "long message renders in chat");
      assert.equal(await page.locator("#unified-chat").evaluate(el => el.scrollHeight > el.clientHeight), true, "long chat remains scrollable behind the independent composer");
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, "no horizontal overflow");
      await page.screenshot({ path: `${output}-${label}.png` });
      assert.deepEqual(errors, [], "no renderer exceptions");
      assert.ok(requests.every(url => url.startsWith("https://isolated.invalid/")), "isolated mount made no backend/model/network requests");
      await page.evaluate(() => window.disposeUnified());
      await context.close();
    }
    console.log(`Isolated real-shell screenshots (synthetic fixture data; no backend/model): ${screens.map(([label]) => `${output}-${label}.png`).join(", ")}; expanded: ${output}-expanded.png`);
  } finally {
    await browser.close();
  }
});

