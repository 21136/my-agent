import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/shells/unified/composer-companion.css", import.meta.url), "utf8");

test("unified composer renders its input without starting Live2D or adding an avatar placeholder", () => {
  const footer = shell.match(/<footer class="unified-composer"[\s\S]*?<\/footer>/)?.[0];
  assert.ok(footer);
  assert.doesNotMatch(footer, /unified-composer-companion|unified-composer-live2d|composer-companion\.png/);
  assert.doesNotMatch(shell, /mountComposerLive2D|disposeComposerLive2D/);
  assert.doesNotMatch(shell, /import\s*\{\s*mountComposerLive2D\s*\} from "\.\/composer-live2d"/);
  for (const id of ["unified-input", "unified-mode-toggle", "unified-stop", "unified-send"]) {
    assert.equal((footer.match(new RegExp(`id="${id}"`, "g")) || []).length, 1);
  }
  assert.match(footer, /class="unified-mode-toggle"[^>]*role="group"/);
  assert.match(footer, /data-mode-value="ask"[^>]*>只聊</);
  assert.match(footer, /data-mode-value="agent"[^>]*>动手</);
  assert.match(footer, /aria-label="输入消息"/);
  assert.match(footer, /aria-label="发送消息"/);
  assert.match(footer, /id="unified-input" rows="1"[^>]*placeholder="和小魔王说点什么…"/);
  assert.match(shell, /import "\.\/composer-companion\.css"/);
});
test("composer is a single lightweight card with a compact growing multiline input", () => {
  assert.match(css, /grid-template-areas:[\s\S]*?"attachments attachments attachments attachments"\s*"input mode stop send"/);
  assert.match(css, /#unified-composer-dock\s*\{[^}]*border:\s*0/);
  assert.match(css, /#unified-composer\s*\{[^}]*border:\s*1px solid/);
  assert.match(css, /#unified-composer \.unified-input\s*\{[^}]*border:\s*0/);
  assert.match(css, /#unified-composer \.unified-input\s*\{[^}]*min-height:\s*2\.5rem/);
  assert.match(css, /#unified-composer \.unified-input\s*\{[^}]*max-height:\s*8rem/);
  assert.match(shell, /Math\.min\(input\.scrollHeight, 200\)/);
  assert.match(shell, /id="unified-input" rows="1"/);
  assert.match(css, /#unified-mode-toggle[^}]*border:\s*0/);
  assert.match(css, /#unified-composer #unified-send[^}]*border-radius:\s*12px/);
  assert.match(css, /#unified-composer #unified-send:focus-visible/);
  assert.match(css, /#unified-composer \.unified-input:focus-visible\s*\{[^}]*outline:\s*none/);
  assert.match(css, /#unified-composer \.unified-input:focus,\s*html\[data-theme\] #unified-composer \.unified-input:focus-visible\s*\{[^}]*outline:\s*none/);
  assert.match(css, /#unified-composer:focus-within\s*\{[^}]*box-shadow:[^}]*0 0 0 2px/s);
  assert.match(css, /max-width:\s*920px/);
  assert.doesNotMatch(css, /animation:/);
});

test("composer reserves no companion space and contains no Live2D styles", () => {
  assert.doesNotMatch(css, /unified-composer-companion|unified-composer-live2d/);
  assert.match(css, /grid-template-columns:\s*minmax\(0, 1fr\) auto auto 2\.2rem/);
  assert.match(css, /grid-template-areas:[\s\S]*?"attachments attachments attachments attachments"\s*"input mode stop send"/);
  assert.doesNotMatch(css, /64px|grid-area:\s*companion/);
});

test("Live2D model image remains available for future restoration", () => {
  const png = readFileSync(new URL("../src/shells/unified/assets/composer-companion.png", import.meta.url));
  assert.equal(png.subarray(1, 4).toString(), "PNG");
  assert.equal(png[25], 6, "RGBA PNG required");
  assert.ok(png.readUInt32BE(16) <= 512);
});
const browserRequire = createRequire(import.meta.url);
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const browserPath = process.env.ISOLATED_TEST_BROWSER || (process.platform === "win32" ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" : "");
const canRunGeometryBrowserTest = existsSync(playwrightPath) && existsSync(browserPath);

test("composer keeps its placeholder visible in the compact input row", { skip: !canRunGeometryBrowserTest, timeout: 30000 }, async () => {
  const playwright = browserRequire(playwrightPath);
  const theme = readFileSync(new URL("../src/styles/theme.css", import.meta.url), "utf8");
  const baseCss = readFileSync(new URL("../src/shells/unified/unified.css", import.meta.url), "utf8");
  const projectCss = readFileSync(new URL("../src/shells/unified/neon-twin.css", import.meta.url), "utf8");
  const browser = await playwright.chromium.launch({ headless: true, executablePath: browserPath });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.setContent(`<!doctype html><html data-theme="dark"><head><style>${theme}\n${baseCss}\n${projectCss}\n${css}\nhtml,body,#root{height:100%;width:100vw;margin:0}#root{display:block!important;width:100vw!important}.unified-main{width:100vw!important;height:100%}</style></head><body><div id="root" class="unified-shell" data-main-focus="chat" data-perspective="project"><main class="unified-main"><section class="unified-stage"><main id="unified-chat" class="unified-chat"></main></section><div class="unified-composer-dock" id="unified-composer-dock"><footer class="unified-composer" id="unified-composer"><div id="unified-mode-toggle" class="unified-mode-toggle"><button class="unified-mode-seg">只聊</button><button class="unified-mode-seg">动手</button></div><button id="unified-stop" hidden>停止</button><textarea id="unified-input" class="unified-input" rows="1" placeholder="和小魔王说点什么…"></textarea><button id="unified-send">↑</button></footer></div></main></div></body></html>`);
    const input = page.locator("#unified-input");
    assert.equal(await input.getAttribute("placeholder"), "和小魔王说点什么…");
    assert.equal(await page.locator("#unified-composer canvas, #unified-composer img, .unified-composer-companion").count(), 0);
    for (const width of [1440, 768, 390]) {
      await page.setViewportSize({ width, height: 844 });
      const geometry = await input.evaluate((element) => {
        const rect = element.getBoundingClientRect();
        return { width: rect.width, height: rect.height, placeholder: getComputedStyle(element, "::placeholder").color };
      });
      assert.ok(geometry.width > 80, `${width}px input unexpectedly collapsed: ${JSON.stringify(geometry)}`);
      assert.ok(geometry.height >= 36, `${width}px input lost compact control height: ${JSON.stringify(geometry)}`);
      assert.notEqual(geometry.placeholder, "rgba(0, 0, 0, 0)");
    }
  } finally {
    await browser.close();
  }
});
