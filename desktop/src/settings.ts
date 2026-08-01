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
  // also sync unified shell perspective
  const shell = document.querySelector<HTMLElement>(".unified-shell");
  if (shell) {
    shell.setAttribute("data-perspective", theme === "dark" ? "night" : "default");
  }
}

/** @deprecated — kept for old localStorage migration only */
export function readActiveShell(): string {
  return localStorage.getItem("active_shell") ?? "grow";
}
