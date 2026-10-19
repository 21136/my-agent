export type ShellId = string;
export type ThemeId = "light" | "dark";

const THEME_KEY = "theme";

export function readTheme(): ThemeId {
  return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
}

export function writeTheme(theme: ThemeId): void {
  localStorage.setItem(THEME_KEY, theme);
  applyTheme(theme);
}

export function applyTheme(theme: ThemeId = readTheme()): void {
  document.documentElement.dataset.theme = theme;
  // Phase 34: do not stomp workbench data-perspective (night is theme, not entry).
  const shell = document.querySelector<HTMLElement>(".unified-shell");
  if (shell) {
    shell.classList.toggle("is-dark-theme", theme === "dark");
  }
}

/** @deprecated — kept for old localStorage migration only */
export function readActiveShell(): string {
  return localStorage.getItem("active_shell") ?? "grow";
}


export const CHAT_FONT_SIZE_MIN = 12;
export const CHAT_FONT_SIZE_MAX = 20;
export const CHAT_FONT_SIZE_DEFAULT = 14;
const CHAT_FONT_SIZE_KEY = "chat_font_size";

export function normalizeChatFontSize(value: unknown): number {
  if (typeof value !== "number" && typeof value !== "string") return CHAT_FONT_SIZE_DEFAULT;
  if (typeof value === "string" && !value.trim()) return CHAT_FONT_SIZE_DEFAULT;
  const size = Number(value);
  if (!Number.isFinite(size)) return CHAT_FONT_SIZE_DEFAULT;
  return Math.min(CHAT_FONT_SIZE_MAX, Math.max(CHAT_FONT_SIZE_MIN, Math.round(size)));
}

export function readChatFontSize(): number {
  try {
    return normalizeChatFontSize(localStorage.getItem(CHAT_FONT_SIZE_KEY));
  } catch {
    return CHAT_FONT_SIZE_DEFAULT;
  }
}

/** Only chat typography consumes this variable; never scale the root/UI font. */
export function applyChatFontSize(size: unknown = readChatFontSize()): number {
  const normalized = normalizeChatFontSize(size);
  if (typeof document !== "undefined") {
    document.documentElement.style.setProperty("--ma-chat-font-size", normalized + "px");
  }
  return normalized;
}

export function writeChatFontSize(size: unknown): number {
  const normalized = applyChatFontSize(size);
  try {
    localStorage.setItem(CHAT_FONT_SIZE_KEY, String(normalized));
  } catch {
    // A restricted storage environment must not block current-session adjustment.
  }
  return normalized;
}

export function resetChatFontSize(): number {
  return writeChatFontSize(CHAT_FONT_SIZE_DEFAULT);
}
