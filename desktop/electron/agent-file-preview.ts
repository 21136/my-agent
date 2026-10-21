import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const MAX_BYTES = 12 * 1024 * 1024;
const IMAGE_EXT = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif"]);

function mimeForExt(ext: string): string | null {
  switch (ext) {
    case ".png":
      return "image/png";
    case ".jpg":
    case ".jpeg":
      return "image/jpeg";
    case ".webp":
      return "image/webp";
    case ".gif":
      return "image/gif";
    default:
      return null;
  }
}

/** Read an agent-relative image path into a data URL for in-chat preview. */
export function readAgentImageDataUrl(agentRoot: string, relativePath: string): string | null {
  const raw = (relativePath || "").trim().replace(/\\/g, "/");
  if (!raw || raw.includes("..") || /^[a-z]+:/i.test(raw)) return null;
  const root = path.resolve(agentRoot);
  const abs = path.resolve(root, raw);
  if (!abs.startsWith(root + path.sep) && abs !== root) return null;
  const ext = path.extname(abs).toLowerCase();
  if (!IMAGE_EXT.has(ext)) return null;
  if (!existsSync(abs)) return null;
  const buf = readFileSync(abs);
  if (buf.length <= 0 || buf.length > MAX_BYTES) return null;
  const mime = mimeForExt(ext);
  if (!mime) return null;
  return `data:${mime};base64,${buf.toString("base64")}`;
}
