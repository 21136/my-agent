import hljs from "highlight.js";
import { renderMarkdown } from "./markdown";

/** Load agent-relative image paths into <img> via Electron IPC (shared cache). */

const agentImageDataUrlCache = new Map<string, string>();
const agentTextPreviewCache = new Map<string, { text: string; truncated: boolean; language: string }>();

export function peekAgentImageDataUrl(agentPath: string): string | undefined {
  return agentImageDataUrlCache.get(agentPath.trim());
}

export async function loadAgentImageDataUrl(agentPath: string): Promise<string | null> {
  const path = agentPath.trim();
  if (!path) return null;
  const cached = agentImageDataUrlCache.get(path);
  if (cached) return cached;
  const api = window.myAgentDesktop?.readAgentImageDataUrl;
  if (!api) return null;
  try {
    const dataUrl = await api(path);
    if (!dataUrl) return null;
    agentImageDataUrlCache.set(path, dataUrl);
    return dataUrl;
  } catch {
    return null;
  }
}

export function agentMediaSrc(displayRef: string): string {
  return `agent-media://media/?p=${encodeURIComponent(displayRef.trim())}`;
}

export async function hydrateAgentVideoElements(
  root: ParentNode,
  selector = "video.unified-generated-video[data-agent-path]",
): Promise<void> {
  const videos = root.querySelectorAll<HTMLVideoElement>(selector);
  for (const video of videos) {
    const agentPath = video.dataset.agentPath?.trim();
    if (!agentPath) continue;
    const src = agentMediaSrc(agentPath);
    if (video.getAttribute("src") === src) continue;
    video.src = src;
  }
}

function escapeFileText(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function highlightFileText(text: string, language: string): string {
  if (language && language !== "plaintext" && hljs.getLanguage(language)) {
    return hljs.highlight(text, { language, ignoreIllegals: true }).value;
  }
  return escapeFileText(text);
}

export async function hydrateAgentTextElements(
  root: ParentNode,
  selector = ".unified-inline-file-body[data-agent-path]",
): Promise<void> {
  const api = window.myAgentDesktop?.readAgentTextPreview;
  const nodes = root.querySelectorAll<HTMLElement>(selector);
  await Promise.all(
    [...nodes].map(async (node) => {
      const agentPath = node.dataset.agentPath?.trim();
      if (!agentPath || node.dataset.hydrated === "1") return;
      if (!api) {
        node.textContent = "请完全退出并重启桌面壳后再试文件预览";
        return;
      }
      try {
        const cached = agentTextPreviewCache.get(agentPath);
        const preview = cached ?? await api(agentPath);
        if (!preview) {
          node.textContent = "文件预览失败（路径不存在、过大或不是文本）";
          return;
        }
        agentTextPreviewCache.set(agentPath, preview);
        const kind = node.dataset.fileKind === "markdown" ? "markdown" : "code";
        if (kind === "markdown") {
          node.innerHTML = renderMarkdown(preview.text);
        } else {
          node.innerHTML = `<pre><code class="hljs">${highlightFileText(preview.text, preview.language)}</code></pre>`;
        }
        if (preview.truncated) {
          const note = document.createElement("p");
          note.className = "unified-inline-file-truncated text-muted";
          note.textContent = "已截断过长正文";
          node.append(note);
        }
        node.dataset.hydrated = "1";
      } catch {
        node.textContent = "文件预览失败（读取异常）";
      }
    }),
  );
}

export async function hydrateAgentImageElements(
  root: ParentNode,
  selector = "img[data-agent-path]",
): Promise<void> {
  const api = window.myAgentDesktop?.readAgentImageDataUrl;
  const imgs = root.querySelectorAll<HTMLImageElement>(selector);
  await Promise.all(
    [...imgs].map(async (img) => {
      const agentPath = img.dataset.agentPath?.trim();
      if (!agentPath) return;

      const cached = agentImageDataUrlCache.get(agentPath);
      if (cached) {
        if (img.src !== cached) img.src = cached;
        img.dataset.hydrated = "1";
        return;
      }
      if (img.dataset.hydrated === "1" && img.src.startsWith("data:")) return;

      if (!api) {
        img.alt = img.alt || "请完全退出并重启桌面壳后再试图片预览";
        return;
      }
      try {
        const dataUrl = await api(agentPath);
        if (!dataUrl) {
          img.alt = img.alt || "图片预览失败（路径或文件不存在）";
          return;
        }
        agentImageDataUrlCache.set(agentPath, dataUrl);
        img.src = dataUrl;
        img.dataset.hydrated = "1";
      } catch {
        img.alt = img.alt || "图片预览失败（读取异常）";
      }
    }),
  );
}
