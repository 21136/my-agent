import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import test from "node:test";
const shell = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/shells/unified/composer-companion.css", import.meta.url), "utf8");

test("composer embeds one decorative static companion without replacing chat controls", () => {
  const footer = shell.match(/<footer class="unified-composer"[\s\S]*?<\/footer>/)?.[0];
  assert.ok(footer);
  assert.match(footer, /class="unified-composer-companion"[^>]*aria-hidden="true"/);
  assert.match(footer, /composer-companion\.png/);
  assert.match(footer, /draggable="false"/);
  for (const id of ["unified-input", "unified-mode-toggle", "unified-stop", "unified-send"]) {
    assert.equal((footer.match(new RegExp(`id="${id}"`, "g")) || []).length, 1);
  }
  assert.match(footer, /class="unified-mode-toggle"[^>]*role="group"/);
  assert.match(footer, /data-mode-value="ask"[^>]*>只聊</);
  assert.match(footer, /data-mode-value="agent"[^>]*>动手</);
  assert.match(footer, /aria-label="输入消息"/);
  assert.match(footer, /aria-label="发送消息"/);
  assert.match(footer, /id="unified-input" rows="1"/);
  assert.match(shell, /import "\.\/composer-companion\.css"/);
});

test("composer stays compact and lets the input use its full row", () => {
  assert.match(css, /grid-template-areas:[\s\S]*?"attachments attachments attachments"\s*"input input input"\s*"mode stop send"/);
  assert.doesNotMatch(css, /grid-area:\s*companion/);
  assert.match(css, /#unified-composer \.unified-input\s*\{[^}]*min-height:\s*2\.5rem/);
  assert.match(css, /#unified-composer \.unified-input\s*\{[^}]*max-height:\s*8rem/);
  assert.match(shell, /Math\.min\(input\.scrollHeight, 200\)/);
  assert.doesNotMatch(css, /#unified-composer\s*\{[^}]*margin-top:/s);
  assert.match(css, /max-width:\s*920px/);
  assert.doesNotMatch(css, /animation:/);
});

test("companion floats at the composer upper-right without reserving a row", () => {
  const companionRule = css.match(/\.unified-composer-companion\s*\{([^}]*)\}/)?.[1] || "";
  assert.match(companionRule, /position:\s*absolute/);
  assert.match(companionRule, /right:\s*calc\(468px\s*-\s*50cqw\)/);
  assert.match(companionRule, /width:\s*min\(var\(--composer-companion-width,\s*132px\),\s*calc\(50cqw\s*-\s*476px\)\)/);
  assert.match(companionRule, /bottom:\s*calc\(100%\s*-\s*5px\)/);
  assert.match(companionRule, /pointer-events:\s*none/);
  assert.match(css, /--composer-companion-width:\s*132px/);
  assert.match(css, /container-type:\s*inline-size/);
  assert.match(css, /@container\s*\(max-width:\s*1096px\)[\s\S]*?\.unified-composer-companion\s*\{[^}]*display:\s*none/);
  assert.match(css, /@media\s*\(max-width:\s*680px\)[\s\S]*?\.unified-composer-companion\s*\{[^}]*display:\s*none/);
  assert.match(css, /\.unified-composer-live2d\s*\{[^}]*pointer-events:\s*none/);
});

test("companion image is a transparent cutout rather than a rectangular screenshot", () => {
  const png = readFileSync(new URL("../src/shells/unified/assets/composer-companion.png", import.meta.url));
  assert.equal(png.subarray(1, 4).toString(), "PNG");
  assert.equal(png[25], 6, "RGBA PNG required");
  assert.ok(png.readUInt32BE(16) <= 512);
});

const browserRequire = createRequire(import.meta.url);
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH || path.join(os.homedir(), ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const browserPath = process.env.ISOLATED_TEST_BROWSER || (process.platform === "win32" ? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" : "");
const canRunGeometryBrowserTest = existsSync(playwrightPath) && existsSync(browserPath);

test("companion stays outside a long chat transcript and hides when the side gutter is too narrow", { skip: !canRunGeometryBrowserTest, timeout: 30000 }, async () => {
  const playwright = browserRequire(playwrightPath);
  const theme = readFileSync(new URL("../src/styles/theme.css", import.meta.url), "utf8");
  const baseCss = readFileSync(new URL("../src/shells/unified/unified.css", import.meta.url), "utf8");
  const projectCss = readFileSync(new URL("../src/shells/unified/neon-twin.css", import.meta.url), "utf8");
  const image = `data:image/png;base64,${readFileSync(new URL("../src/shells/unified/assets/composer-companion.png", import.meta.url)).toString("base64")}`;
  const longReply = Array.from({ length: 80 }, (_, index) => `<p>第 ${index + 1} 段长回复：这是用于检查长聊天内容与右侧装饰角色是否发生几何重叠的重复文本。</p>`).join("");
  const browser = await playwright.chromium.launch({ headless: true, executablePath: browserPath });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
    await page.setContent(`<!doctype html><html data-theme="dark"><head><style>${theme}\n${baseCss}\n${projectCss}\n${css}\nhtml,body,#root{height:100%;width:100vw;margin:0}#root{display:block!important;width:100vw!important}.unified-main{width:100vw!important;height:100%}</style></head><body><div id="root" class="unified-shell" data-main-focus="chat" data-perspective="project"><main class="unified-main"><section class="unified-stage"><main id="unified-chat" class="unified-chat"><article class="unified-turn-assistant"><div class="unified-markdown">${longReply}</div></article></main></section><div class="unified-composer-dock" id="unified-composer-dock"><footer class="unified-composer" id="unified-composer"><span class="unified-composer-companion" aria-hidden="true"><img src="${image}" alt="" /></span><div id="unified-mode-toggle" class="unified-mode-toggle"><button class="unified-mode-seg">只聊</button><button class="unified-mode-seg">动手</button></div><button id="unified-stop" hidden>停止</button><textarea id="unified-input" class="unified-input" rows="1"></textarea><button id="unified-send">↑</button></footer></div></main></div></body></html>`);
    const longChatHeight = await page.locator("#unified-chat").evaluate((chat) => chat.scrollHeight);
    assert.ok(longChatHeight > 2000, `expected a long scrollable transcript, got ${longChatHeight}px`);

    for (const width of [1440, 1180]) {
      await page.setViewportSize({ width, height: 900 });
      const geometry = await page.evaluate(() => {
        const rect = (selector: string) => document.querySelector(selector)!.getBoundingClientRect();
        const avatar = rect(".unified-composer-companion");
        const content = rect("#unified-chat > .unified-turn-assistant");
        const input = rect("#unified-input");
        const dock = rect("#unified-composer-dock");
        return {
          visible: getComputedStyle(document.querySelector(".unified-composer-companion")!).display !== "none",
          avatarLeft: avatar.left, avatarRight: avatar.right, avatarBottom: avatar.bottom,
          contentRight: content.right, inputTop: input.top, dockRight: dock.right,
          avatarWidth: avatar.width, longReplyHeight: content.height,
        };
      });
      assert.ok(geometry.visible, `${width}px should have sufficient right gutter`);
      assert.ok(geometry.longReplyHeight > 2000, "geometry must include the long reply");
      assert.ok(geometry.avatarLeft >= geometry.contentRight + 8, `${width}px avatar overlaps long chat: ${JSON.stringify(geometry)}`);
      assert.ok(geometry.avatarRight <= geometry.dockRight - 8, `${width}px avatar exceeds available right gutter: ${JSON.stringify(geometry)}`);
      assert.ok(geometry.avatarBottom <= geometry.inputTop, `${width}px avatar overlaps the input: ${JSON.stringify(geometry)}`);
      assert.ok(geometry.avatarWidth >= 72 && geometry.avatarWidth <= 132);
    }

    for (const width of [1096, 390]) {
      await page.setViewportSize({ width, height: 844 });
      assert.equal(await page.locator(".unified-composer-companion").evaluate((avatar) => getComputedStyle(avatar).display), "none", `${width}px must hide the companion when side space is insufficient`);
    }
  } finally {
    await browser.close();
  }
});
