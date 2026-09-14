import { marked } from "marked";
import { markedHighlight } from "marked-highlight";
import hljs from "highlight.js";
import mermaid from "mermaid";
import "highlight.js/styles/github.css";

marked.use(
  markedHighlight({
    langPrefix: "hljs language-",
    highlight(code: string, lang: string) {
      if (lang && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    },
  }),
);

marked.setOptions({
  gfm: true,
  breaks: true,
});

mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
});

let mermaidRenderSequence = 0;

function encodeMermaidSource(source: string): string {
  return encodeURIComponent(source.trim());
}

function decodeMermaidSource(encoded: string): string {
  try {
    return decodeURIComponent(encoded);
  } catch {
    return "";
  }
}

function replaceMermaidBlocks(text: string): string {
  return text.replace(
    /```mermaid[ \t]*\r?\n([\s\S]*?)\r?\n```/gi,
    (_match, source: string) => {
      const encoded = encodeMermaidSource(source);
      return `<div class="mermaid-placeholder" data-mermaid-source="${encoded}"><span>图表渲染中…</span></div>`;
    },
  );
}

export function sanitizeAssistantMarkdown(text: string): string {
  let cleaned = text.trimEnd();
  const trailingMarkers = [
    /【[^】]*交付完成[^】]*】\s*$/,
    /【[^】]*已验收[^】]*】\s*$/,
    /【[^】]*沉淀完成[^】]*】\s*$/,
  ];
  for (const re of trailingMarkers) {
    cleaned = cleaned.replace(re, "").trimEnd();
  }
  // UX-030 — repair common model markdown glitches before marked.parse
  cleaned = cleaned.replace(/^\[continue\]\s*$/gim, "");
  cleaned = cleaned.replace(/[ \t]+`{2,}\s*$/gm, "");
  cleaned = cleaned.replace(
    /命令[：:]\s*\\?`+\s*(powershell\b[^\n`]*)/gim,
    (_match, cmd: string) => `\n\n\`\`\`powershell\n${cmd.trim()}\n\`\`\`\n`,
  );
  cleaned = cleaned.replace(
    /(^|[\n\r])`{1,2}\s*(powershell\b[^\n`]+)`?/gim,
    (_match, prefix: string, cmd: string) => `${prefix}\n\`\`\`powershell\n${cmd.trim()}\n\`\`\`\n`,
  );
  return cleaned;
}

export function renderMarkdown(text: string): string {
  if (!text.trim()) return "";
  const cleaned = sanitizeAssistantMarkdown(text);
  return marked.parse(replaceMermaidBlocks(cleaned), { async: false }) as string;
}

export async function hydrateMermaid(root: ParentNode): Promise<void> {
  const placeholders = Array.from(
    root.querySelectorAll<HTMLElement>(".mermaid-placeholder:not([data-mermaid-rendered])"),
  );
  await Promise.all(
    placeholders.map(async (placeholder) => {
      const source = decodeMermaidSource(placeholder.dataset.mermaidSource || "");
      if (!source) {
        placeholder.dataset.mermaidRendered = "error";
        placeholder.classList.add("mermaid-error");
        placeholder.textContent = "Mermaid 图表为空";
        return;
      }
      placeholder.dataset.mermaidRendered = "pending";
      try {
        const id = `mermaid-diagram-${mermaidRenderSequence++}`;
        const result = await mermaid.render(id, source);
        placeholder.classList.add("mermaid-diagram");
        placeholder.replaceChildren();
        placeholder.insertAdjacentHTML("afterbegin", result.svg);
        result.bindFunctions?.(placeholder);
        placeholder.dataset.mermaidRendered = "ok";
      } catch (error) {
        placeholder.classList.add("mermaid-error");
        placeholder.replaceChildren();
        const label = document.createElement("div");
        label.className = "mermaid-error-title";
        label.textContent = "Mermaid 图表渲染失败，已保留源代码";
        const detail = document.createElement("div");
        detail.className = "mermaid-error-detail";
        detail.textContent = error instanceof Error ? error.message : String(error);
        const sourceBlock = document.createElement("pre");
        const sourceCode = document.createElement("code");
        sourceCode.textContent = source;
        sourceBlock.appendChild(sourceCode);
        placeholder.append(label, detail, sourceBlock);
        placeholder.dataset.mermaidRendered = "error";
      }
    }),
  );
}
