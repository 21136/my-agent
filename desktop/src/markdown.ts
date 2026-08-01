import { marked } from "marked";
import { markedHighlight } from "marked-highlight";
import hljs from "highlight.js";
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

export function renderMarkdown(text: string): string {
  if (!text.trim()) return "";
  let cleaned = text.trimEnd();
  // strip trailing delivery markers that some models emit
  const trailingMarkers = [
    /【[^】]*交付完成[^】]*】\s*$/,
    /【[^】]*已验收[^】]*】\s*$/,
    /【[^】]*沉淀完成[^】]*】\s*$/,
  ];
  for (const re of trailingMarkers) {
    cleaned = cleaned.replace(re, "").trimEnd();
  }
  return marked.parse(cleaned, { async: false }) as string;
}
