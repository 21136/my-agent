/**
 * Full unified-shell stylesheet bundle for isolated Playwright mounts.
 * Interaction/layout tests may use this; screenshots still are NOT Win32 Electron parity.
 */
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

export const VISUAL_DISCLAIMER =
  "Isolation mount: interaction/layout contract only — not production Electron chrome or asset CDN.";

const SHELL_CSS_FILES = [
  "unified.css",
  "neon-twin.css",
  "sidebar-v2.css",
  "single-chat-tools.css",
  "composer-companion.css",
  "current-work-overview.css",
  "message-reading.css",
  "work-card.css",
];

export function buildIsolatedShellStyles(desktopRoot) {
  const unifiedDir = path.join(desktopRoot, "src/shells/unified");
  const themeCss = readFileSync(path.join(desktopRoot, "src/styles/theme.css"), "utf8");
  const backgroundPath = path.join(unifiedDir, "assets/chat-background-candidate.png");
  const backgroundDataUrl = existsSync(backgroundPath)
    ? `url("data:image/png;base64,${readFileSync(backgroundPath).toString("base64")}")`
    : "none";
  const companionPath = path.join(unifiedDir, "assets/composer-companion.png");
  const companionDataUrl = existsSync(companionPath)
    ? `data:image/png;base64,${readFileSync(companionPath).toString("base64")}`
    : "";
  let shellCss = SHELL_CSS_FILES
    .map(file => readFileSync(path.join(unifiedDir, file), "utf8"))
    .join("\n");
  shellCss = shellCss
    .replaceAll('url("./assets/chat-background-candidate.png")', backgroundDataUrl)
    .replaceAll('url("./assets/chat-background-dark.png")', backgroundDataUrl);
  if (companionDataUrl) {
    shellCss = shellCss.replaceAll("./assets/composer-companion.png", companionDataUrl);
  }
  const baseLayout = `html,body,#root{height:100%;margin:0}body{font-family:system-ui,"Segoe UI",sans-serif}`;
  return {
    htmlShell: `<!doctype html><html data-theme="light"><head><meta charset="utf-8"></head><body><div id="root"></div></body></html>`,
    injectCss: `${themeCss}\n${shellCss}\n${baseLayout}`,
    visualDisclaimer: VISUAL_DISCLAIMER,
  };
}

/** Heuristic: full bundle loaded (not bare unified.css-only stub). Does not prove glass on Win32. */
export async function assessIsolatedStyleFidelity(page) {
  return page.evaluate(() => {
    const shell = document.querySelector(".unified-shell");
    const stage = document.querySelector(".unified-stage");
    const composer = document.querySelector(".unified-composer-dock");
    const shellFont = shell ? getComputedStyle(shell).fontFamily : "";
    const stageDisplay = stage ? getComputedStyle(stage).display : "";
    const composerRadius = composer ? getComputedStyle(composer).borderRadius : "";
    return {
      shellFont,
      hasFlexStage: stageDisplay === "flex",
      composerRounded: composerRadius !== "" && composerRadius !== "0px",
      brokenImages: [...document.querySelectorAll("img")].filter(img => !img.complete || img.naturalWidth === 0).length,
    };
  });
}

export async function assertInteractionStylesLoaded(page) {
  const fidelity = await assessIsolatedStyleFidelity(page);
  const { ok, detail } = (() => {
    if (!fidelity.hasFlexStage) return { ok: false, detail: "missing flex stage layout" };
    if (!/system-ui|Segoe UI|sans-serif/i.test(fidelity.shellFont)) {
      return { ok: false, detail: `unexpected shell font: ${fidelity.shellFont}` };
    }
    if (fidelity.brokenImages > 0) return { ok: false, detail: `${fidelity.brokenImages} broken img` };
    return { ok: true, detail: "interaction stylesheet bundle present" };
  })();
  return { ok, detail, fidelity, visualDisclaimer: VISUAL_DISCLAIMER };
}
