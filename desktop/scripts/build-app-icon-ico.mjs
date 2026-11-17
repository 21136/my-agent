#!/usr/bin/env node
/**
 * Build desktop/build/app-icon.ico from existing PNG sources (regeneratable).
 */
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import pngToIco from "png-to-ico";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const buildDir = path.join(desktopDir, "build");
const outIco = path.join(buildDir, "app-icon.ico");

const PNG_SOURCES = [
  path.join(buildDir, "icon-256.png"),
  path.join(buildDir, "icon-32.png"),
  path.join(desktopDir, "public", "icon-32.png"),
];

function newestSource() {
  let best = null;
  for (const file of PNG_SOURCES) {
    if (!existsSync(file)) continue;
    const mtime = statSync(file).mtimeMs;
    if (!best || mtime > best.mtime) best = { file, mtime };
  }
  return best?.file ?? null;
}

export async function buildAppIconIco({ force = false } = {}) {
  if (!existsSync(buildDir)) mkdirSync(buildDir, { recursive: true });
  const source = newestSource();
  if (!source) {
    throw new Error("no PNG icon source under desktop/build or desktop/public");
  }
  if (!force && existsSync(outIco)) {
    const icoMtime = statSync(outIco).mtimeMs;
    if (icoMtime >= statSync(source).mtimeMs) {
      return { outIco, skipped: true, source };
    }
  }
  const buf = await pngToIco(source);
  writeFileSync(outIco, buf);
  return { outIco, skipped: false, source };
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isMain) {
  buildAppIconIco({ force: process.argv.includes("--force") })
    .then((result) => {
      const rel = path.relative(desktopDir, result.outIco);
      console.log(
        result.skipped
          ? `[app-icon] ${rel} up to date (from ${path.basename(result.source)})`
          : `[app-icon] wrote ${rel} from ${path.basename(result.source)}`,
      );
    })
    .catch((err) => {
      console.error("[app-icon]", err instanceof Error ? err.message : err);
      process.exitCode = 1;
    });
}