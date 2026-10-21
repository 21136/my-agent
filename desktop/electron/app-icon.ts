import { existsSync } from "node:fs";
import { nativeImage, type NativeImage } from "electron";
import { APP_ICON_CANDIDATE_FILES } from "./app-icon-paths";

export { APP_ICON_CANDIDATE_FILES } from "./app-icon-paths";

export function resolveAppIconPath(): string | null {
  for (const file of APP_ICON_CANDIDATE_FILES) {
    if (!existsSync(file)) continue;
    const image = nativeImage.createFromPath(file);
    if (!image.isEmpty()) return file;
  }
  return null;
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
