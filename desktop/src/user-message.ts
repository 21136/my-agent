import { escapeHtml } from "./shells/chat-state";

export type ParsedAttachment = {
  name: string;
  ref: string;
  meta: string;
};

export type ParsedUserMessage = {
  attachments: ParsedAttachment[];
  body: string;
};

const ATTACHMENT_LINE_RE = /^- (.+?) → (.+?) \((.+)\)$/;

export function parseUserMessage(text: string): ParsedUserMessage {
  const trimmed = text.trim();
  if (!trimmed.startsWith("[附件]")) {
    return { attachments: [], body: trimmed };
  }

  const lines = trimmed.split("\n");
  const attachments: ParsedAttachment[] = [];
  let index = 1;
  for (; index < lines.length; index += 1) {
    const line = lines[index]?.trim() ?? "";
    if (!line) {
      index += 1;
      break;
    }
    const match = ATTACHMENT_LINE_RE.exec(line);
    if (!match) {
      break;
    }
    attachments.push({ name: match[1], ref: match[2], meta: match[3] });
  }

  const body = lines.slice(index).join("\n").trim();
  return { attachments, body };
}

export function formatUserMessageHtml(text: string): string {
  const parsed = parseUserMessage(text);
  if (!parsed.attachments.length) {
    return escapeHtml(text);
  }

  const chips = parsed.attachments
    .map(
      (item) => `
        <span class="history-attachment-chip" title="${escapeHtml(item.ref)}">
          <span class="history-attachment-name">${escapeHtml(item.name)}</span>
          <span class="history-attachment-meta">${escapeHtml(item.meta)}</span>
        </span>`,
    )
    .join("");

  const body = parsed.body ? `<div class="history-user-body">${escapeHtml(parsed.body)}</div>` : "";
  return `<div class="history-attachments">${chips}</div>${body}`;
}

export function userMessagePreview(text: string): string {
  const parsed = parseUserMessage(text);
  if (!parsed.attachments.length) {
    return text;
  }
  const names = parsed.attachments.map((item) => item.name).join(", ");
  if (parsed.body) {
    return `${parsed.body} [${names}]`;
  }
  return `[${names}]`;
}
