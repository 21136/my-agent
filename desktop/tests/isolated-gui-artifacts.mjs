import assert from "node:assert/strict";
import { writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";
import { assertInteractionStylesLoaded, assessIsolatedStyleFidelity } from "./isolated-shell-styles.mjs";

export async function collectImageDiagnostics(page) {
  return page.evaluate(() => [...document.querySelectorAll("img")].map((img) => ({
    src: img.currentSrc || img.src,
    complete: img.complete,
    naturalWidth: img.naturalWidth,
    naturalHeight: img.naturalHeight,
    className: img.className,
    alt: img.alt,
  })));
}

export async function writeGuiHarnessArtifacts(page, artifactDir, label, {
  styleReport = null,
  extra = {},
} = {}) {
  mkdirSync(artifactDir, { recursive: true });
  const prefix = path.join(artifactDir, label);
  const fidelity = await assessIsolatedStyleFidelity(page);
  const images = await collectImageDiagnostics(page);
  const payload = {
    label,
    styleReport,
    fidelity,
    images,
    ...extra,
  };
  writeFileSync(`${prefix}-style-report.json`, `${JSON.stringify(payload, null, 2)}\n`, "utf8");
  await page.screenshot({ path: `${prefix}-failure.png`, fullPage: true }).catch(() => {});
  return payload;
}

export async function verifyInteractionStyles(page, artifactDir, label) {
  const styleReport = await assertInteractionStylesLoaded(page);
  if (!styleReport.ok) {
    await writeGuiHarnessArtifacts(page, artifactDir, label, { styleReport });
    assert.ok(false, `interaction styles: ${styleReport.detail}`);
  }
  return styleReport;
}
