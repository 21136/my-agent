import "./app-chrome.css";
import {
  readActiveShell,
  readShellRouteLocked,
  readTheme,
  SHELL_LABELS,
  writeActiveShell,
  writeShellRouteLocked,
  writeTheme,
  type ShellId,
  type ThemeId,
} from "./settings";

export type AppChromeHandlers = {
  onShellChange: (shell: ShellId, meta: { manual: boolean }) => void;
  onSwitchToCli: () => Promise<void>;
  onOpenSettings?: () => void;
};

export type AppChromeApi = {
  setShell: (shell: ShellId) => void;
  showRouteNotice: (text: string, onUndo?: () => void) => void;
};

export function mountAppChrome(
  root: HTMLElement,
  handlers: AppChromeHandlers,
): AppChromeApi {
  const shell = readActiveShell();
  const theme = readTheme();
  const locked = readShellRouteLocked();

  root.innerHTML = `
    <header class="app-chrome">
      <span class="app-chrome-title">my-agent</span>
      <div class="app-chrome-group">
        <span class="app-chrome-label">外壳</span>
        <select id="chrome-shell" aria-label="外壳">
          <option value="grow">生长</option>
          <option value="project">项目</option>
          <option value="daily">日用</option>
          <option value="govern">治理</option>
        </select>
        <label class="app-chrome-lock" title="勾选后不再根据任务自动切换外壳">
          <input type="checkbox" id="chrome-shell-lock" />
          锁定
        </label>
      </div>
      <div class="app-chrome-group">
        <span class="app-chrome-label">外观</span>
        <select id="chrome-theme" aria-label="外观">
          <option value="light">亮色</option>
          <option value="dark">暗色</option>
        </select>
      </div>
      <span class="app-chrome-spacer"></span>
      <div class="app-chrome-route hidden" id="chrome-route-notice"></div>
      ${handlers.onOpenSettings ? '<button type="button" class="app-chrome-btn" id="chrome-settings">托管区</button>' : ""}
      <button type="button" class="app-chrome-btn" id="chrome-cli">改用终端 (CLI)</button>
    </header>
  `;

  const shellSelect = root.querySelector<HTMLSelectElement>("#chrome-shell")!;
  const shellLock = root.querySelector<HTMLInputElement>("#chrome-shell-lock")!;
  const themeSelect = root.querySelector<HTMLSelectElement>("#chrome-theme")!;
  const cliBtn = root.querySelector<HTMLButtonElement>("#chrome-cli")!;
  const routeNotice = root.querySelector<HTMLElement>("#chrome-route-notice")!;

  shellSelect.value = shell;
  themeSelect.value = theme;
  shellLock.checked = locked;

  let routeTimer: number | null = null;
  let programmaticShell = false;

  function applyShell(shellId: ShellId, manual: boolean): void {
    if (!(shellId in SHELL_LABELS)) return;
    programmaticShell = !manual;
    shellSelect.value = shellId;
    writeActiveShell(shellId);
    handlers.onShellChange(shellId, { manual });
    programmaticShell = false;
  }

  shellSelect.addEventListener("change", () => {
    const next = shellSelect.value as ShellId;
    if (!(next in SHELL_LABELS)) return;
    if (!programmaticShell) {
      writeShellRouteLocked(true);
      shellLock.checked = true;
    }
    applyShell(next, !programmaticShell);
  });

  shellLock.addEventListener("change", () => {
    writeShellRouteLocked(shellLock.checked);
  });

  themeSelect.addEventListener("change", () => {
    const next = themeSelect.value as ThemeId;
    writeTheme(next);
  });

  cliBtn.addEventListener("click", () => {
    cliBtn.disabled = true;
    void handlers.onSwitchToCli().finally(() => {
      cliBtn.disabled = false;
    });
  });

  const settingsBtn = root.querySelector<HTMLButtonElement>("#chrome-settings");
  settingsBtn?.addEventListener("click", () => {
    handlers.onOpenSettings?.();
  });

  return {
    setShell(shellId: ShellId): void {
      if (shellSelect.value === shellId) return;
      applyShell(shellId, false);
    },
    showRouteNotice(text: string, onUndo?: () => void): void {
      if (routeTimer !== null) {
        window.clearTimeout(routeTimer);
        routeTimer = null;
      }
      routeNotice.classList.remove("hidden");
      routeNotice.innerHTML = "";
      const label = document.createElement("span");
      label.textContent = text;
      routeNotice.append(label);
      if (onUndo) {
        const undoBtn = document.createElement("button");
        undoBtn.type = "button";
        undoBtn.className = "app-chrome-route-undo";
        undoBtn.textContent = "撤销";
        undoBtn.addEventListener("click", () => {
          onUndo();
          routeNotice.classList.add("hidden");
          routeNotice.innerHTML = "";
        });
        routeNotice.append(undoBtn);
      }
      routeTimer = window.setTimeout(() => {
        routeNotice.classList.add("hidden");
        routeNotice.innerHTML = "";
        routeTimer = null;
      }, 8000);
    },
  };
}
