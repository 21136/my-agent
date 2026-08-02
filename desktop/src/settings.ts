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
