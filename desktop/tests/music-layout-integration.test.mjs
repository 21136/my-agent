/**
 * Isolated Playwright layout verification for the real music-panel markup and CSS.
 * Synthetic browser rendering only: this does not open Bilibili or verify playback.
 */
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { buildIsolatedShellStyles } from './isolated-shell-styles.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const desktop = path.resolve(here, '..');
const require = createRequire(path.join(desktop, 'package.json'));
const edgeCandidates = [
  process.env.CODEX_EDGE_PATH,
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
].filter(Boolean);
const edge = edgeCandidates.find(candidate => existsSync(candidate));
const playwrightPath = process.env.CODEX_PLAYWRIGHT_PATH
  || path.join(process.env.HOME || process.env.USERPROFILE || '', '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
let playwright = null;
try {
  playwright = require(playwrightPath);
} catch {
  try { playwright = require('playwright'); } catch { /* reported as a skip */ }
}

const browserAvailable = Boolean(playwright && edge);
const skipReason = !playwright
  ? 'Playwright unavailable (Browser plugin unavailable; isolated Playwright fallback required)'
  : !edge ? 'Microsoft Edge executable unavailable' : false;

async function createPanelPage(browser, width, height) {
  const context = await browser.newContext({ viewport: { width, height }, serviceWorkers: 'block' });
  const page = await context.newPage();
  const errors = [];
  const externalRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!['about:blank', 'data:'].some(prefix => request.url().startsWith(prefix))) externalRequests.push(request.url());
  });
  const { injectCss } = buildIsolatedShellStyles(desktop);
  const bundle = await require('esbuild').build({
    entryPoints: [path.join(desktop, 'src/shells/unified/music-panel.ts')],
    bundle: true,
    write: false,
    platform: 'browser',
    format: 'iife',
    globalName: 'MusicPanelUnderTest',
    logLevel: 'silent',
  });
  await page.setContent(`<!doctype html><html data-theme="light"><head><meta charset="utf-8"><style>${injectCss}</style></head>
    <body class="unified-shell is-tool-fullpage" data-perspective="project" data-main-focus="music" data-rail="music">
      <aside class="unified-sidebar" hidden></aside>
      <main class="unified-main"><section class="unified-stage">
        <section class="unified-tool-region"><section class="unified-music" id="unified-music"></section></section>
      </section></main>
    </body></html>`);
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.evaluate(() => {
    document.querySelector('#unified-music').innerHTML = MusicPanelUnderTest.renderMusicPanel();
  });
  return { context, page, errors, externalRequests };
}

test('music panel renders with real app CSS across desktop and narrow viewports without horizontal overflow', {
  skip: browserAvailable ? false : skipReason,
}, async () => {
  const { chromium } = playwright;
  const browser = await chromium.launch({ headless: true, executablePath: edge, args: ['--disable-background-networking', '--no-first-run'] });
  try {
    for (const viewport of [{ width: 1920, height: 1080 }, { width: 640, height: 720 }]) {
      const { context, page, errors, externalRequests } = await createPanelPage(browser, viewport.width, viewport.height);
      try {
        const layout = await page.evaluate(() => {
          const panel = document.querySelector('.music-page');
          const empty = document.querySelector('[data-music-empty-state]');
          const form = document.querySelector('[data-music-search-form]');
          const rect = element => {
            const { x, y, width, height, right, bottom } = element.getBoundingClientRect();
            return { x, y, width, height, right, bottom };
          };
          return {
            viewport: { width: innerWidth, height: innerHeight },
            documentWidth: document.documentElement.scrollWidth,
            panel: rect(panel), empty: rect(empty), form: rect(form),
            emptyDisplay: getComputedStyle(empty).display,
            slotHidden: document.querySelector('[data-music-web-slot]').hidden,
          };
        });
        assert.equal(layout.viewport.width, viewport.width);
        assert.ok(layout.panel.width >= viewport.width * 0.95, `music surface should use the available width: ${JSON.stringify(layout)}`);
        assert.ok(layout.panel.height >= viewport.height * 0.9, `music surface should use the available height: ${JSON.stringify(layout)}`);
        assert.ok(layout.documentWidth <= viewport.width, `no horizontal document overflow: ${JSON.stringify(layout)}`);
        assert.ok(layout.panel.x >= 0 && layout.panel.right <= viewport.width, `panel stays within the viewport: ${JSON.stringify(layout)}`);
        assert.ok(layout.form.width > 0 && layout.form.right <= viewport.width, `search form stays within the viewport: ${JSON.stringify(layout)}`);
        assert.notEqual(layout.emptyDisplay, 'none', 'initial empty state is visible');
        assert.ok(layout.empty.height >= (viewport.width <= 720 ? 220 : 230), `empty state has a useful display area: ${JSON.stringify(layout)}`);
        assert.equal(layout.slotHidden, true, 'player slot starts hidden');
        assert.deepEqual(errors, [], 'the rendered panel has no browser runtime errors');
        assert.deepEqual(externalRequests, [], 'the isolated render makes no external requests');
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
});

test('synthetic post-search state reserves a large in-bounds player slot and hides the empty state', {
  skip: browserAvailable ? false : skipReason,
}, async () => {
  const { chromium } = playwright;
  const browser = await chromium.launch({ headless: true, executablePath: edge, args: ['--disable-background-networking', '--no-first-run'] });
  try {
    for (const viewport of [{ width: 1920, height: 1080 }, { width: 640, height: 720 }]) {
      const { context, page, errors, externalRequests } = await createPanelPage(browser, viewport.width, viewport.height);
      try {
        await page.evaluate(() => {
          // Mimic only the state transition after a search; no Bilibili page is contacted or embedded.
          document.querySelector('[data-music-web-slot]').hidden = false;
        });
        const layout = await page.evaluate(() => {
          const slot = document.querySelector('[data-music-web-slot]');
          const empty = document.querySelector('[data-music-empty-state]');
          const { x, y, width, height, right, bottom } = slot.getBoundingClientRect();
          return {
            viewport: { width: innerWidth, height: innerHeight },
            documentWidth: document.documentElement.scrollWidth,
            slot: { x, y, width, height, right, bottom },
            slotDisplay: getComputedStyle(slot).display,
            emptyDisplay: getComputedStyle(empty).display,
          };
        });
        const expectedMinHeight = viewport.width <= 720
          ? Math.min(260, viewport.height * 0.4)
          : Math.min(480, Math.max(300, viewport.height * 0.45));
        assert.notEqual(layout.slotDisplay, 'none', `synthetic player slot is rendered: ${JSON.stringify(layout)}`);
        assert.ok(layout.slot.width >= viewport.width * 0.9, `slot fills the panel width: ${JSON.stringify(layout)}`);
        assert.ok(layout.slot.height >= expectedMinHeight - 2, `slot has its responsive minimum area: ${JSON.stringify(layout)}`);
        assert.ok(layout.slot.x >= 0 && layout.slot.right <= viewport.width, `slot stays within viewport: ${JSON.stringify(layout)}`);
        assert.ok(layout.documentWidth <= viewport.width, `search state adds no horizontal overflow: ${JSON.stringify(layout)}`);
        assert.equal(layout.emptyDisplay, 'none', 'empty state yields to the player slot');
        assert.deepEqual(errors, [], 'synthetic state rendering has no browser runtime errors');
        assert.deepEqual(externalRequests, [], 'no real Bilibili search or playback request was made');
      } finally {
        await context.close();
      }
    }
  } finally {
    await browser.close();
  }
});
