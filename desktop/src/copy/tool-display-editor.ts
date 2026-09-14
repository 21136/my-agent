import { builtinToolDisplayName } from "./tool-display";
import {
  clearToolDisplayOverride,
  getToolDisplayOverride,
  setToolDisplayOverride,
} from "./tool-display-overrides";

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

let overlay: HTMLElement | null = null;
let inputEl: HTMLInputElement | null = null;
let currentToolId = "";

function ensureOverlay(): HTMLElement {
  if (overlay) return overlay;
  overlay = document.createElement("div");
  overlay.className = "tool-display-editor-overlay hidden";
  overlay.innerHTML = `
    <div class="tool-display-editor-panel" role="dialog" aria-labelledby="tool-display-editor-title">
      <header class="tool-display-editor-header">
        <span class="tool-display-editor-title" id="tool-display-editor-title">工具显示名称</span>
        <button type="button" class="tool-display-editor-btn" data-action="close" aria-label="关闭">×</button>
      </header>
      <div class="tool-display-editor-body">
        <label class="tool-display-editor-field">
          <span class="tool-display-editor-label">工具 id（给模型用，不可改）</span>
          <code class="tool-display-editor-id" id="tool-display-editor-id"></code>
        </label>
        <label class="tool-display-editor-field">
          <span class="tool-display-editor-label">显示名称（仅界面）</span>
          <input type="text" class="tool-display-editor-input" id="tool-display-editor-input" maxlength="40" autocomplete="off" />
        </label>
        <p class="tool-display-editor-hint" id="tool-display-editor-hint"></p>
      </div>
      <footer class="tool-display-editor-footer">
        <button type="button" class="tool-display-editor-btn" data-action="reset">恢复默认</button>
        <button type="button" class="tool-display-editor-btn tool-display-editor-btn-accent" data-action="save">保存</button>
      </footer>
    </div>
  `;
  document.body.appendChild(overlay);

  inputEl = overlay.querySelector<HTMLInputElement>("#tool-display-editor-input");

  overlay.querySelector<HTMLButtonElement>('[data-action="close"]')?.addEventListener("click", closeToolDisplayEditor);
  overlay.addEventListener("click", (ev) => {
    if (ev.target === overlay) closeToolDisplayEditor();
  });
  overlay.querySelector<HTMLButtonElement>('[data-action="save"]')?.addEventListener("click", () => {
    if (!currentToolId || !inputEl) return;
    const value = inputEl.value.trim();
    if (!value) return;
    setToolDisplayOverride(currentToolId, value);
    closeToolDisplayEditor();
  });
  overlay.querySelector<HTMLButtonElement>('[data-action="reset"]')?.addEventListener("click", () => {
    if (!currentToolId) return;
    clearToolDisplayOverride(currentToolId);
    closeToolDisplayEditor();
  });

  inputEl?.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      overlay?.querySelector<HTMLButtonElement>('[data-action="save"]')?.click();
    }
    if (ev.key === "Escape") {
      ev.preventDefault();
      closeToolDisplayEditor();
    }
  });

  return overlay;
}

export function mountToolDisplayEditor(): void {
  ensureOverlay();
}

export function openToolDisplayEditor(toolId: string): void {
  const id = toolId.trim().toLowerCase();
  if (!id) return;
  currentToolId = id;
  const root = ensureOverlay();
  const builtin = builtinToolDisplayName(id);
  const current = getToolDisplayOverride(id) ?? builtin;
  const idEl = root.querySelector<HTMLElement>("#tool-display-editor-id");
  const hintEl = root.querySelector<HTMLElement>("#tool-display-editor-hint");
  if (idEl) idEl.textContent = id;
  if (hintEl) {
    hintEl.innerHTML = `默认：<strong>${escapeHtml(builtin)}</strong> · 修改后活动流与失败提示会更新，模型仍使用 <code>${escapeHtml(id)}</code>`;
  }
  if (inputEl) {
    inputEl.value = current;
    inputEl.select();
  }
  root.classList.remove("hidden");
  window.setTimeout(() => inputEl?.focus(), 0);
}

export function closeToolDisplayEditor(): void {
  overlay?.classList.add("hidden");
  currentToolId = "";
}
