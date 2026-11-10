import { createRequire } from "node:module";
import path from "node:path";
import { buildIsolatedShellStyles } from "./isolated-shell-styles.mjs";
import { assertOfflinePetAvatarsPresent, createPublicAssetRouter } from "./isolated-shell-pet-assets.mjs";

export async function buildIsolatedUnifiedBundle(desktop) {
  const esbuild = createRequire(path.join(desktop, "package.json"))("esbuild");
  return esbuild.build({
    stdin: {
      contents: 'export { mountUnifiedShell } from "./src/shells/unified/index.ts"; export { AgentWsClient } from "./src/api/ws.ts";',
      resolveDir: desktop,
      loader: "ts",
    },
    bundle: true,
    write: false,
    platform: "browser",
    format: "iife",
    globalName: "UnifiedUnderTest",
    define: { "import.meta.url": '"http://127.0.0.1/isolated.js"' },
    loader: { ".png": "dataurl", ".jpg": "dataurl", ".svg": "dataurl", ".woff2": "dataurl" },
    plugins: [{ name: "no-css", setup(b) { b.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    logLevel: "silent",
  });
}

export async function mountIsolatedUnifiedPage(context, { desktop, port }) {
  assertOfflinePetAvatarsPresent(desktop);
  const { injectCss, htmlShell } = buildIsolatedShellStyles(desktop);
  const bundle = await buildIsolatedUnifiedBundle(desktop);
  const page = await context.newPage();
  page.setDefaultTimeout(20_000);
  const pageUrl = `http://127.0.0.1:${port}/`;
  const servePublic = createPublicAssetRouter(desktop);
  await context.route("**/*", async (route) => {
    const url = route.request().url();
    if (url === pageUrl) {
      await route.fulfill({
        status: 200,
        contentType: "text/html",
        body: htmlShell.replace("</head>", `<style>${injectCss}</style></head>`),
      });
      return;
    }
    if (url.startsWith(`http://127.0.0.1:${port}/`)) {
      await servePublic(route);
      return;
    }
    await route.abort();
  });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(pageUrl);
  await page.addScriptTag({ content: bundle.outputFiles[0].text });
  await page.evaluate(async (wsPort) => {
    window.myAgentDesktop = { setBilibiliViewState: async () => {}, setShellDevViewState: async () => {} };
    window.client = new UnifiedUnderTest.AgentWsClient("127.0.0.1", wsPort);
    window.receivedEvents = [];
    window.client.onEvent((event) => window.receivedEvents.push(event));
    window.dispose = UnifiedUnderTest.mountUnifiedShell(document.querySelector("#root"), window.client);
    await window.client.connect();
  }, port);
  await page.waitForSelector(".unified-shell", { timeout: 15_000 });
  return { page, pageErrors: errors, bundleText: bundle.outputFiles[0].text };
}
