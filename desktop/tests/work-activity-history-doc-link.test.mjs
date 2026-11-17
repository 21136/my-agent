import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const require = createRequire(import.meta.url);
const esbuild = require("esbuild");
const playwrightPath =
  process.env.CODEX_PLAYWRIGHT_PATH ||
  path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const edgePath =
  process.env.ISOLATED_TEST_BROWSER ||
  (process.platform === "win32"
    ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
    : "");
const desktopRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

test("work activity history doc link opens reader with visible markdown", {
  skip: !existsSync(playwrightPath) || !edgePath,
  timeout: 90000,
}, async () => {
  const skillRel = "evolve/skills/synthetic-fixture/SKILL.md";
  const skillBody = "# Synthetic Skill Fixture\n\nvisible-body-marker-8842\n";
  const bundle = await esbuild.build({
    entryPoints: [path.join(desktopRoot, "src/shells/unified/index.ts")],
    bundle: true,
    write: false,
    platform: "browser",
    format: "iife",
    globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"https://isolated.invalid/entry.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{
      name: "no-css",
      setup(build) {
        build.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" }));
      },
    }],
    logLevel: "silent",
  });
  const themeCss = readFileSync(path.join(desktopRoot, "src/styles/theme.css"), "utf8");
  const shellCss = ["unified.css", "neon-twin.css", "sidebar-v2.css", "single-chat-tools.css", "composer-companion.css", "current-work-overview.css", "message-reading.css"]
    .map((file) => readFileSync(path.join(desktopRoot, "src/shells/unified", file), "utf8"))
    .join("\n");
  const candidate = readFileSync(
    path.join(desktopRoot, "src/shells/unified/assets/chat-background-candidate.png"),
  ).toString("base64");
  const css = `${themeCss}\n${shellCss.replaceAll('url("./assets/chat-background-candidate.png")', `url("data:image/png;base64,${candidate}")`)}`;

  const browser = await require(playwrightPath).chromium.launch({
    headless: true,
    executablePath: edgePath,
    args: ["--disable-background-networking", "--no-first-run"],
  });
  try {
    const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
    await context.route("**/*", (route) =>
      route.request().url() === "https://isolated.invalid/"
        ? route.fulfill({ status: 200, contentType: "text/html", body: "<!doctype html><html data-theme='light'><div id='root'></div></html>" })
        : route.abort(),
    );
    const page = await context.newPage();
    await page.goto("https://isolated.invalid/");
    await page.addStyleTag({ content: `html,body,#root{height:100%;margin:0}${css}` });
    await page.addScriptTag({ content: bundle.outputFiles[0].text });
    await page.evaluate(({ skillRel, skillBody }) => {
      document.documentElement.dataset.theme = "light";
      window.myAgentDesktop = {
        readAgentTextPreview: async (p) =>
          p === skillRel
            ? { text: skillBody, truncated: false, language: "markdown", filename: "SKILL.md" }
            : null,
        setBilibiliViewState: async () => {},
        setShellDevViewState: async () => {},
      };
      const handlers = new Set();
      const client = new Proxy(
        { onEvent(handler) { handlers.add(handler); return () => handlers.delete(handler); }, listSessionsMerged: async () => [] },
        { get: (target, key) => (key in target ? target[key] : (..._args) => (String(key).startsWith("on") ? () => {} : undefined)) },
      );
      window.emitSynthetic = (event) => { for (const handler of handlers) handler(event); };
      window.disposeUnified = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), client);
    }, { skillRel, skillBody });

    await page.evaluate(() => {
      window.emitSynthetic({
        type: "session.banner",
        session_id: "isolated-doc-link",
        topics: [],
        turn_mode: "agent",
        turn_mode_label: "",
        phase: "S4",
      });
      const run = "fixture-doc-link-run";
      window.emitSynthetic({ type: "turn.start", run_id: run, intent: "execute", intent_label: "执行" });
      window.emitSynthetic({
        type: "tool.start",
        run_id: run,
        call_id: "fixture-explore",
        tool: "explore",
        summary: "探索代码库 SKILL.md",
        path: skillRel,
      });
      window.emitSynthetic({
        type: "tool.end",
        run_id: run,
        call_id: "fixture-explore",
        tool: "explore",
        ok: false,
        summary: "探索代码库 SKILL.md · 先把来龙去脉探测清楚",
        path: skillRel,
      });
      window.emitSynthetic({ type: "turn.end", run_id: run, ok: false, finish_reason: "error" });
    });

    const historyEntry = page.locator("#work-activity-compact-toggle");
    await historyEntry.click();
    const history = page.locator("#work-activity-history");
    assert.ok(await history.isVisible());
    const link = history.locator('[data-action="open-agent-doc"][data-doc-path="' + skillRel + '"]');
    assert.ok(await link.isVisible());
    await link.click();
    assert.equal(await history.isVisible(), false, "modal closes so document reader is not obscured");
    const reader = page.locator("#unified-document");
    assert.ok(await reader.isVisible());
    await page.waitForFunction(
      () => (document.querySelector("#unified-document")?.textContent || "").includes("visible-body-marker-8842"),
    );
    assert.match(await reader.innerText(), /Synthetic Skill Fixture/);
    assert.match(await reader.innerText(), /visible-body-marker-8842/);
    await context.close();
  } finally {
    await browser.close();
  }
});