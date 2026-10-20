import { applyChatFontSize, readChatFontSize, writeChatFontSize } from "../src/settings";
import { bindChatFontControl, renderChatFontControl } from "../src/shells/unified/chat-font-control";

/** Real DOM + production styles/control; no backend connection is required. */
export function runChatFontDomTests(): string[] {
  const saved = readChatFontSize();
  const results: string[] = [];
  const root = document.createElement("div");
  root.className = "unified-shell";
  root.dataset.perspective = "project";
  root.style.cssText = "position:fixed;left:-10000px;top:0;width:800px;display:block";
  document.body.append(root);
  const unbind = bindChatFontControl(root);
  function check(ok: unknown, label: string): void { if (!ok) throw Error(label); results.push(label); }
  function render(): void {
    root.innerHTML = renderChatFontControl() + `
      <button class="unified-btn" id="font-ui-sentinel">侧栏按钮</button>
      <span class="unified-pet-name" id="font-pet-sentinel">小魔王</span>
      <article class="unified-turn-assistant unified-message-bubble"><div class="unified-markdown"><p>短回复</p></div></article>
      <article class="unified-turn-assistant unified-message-reading"><div class="unified-markdown"><div class="unified-reading-prose"><p>长正文</p><h2>标题</h2><ul><li>列表</li></ul><blockquote><p>引用</p></blockquote></div></div></article>
      <article class="unified-turn-user unified-message-bubble"><div class="unified-turn-body">用户短消息</div></article>
      <article class="unified-turn-user unified-message-reading"><div class="unified-turn-body">用户长消息</div></article>`;
  }
  const font = (selector: string): string => getComputedStyle(root.querySelector(selector)!).fontSize;
  try {
    writeChatFontSize(14);
    render();
    const input = root.querySelector<HTMLInputElement>("[data-chat-font-input]")!;
    const uiSize = font("#font-ui-sentinel");
    const petSize = font("#font-pet-sentinel");
    const globalSize = getComputedStyle(document.documentElement).fontSize;
    check(input.min === "12" && input.max === "20" && input.value === "14", "滑杆范围与默认值");
    for (const size of [12, 20, 15]) {
      input.focus();
      input.value = String(size);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      for (const selector of [".unified-message-bubble .unified-markdown p", ".unified-reading-prose > p", ".unified-reading-prose li", ".unified-reading-prose blockquote p", ".unified-turn-user.unified-message-bubble .unified-turn-body", ".unified-turn-user.unified-message-reading .unified-turn-body"]) {
        check(font(selector) === size + "px", size + "px · " + selector);
      }
      check(root.querySelector("[data-chat-font-input]") === input && document.activeElement === input, "调整时不重建滑杆或丢失焦点");
      check(root.querySelector("output")?.textContent === size + "px" && readChatFontSize() === size, "数值提示与保存同步");
      check(font("#font-ui-sentinel") === uiSize && font("#font-pet-sentinel") === petSize && getComputedStyle(document.documentElement).fontSize === globalSize, "侧栏、小魔王与全局字号不变");
    }
    render();
    check(root.querySelector<HTMLInputElement>("input")?.value === "15", "设置重渲染读取已保存值");
    root.querySelector<HTMLButtonElement>("[data-chat-font-reset]")!.click();
    check(readChatFontSize() === 14 && root.querySelector<HTMLInputElement>("input")?.value === "14" && font(".unified-reading-prose > p") === "14px", "恢复默认立即应用并保存");
    unbind();
    const afterUnbind = root.querySelector<HTMLInputElement>("input")!;
    afterUnbind.value = "20";
    afterUnbind.dispatchEvent(new Event("input", { bubbles: true }));
    check(readChatFontSize() === 14, "卸载后移除设置监听器");
    return results;
  } finally {
    unbind();
    root.remove();
    writeChatFontSize(saved);
    applyChatFontSize();
  }
}

/** Accepts an already-mounted real shell, so chat rendering cannot silently unbind settings. */
export function runChatFontMountedShellTests(root: HTMLElement): string[] {
  const saved = readChatFontSize();
  const results: string[] = [];
  const settingsButton = root.querySelector<HTMLButtonElement>('.sidebar-icon-btn[data-panel="settings"]')!;
  function check(ok: unknown, label: string): void { if (!ok) throw Error(label); results.push(label); }
  settingsButton.click();
  let input = root.querySelector<HTMLInputElement>("[data-chat-font-input]")!;
  check(!!input, "正式设置入口渲染字号控件");
  try {
    for (const size of [12, 20]) {
      input.value = String(size);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      check(readChatFontSize() === size && root.querySelector("[data-chat-font-value]")?.textContent === size + "px", "正式界面输入委托在聊天渲染后仍然有效");
      check(getComputedStyle(root.querySelector(".unified-reading-prose > p")!).fontSize === size + "px", "正式长回复立即同步字号");
    }
    root.querySelector<HTMLButtonElement>('.sidebar-icon-btn[data-panel="now"]')!.click();
    settingsButton.click();
    input = root.querySelector<HTMLInputElement>("[data-chat-font-input]")!;
    check(input.value === "20", "关闭再打开设置保留字号");
    root.querySelector<HTMLButtonElement>("[data-chat-font-reset]")!.click();
    check(input.value === "14" && readChatFontSize() === 14, "正式设置恢复默认有效");
    return results;
  } finally {
    input.value = String(saved);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
}
