#!/usr/bin/env node
/**
 * Build desktop/build/app-icon.ico from canonical PNG sources (regeneratable).
 * Always prefers icon-256.png — never newest-mtime among PNGs (avoids stale public/icon-32).
 */
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import pngToIco from "png-to-ico";

const desktopDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const buildDir = path.join(desktopDir, "build");
const outIco = path.join(buildDir, "app-icon.ico");

const CANONICAL_ORDER = [
  path.join(buildDir, "icon-256.png"),
  path.join(buildDir, "icon-32.png"),
  path.join(desktopDir, "public", "icon-32.png"),
];

export function resolveAppIconPngSources() {
  const present = CANONICAL_ORDER.filter((file) => existsSync(file));
  const primary = present[0] ?? null;
  return { primary, present, outIco };
}

export function sha256File(file) {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

function inputsForIco({ present }) {
  const inputs = [];
  const icon256 = path.join(buildDir, "icon-256.png");
  const icon32 = path.join(buildDir, "icon-32.png");
  if (existsSync(icon256)) inputs.push(icon256);
  if (existsSync(icon32) && !inputs.includes(icon32)) inputs.push(icon32);
  if (inputs.length === 0 && present.length) return [present[0]];
  return inputs;
}

function icoInputsStale(icoPath, inputs) {
  if (!existsSync(icoPath)) return true;
  const icoMtime = statSync(icoPath).mtimeMs;
  for (const file of inputs) {
    if (statSync(file).mtimeMs > icoMtime) return true;
  }
  return false;
}

export async function buildAppIconIco({ force = false } = {}) {
  if (!existsSync(buildDir)) mkdirSync(buildDir, { recursive: true });
  const { primary, present } = resolveAppIconPngSources();
  if (!primary) {
    throw new Error("no PNG icon source under desktop/build or desktop/public");
  }
  const inputs = inputsForIco({ present });
  if (!force && !icoInputsStale(outIco, inputs)) {
    return { outIco, skipped: true, source: primary, inputs, primarySha256: sha256File(primary) };
  }
  const buf = await pngToIco(inputs.length === 1 ? inputs[0] : inputs);
  writeFileSync(outIco, buf);
  return {
    outIco,
    skipped: false,
    source: primary,
    inputs,
    primarySha256: sha256File(primary),
    icoSha256: sha256File(outIco),
  };
}

const isMain = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (isMain) {
  buildAppIconIco({ force: process.argv.includes("--force") })
    .then((result) => {
      const rel = path.relative(desktopDir, result.outIco);
      const inputNames = result.inputs.map((f) => path.basename(f)).join("+");
      console.log(
        result.skipped
          ? `[app-icon] ${rel} up to date (canonical ${path.basename(result.source)}; ico inputs: ${inputNames})`
          : `[app-icon] wrote ${rel} from ${inputNames} (canonical ${path.basename(result.source)})`,
      );
    })
    .catch((err) => {
      console.error("[app-icon]", err instanceof Error ? err.message : err);
      process.exitCode = 1;
    });
}