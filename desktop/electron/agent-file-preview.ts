import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const MAX_IMAGE_BYTES = 12 * 1024 * 1024;
const MAX_TEXT_BYTES = 256 * 1024;
const MAX_TEXT_CHARS = 80_000;
const LOCAL_PREFIX = "local:";
const IMAGE_EXT = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"]);
const VIDEO_EXT = new Set([".mp4", ".webm", ".mov"]);
const TEXT_EXT = new Set([
  ".txt", ".md", ".markdown", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".jsx",
  ".json", ".py", ".toml", ".css", ".html", ".htm", ".yml", ".yaml", ".xml",
  ".sh", ".bash", ".bat", ".ps1", ".rs", ".go", ".java", ".kt", ".sql",
  ".csv", ".log", ".ini", ".cfg", ".vue", ".svelte",
]);
const TEXT_LANGUAGE: Record<string, string> = {
  ".md": "markdown",
  ".markdown": "markdown",
  ".ts": "typescript",
  ".tsx": "typescript",
  ".js": "javascript",
  ".mjs": "javascript",
  ".cjs": "javascript",
  ".jsx": "javascript",
  ".json": "json",
  ".py": "python",
  ".toml": "toml",
  ".css": "css",
  ".html": "xml",
  ".htm": "xml",
  ".yml": "yaml",
  ".yaml": "yaml",
  ".xml": "xml",
  ".sh": "bash",
  ".bash": "bash",
  ".bat": "dos",
  ".ps1": "powershell",
  ".rs": "rust",
  ".go": "go",
  ".java": "java",
  ".kt": "kotlin",
  ".sql": "sql",
  ".vue": "xml",
  ".svelte": "xml",
  ".txt": "plaintext",
  ".csv": "plaintext",
  ".log": "plaintext",
  ".ini": "ini",
  ".cfg": "ini",
};

export type AgentTextPreview = {
  text: string;
  truncated: boolean;
  language: string;
  filename: string;
};
const SECRET_DIRS = new Set([".ssh", ".gnupg"]);
const SECRET_FILES = new Set([".env", "id_rsa", "id_rsa.pub"]);
const SYSTEM_PREFIXES = ["C:\\Windows", "C:\\Program Files", "C:\\Program Files (x86)"];

function mimeForImageExt(ext: string): string | null {
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
    case ".bmp":
      return "image/bmp";
    default:
      return null;
  }
}

export function isDisplayPathDenied(absPath: string): boolean {
  const resolved = path.resolve(absPath);
  const posix = resolved.replace(/\\/g, "/");
  for (const prefix of SYSTEM_PREFIXES) {
    const norm = prefix.replace(/\\/g, "/");
    if (posix === norm || posix.toLowerCase().startsWith(norm.toLowerCase() + "/")) {
      return true;
    }
  }
  for (const part of resolved.split(path.sep)) {
    const name = part.toLowerCase();
    if (SECRET_DIRS.has(name)) return true;
    if (SECRET_FILES.has(name) || name.startsWith(".env.")) return true;
    if (name.includes("credentials")) return true;
  }
  return false;
}

/** Resolve an agent-relative or ``local:`` display ref to an absolute file path. */
export function resolveDisplayFile(agentRoot: string, displayRef: string): string | null {
  const raw = (displayRef || "").trim().replace(/\\/g, "/");
  if (!raw) return null;
  let abs: string;
  if (raw.startsWith(LOCAL_PREFIX)) {
    const rest = raw.slice(LOCAL_PREFIX.length).trim();
    if (!rest || rest.includes("..")) return null;
    abs = path.resolve(rest);
  } else {
    if (raw.includes("..") || /^[a-z]+:/i.test(raw)) return null;
    const root = path.resolve(agentRoot);
    abs = path.resolve(root, raw);
    if (!abs.startsWith(root + path.sep) && abs !== root) return null;
  }
  if (isDisplayPathDenied(abs)) return null;
  if (!existsSync(abs)) return null;
  return abs;
}

export function isDisplayImagePath(filePath: string): boolean {
  return IMAGE_EXT.has(path.extname(filePath).toLowerCase());
}

export function isDisplayVideoPath(filePath: string): boolean {
  return VIDEO_EXT.has(path.extname(filePath).toLowerCase());
}

export function isDisplayTextPath(filePath: string): boolean {
  return TEXT_EXT.has(path.extname(filePath).toLowerCase());
}

export function readAgentTextPreview(agentRoot: string, displayRef: string): AgentTextPreview | null {
  const abs = resolveDisplayFile(agentRoot, displayRef);
  if (!abs || !isDisplayTextPath(abs)) return null;
  const buf = readFileSync(abs);
  if (buf.length <= 0 || buf.length > MAX_TEXT_BYTES) return null;
  if (buf.includes(0)) return null;
  let text = buf.toString("utf8");
  const truncated = text.length > MAX_TEXT_CHARS;
  if (truncated) text = text.slice(0, MAX_TEXT_CHARS);
  const ext = path.extname(abs).toLowerCase();
  return {
    text,
    truncated,
    language: TEXT_LANGUAGE[ext] || "plaintext",
    filename: path.basename(abs),
  };
}

/** Read an image display ref into a data URL for in-chat preview. */
export function readAgentImageDataUrl(agentRoot: string, displayRef: string): string | null {
  const abs = resolveDisplayFile(agentRoot, displayRef);
  if (!abs) return null;
  const ext = path.extname(abs).toLowerCase();
  const mime = mimeForImageExt(ext);
  if (!mime) return null;
  const buf = readFileSync(abs);
  if (buf.length <= 0 || buf.length > MAX_IMAGE_BYTES) return null;
  return `data:${mime};base64,${buf.toString("base64")}`;
}

export function agentMediaSrc(displayRef: string): string {
  return `agent-media://media/?p=${encodeURIComponent(displayRef.trim())}`;
}

export function displayRefFromAgentMediaUrl(url: string): string | null {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "agent-media:") return null;
    const value = parsed.searchParams.get("p");
    return value && value.trim() ? value.trim() : null;
  } catch {
    return null;
  }
}
