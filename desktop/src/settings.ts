export type ShellId = "grow" | "daily" | "govern" | "project";
export type ThemeId = "light" | "dark";

const SHELL_KEY = "active_shell";
const THEME_KEY = "theme";
const SHELL_LOCK_KEY = "shell_route_locked";

export function readActiveShell(): ShellId {
  const stored = localStorage.getItem(SHELL_KEY);
  if (stored === "daily" || stored === "govern" || stored === "grow" || stored === "project") {
    return stored;
  }
  return "grow";
}

export function writeActiveShell(shell: ShellId): void {
  localStorage.setItem(SHELL_KEY, shell);
}

export function readShellRouteLocked(): boolean {
  return localStorage.getItem(SHELL_LOCK_KEY) === "1";
}

export function writeShellRouteLocked(locked: boolean): void {
  localStorage.setItem(SHELL_LOCK_KEY, locked ? "1" : "0");
}

export function readTheme(): ThemeId {
  return localStorage.getItem(THEME_KEY) === "dark" ? "dark" : "light";
}

export function writeTheme(theme: ThemeId): void {
  localStorage.setItem(THEME_KEY, theme);
  applyTheme(theme);
}

export function applyTheme(theme: ThemeId = readTheme()): void {
  document.documentElement.dataset.theme = theme;
}

export const SHELL_LABELS: Record<ShellId, string> = {
  grow: "生长",
  project: "项目",
  daily: "日用",
  govern: "治理",
};
