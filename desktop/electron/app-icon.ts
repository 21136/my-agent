import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { nativeImage, type NativeImage } from "electron";
import { APP_ICON_RUNTIME_CANDIDATE_FILES } from "./app-icon-paths";

export {
  APP_ICON_CANONICAL_PNG,
  APP_ICON_ICO_PATH,
  APP_ICON_RUNTIME_CANDIDATE_FILES,
} from "./app-icon-paths";

/** @deprecated use APP_ICON_RUNTIME_CANDIDATE_FILES */
export { APP_ICON_RUNTIME_CANDIDATE_FILES as APP_ICON_CANDIDATE_FILES } from "./app-icon-paths";

export type AppIconResolution = {
  path: string | null;
  basename: string | null;
  sha256: string | null;
  candidateIndex: number;
};

export function sha256AppIconFile(file: string): string {
  return createHash("sha256").update(readFileSync(file)).digest("hex");
}

export function resolveAppIconPath(
  candidates: readonly string[] = APP_ICON_RUNTIME_CANDIDATE_FILES,
): string | null {
  for (const file of candidates) {
    if (!existsSync(file)) continue;
    const image = nativeImage.createFromPath(file);
    if (!image.isEmpty()) return file;
  }
  return null;
}

export function describeAppIconResolution(
  candidates: readonly string[] = APP_ICON_RUNTIME_CANDIDATE_FILES,
): AppIconResolution {
  const chosen = resolveAppIconPath(candidates);
  if (!chosen) {
    return { path: null, basename: null, sha256: null, candidateIndex: -1 };
  }
  const candidateIndex = candidates.findIndex((file) => path.resolve(file) === path.resolve(chosen));
  return {
    path: chosen,
    basename: path.basename(chosen),
    sha256: sha256AppIconFile(chosen),
    candidateIndex,
  };
}

export function loadAppIcon(maxSizePx?: number): NativeImage {
  const file = resolveAppIconPath();
  if (!file) return nativeImage.createEmpty();
  let image = nativeImage.createFromPath(file);
  if (image.isEmpty()) return nativeImage.createEmpty();
  if (maxSizePx != null && maxSizePx > 0) {
    const { width, height } = image.getSize();
    const longest = Math.max(width, height);
    if (longest > maxSizePx) {
      const scale = maxSizePx / longest;
      image = image.resize({
        width: Math.max(1, Math.round(width * scale)),
        height: Math.max(1, Math.round(height * scale)),
      });
    }
  }
  return image;
}