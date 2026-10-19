import {
  CHAT_FONT_SIZE_DEFAULT, CHAT_FONT_SIZE_MAX, CHAT_FONT_SIZE_MIN,
  readChatFontSize, resetChatFontSize, writeChatFontSize,
} from "../../settings";

export function renderChatFontControl(): string {
  const size = readChatFontSize();
  return `<div class="chat-font-setting" data-chat-font-control>
    <div class="font-line"><div><label for="settings-chat-font-size">聊天字号</label><p class="description" id="settings-chat-font-hint">只调整聊天文字，不改变界面与小魔王的大小。</p></div>
      <div class="font-controls"><div class="font-stepper"><button type="button" data-chat-font-step="-1" aria-label="减小字号"${size === CHAT_FONT_SIZE_MIN ? ' disabled' : ''}>−</button><output for="settings-chat-font-size" data-chat-font-value aria-live="polite">${size}px</output><button type="button" data-chat-font-step="1" aria-label="增大字号"${size === CHAT_FONT_SIZE_MAX ? ' disabled' : ''}>+</button></div><button type="button" class="text-button" data-chat-font-reset title="恢复默认 ${CHAT_FONT_SIZE_DEFAULT}px">恢复默认</button></div>
    </div><div class="font-range-wrap"><span aria-hidden="true">${CHAT_FONT_SIZE_MIN}</span><input type="range" id="settings-chat-font-size" data-chat-font-input min="${CHAT_FONT_SIZE_MIN}" max="${CHAT_FONT_SIZE_MAX}" step="1" value="${size}" style="--range-progress:${(size - CHAT_FONT_SIZE_MIN) / (CHAT_FONT_SIZE_MAX - CHAT_FONT_SIZE_MIN) * 100}%" aria-valuetext="${size} 像素" aria-describedby="settings-chat-font-hint" /><span aria-hidden="true">${CHAT_FONT_SIZE_MAX}</span></div>
  </div>`;
}

/** Update labels in place, so theme/runtime events never interrupt a slider drag. */
export function syncChatFontControl(root: HTMLElement, size: number = readChatFontSize()): void {
  root.querySelectorAll<HTMLInputElement>("[data-chat-font-input]").forEach(input => {
    input.value = String(size);
    input.setAttribute("aria-valuetext", size + " 像素");
    input.style.setProperty('--range-progress', (size - CHAT_FONT_SIZE_MIN) / (CHAT_FONT_SIZE_MAX - CHAT_FONT_SIZE_MIN) * 100 + '%');
  });
  root.querySelectorAll<HTMLElement>("[data-chat-font-value]").forEach(output => { output.textContent = size + "px"; });
  root.querySelectorAll<HTMLButtonElement>("[data-chat-font-step]").forEach(button => {
    button.disabled = Number(button.dataset.chatFontStep) < 0 ? size <= CHAT_FONT_SIZE_MIN : size >= CHAT_FONT_SIZE_MAX;
  });
}
export function bindChatFontControl(root: HTMLElement): () => void {
  function onInput(event: Event): void {
    const input = event.target;
    if (!(input instanceof HTMLInputElement) || !input.matches("[data-chat-font-input]")) return;
    syncChatFontControl(root, writeChatFontSize(input.value));
  }
  function onClick(event: MouseEvent): void {
    if (!(event.target instanceof Element)) return;
    const button = event.target.closest<HTMLButtonElement>('[data-chat-font-reset], [data-chat-font-step]');
    if (!button || button.disabled) return;
    event.preventDefault();
    const size = button.hasAttribute('data-chat-font-reset') ? resetChatFontSize() : writeChatFontSize(Number(root.querySelector<HTMLInputElement>('[data-chat-font-input]')?.value ?? readChatFontSize()) + Number(button.dataset.chatFontStep));
    syncChatFontControl(root, size);
  }
  root.addEventListener("input", onInput);
  root.addEventListener("click", onClick);
  return () => { root.removeEventListener("input", onInput); root.removeEventListener("click", onClick); };
}
