import type { AgentWsClient } from "./api/ws";
import type { FileDropHandle } from "./file-drop";
import type { ChatSession } from "./shells/chat-state";
import { userMessagePreview } from "./user-message";

export type ComposerAttachmentWire = {
  fileDrop: FileDropHandle;
  syncSendEnabled: () => void;
  sendCurrentMessage: () => void;
};

export function wireComposerAttachments(options: {
  input: HTMLTextAreaElement;
  sendBtn: HTMLButtonElement;
  client: AgentWsClient;
  chat: ChatSession;
  fileDrop: FileDropHandle;
  onStatus?: (text: string) => void;
  beforeSend?: () => void;
}): ComposerAttachmentWire {
  const { input, sendBtn, client, chat, fileDrop, onStatus, beforeSend } = options;

  function canSend(): boolean {
    const text = input.value.trim();
    return Boolean(text) || fileDrop.getAttachments().length > 0;
  }

  function syncSendEnabled(): void {
    if (!input.disabled) {
      sendBtn.disabled = !canSend();
    }
  }

  function sendCurrentMessage(): void {
    const text = input.value.trim();
    const attachments = fileDrop.getAttachments();
    const attachmentIds = attachments.map((item) => item.id);
    if ((!text && !attachmentIds.length) || chat.model.confirmPending) return;
    input.value = "";
    beforeSend?.();
    chat.pushUserMessage(
      attachmentIds.length ? composeDisplayMessage(text, attachments) : text,
    );
    fileDrop.clearAttachments();
    try {
      client.sendMessage(text, attachmentIds.length ? attachmentIds : undefined);
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      onStatus?.(`发送失败：${message}`);
    }
    syncSendEnabled();
  }

  input.addEventListener("input", syncSendEnabled);

  return { fileDrop, syncSendEnabled, sendCurrentMessage };
}

function composeDisplayMessage(
  text: string,
  attachments: Array<{ name: string; ref: string; size: number; mime: string; readable_text: boolean }>,
): string {
  if (!attachments.length) {
    return text;
  }
  const lines = ["[附件]"];
  for (const item of attachments) {
    const size =
      item.size < 1024
        ? `${item.size} B`
        : item.size < 1024 * 1024
          ? `${(item.size / 1024).toFixed(1)} KB`
          : `${(item.size / (1024 * 1024)).toFixed(1)} MB`;
    const hint = item.readable_text ? item.mime : `${item.mime}；不可直接 read_file`;
    lines.push(`- ${item.name} → ${item.ref} (${size}, ${hint})`);
  }
  if (text.trim()) {
    return `${lines.join("\n")}\n\n${text.trim()}`;
  }
  return `${lines.join("\n")}\n\n[用户附带了 ${attachments.length} 个文件]`;
}

export { userMessagePreview };
