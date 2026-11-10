/**
 * Isolated layout/interaction checks for companion UI batch 2 (4–6, settings, briefing).
 * Synthetic mount only — not Win32 glass parity.
 */
import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { createRequire } from "node:module";
import { buildIsolatedShellStyles } from "./isolated-shell-styles.mjs";
import { renderWorkRecordsBriefingShell } from "./companion-ui-batch2-styles-load.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, "..");
const req = createRequire(path.join(desktop, "package.json"));
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(process.env.HOME || "", ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
let playwrightReady = false;
try {
  req.resolve(playwrightPath.endsWith("playwright") ? "playwright" : path.join(playwrightPath, "..", "playwright"));
  playwrightReady = true;
} catch {
  try {
    req.resolve("playwright");
    playwrightReady = true;
  } catch {
    playwrightReady = false;
  }
}

function startStylesOnlyServer(injectCss) {
  return new Promise((resolve) => {
    const html = `<!DOCTYPE html><html data-theme="light"><head><meta charset="utf-8" /><style>${injectCss}</style></head>
<body class="unified-shell" data-perspective="project" data-main-focus="chat" data-rail="now">
<div id="fixture"></div></body></html>`;
    const server = http.createServer((req, res) => {
      if (req.url === "/") {
        res.writeHead(200, { "Content-Type": "text/html" });
        res.end(html);
        return;
      }
      res.writeHead(404).end();
    });
    server.listen(0, "127.0.0.1", () => {
      const { port } = server.address();
      resolve({
        url: `http://127.0.0.1:${port}/`,
        close: () => new Promise((done) => server.close(() => done())),
      });
    });
  });
}

async function withPlaywrightPage(run) {
  const { chromium } = req("playwright");
  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext();
    const page = await context.newPage();
    await run(page);
    await context.close();
  } finally {
    await browser.close();
  }
}

test("briefing panel is collapsed by default with bounded scroll region", { skip: playwrightReady ? false : "playwright not installed" }, async () => {
  const { injectCss } = buildIsolatedShellStyles(desktop);
  const host = await startStylesOnlyServer(injectCss);
  try {
    await withPlaywrightPage(async (page) => {
      await page.goto(host.url);
      const longBody = "<p>".repeat(120).concat("要求正文 A ".repeat(40), "</p>");
      const shell = renderWorkRecordsBriefingShell("工作记录摘要（历史）", longBody);
      await page.evaluate((html) => {
        document.querySelector("#fixture").innerHTML = `<article class="unified-turn unified-turn-assistant unified-message-bubble">${html}</article>`;
      }, shell);
      const details = page.locator(".unified-history-briefing");
      assert.equal(await details.evaluate((el) => el instanceof HTMLDetailsElement && !el.open), true);
      const body = page.locator(".unified-history-briefing-body");
      const metrics = await body.evaluate((el) => {
        const style = getComputedStyle(el);
        return { maxHeight: style.maxHeight, overflowY: style.overflowY };
      });
      assert.notEqual(metrics.maxHeight, "none");
      assert.match(metrics.overflowY, /auto|scroll/);
    });
  } finally {
    await host.close();
  }
});

test("settings category bar stays sticky inside tool region", { skip: playwrightReady ? false : "playwright not installed" }, async () => {
  const { injectCss } = buildIsolatedShellStyles(desktop);
  const host = await startStylesOnlyServer(injectCss);
  try {
    await withPlaywrightPage(async (page) => {
      await page.goto(host.url);
      await page.evaluate(() => {
        document.body.classList.add("is-tool-fullpage");
        document.body.dataset.rail = "settings";
        document.querySelector("#fixture").innerHTML = `
          <section class="unified-tool-region" style="height:420px;overflow:auto;display:flex;flex-direction:column">
            <nav class="settings-category-nav" data-test-nav><button type="button">外观</button><button type="button">模型</button></nav>
            <div class="settings-pane" style="min-height:1200px;padding:1rem">长内容</div>
          </section>`;
      });
      const pos = await page.locator("[data-test-nav]").evaluate((el) => getComputedStyle(el).position);
      assert.equal(pos, "sticky");
    });
  } finally {
    await host.close();
  }
});

test("music page uses glass card not full opaque stage", { skip: playwrightReady ? false : "playwright not installed" }, async () => {
  const { injectCss } = buildIsolatedShellStyles(desktop);
  const host = await startStylesOnlyServer(injectCss);
  try {
    await withPlaywrightPage(async (page) => {
      await page.goto(host.url);
      await page.evaluate(() => {
        document.body.dataset.rail = "music";
        document.querySelector("#fixture").innerHTML = `
          <section class="unified-tool-region"><div class="music-page" data-test-music-card><header class="music-page-heading"><h1>听歌</h1></header></div></section>`;
      });
      const card = page.locator("[data-test-music-card].music-page");
      await card.waitFor({ state: "visible" });
      const metrics = await card.evaluate((el) => {
        const style = getComputedStyle(el);
        return {
          tag: el.tagName,
          className: el.className,
          borderRadius: style.borderRadius,
          borderTopWidth: style.borderTopWidth,
          backdropFilter: style.backdropFilter,
          webkitBackdropFilter: style.webkitBackdropFilter,
          backgroundColor: style.backgroundColor,
          maxHeight: style.maxHeight,
        };
      });
      assert.equal(metrics.tag, "DIV");
      assert.match(metrics.className, /\bmusic-page\b/);
      assert.notEqual(metrics.borderRadius, "0px");
      assert.notEqual(metrics.maxHeight, "none");
      const backdrop = `${metrics.backdropFilter} ${metrics.webkitBackdropFilter}`;
      assert.match(backdrop, /blur/i, "glass card should apply backdrop blur on .music-page");
      const borderPx = Number.parseFloat(metrics.borderTopWidth);
      assert.ok(borderPx > 0, "music card should render a bordered surface");
      const alpha = metrics.backgroundColor.match(/rgba?\([^)]+,\s*([\d.]+)\s*\)/)?.[1];
      const opaqueEnough = alpha !== undefined
        ? Number.parseFloat(alpha) > 0.05
        : metrics.backgroundColor !== "rgba(0, 0, 0, 0)";
      assert.ok(
        opaqueEnough || /blur/i.test(backdrop),
        `expected glass surface on .music-page, got background=${metrics.backgroundColor} backdrop=${backdrop}`,
      );
    });
  } finally {
    await host.close();
  }
});
