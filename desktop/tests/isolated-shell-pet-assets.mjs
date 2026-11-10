import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const MIME = {
  ".webp": "image/webp",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".woff2": "font/woff2",
};

/** Offline fulfill for desktop/public/* when isolated mount loads ./pet-assets/... from page origin. */
export function createPublicAssetRouter(desktopRoot) {
  const publicRoot = path.join(desktopRoot, "public");
  return async (route) => {
    const url = new URL(route.request().url());
    const rel = decodeURIComponent(url.pathname.replace(/^\//, ""));
    if (!rel || rel.includes("..")) {
      await route.abort();
      return;
    }
    const filePath = path.join(publicRoot, rel);
    if (!existsSync(filePath)) {
      await route.abort();
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    await route.fulfill({
      status: 200,
      contentType: MIME[ext] ?? "application/octet-stream",
      body: readFileSync(filePath),
    });
  };
}

export function listRequiredPetAvatarFiles(desktopRoot) {
  const dir = path.join(desktopRoot, "public/pet-assets/avatar");
  return ["pet-idle.webp", "pet-idle-still.webp"].map((name) => path.join(dir, name));
}

export function assertOfflinePetAvatarsPresent(desktopRoot) {
  for (const file of listRequiredPetAvatarFiles(desktopRoot)) {
    if (!existsSync(file)) throw new Error(`missing offline harness avatar: ${file}`);
  }
}
