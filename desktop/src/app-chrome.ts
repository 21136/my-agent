import "./app-chrome.css";
import {
  readTheme,
  writeTheme,
  type ThemeId,
} from "./settings";

export type AppChromeHandlers = {
  onSwitchToCli: () => Promise<void>;
  onOpenSettings?: () => void;
  onOpenModelKeys?: () => void;
};

export type AppChromeApi = {
  showRouteNotice: (text: string, onUndo?: () => void) => void;
};

function bindOverflowMenu(menu: HTMLDetailsElement): void {
  menu.querySelectorAll<HTMLButtonElement>(".app-chrome-menu-item").forEach((btn) => {
    btn.addEventListener("click", () => {
      menu.open = false;
    });
  });
  menu.addEventListener("toggle", () => {
    if (!menu.open) return;
    const close = (ev: MouseEvent) => {
      if (!menu.contains(ev.target as Node)) {
        menu.open = false;
        document.removeEventListener("click", close);
      }
    };
    window.setTimeout(() => document.addEventListener("click", close), 0);
  });
}

export function mountAppChrome(
  root: HTMLElement,
  handlers: AppChromeHandlers,
): AppChromeApi {
  const theme = readTheme();

  const partnerTitle = typeof localStorage !== "undefined" ? (localStorage.getItem("partner_name") || "小魔王") : "小魔王";
  root.innerHTML = `
    <header class="app-chrome">
      <div class="app-chrome-leading">
        <span class="app-chrome-title">✦ ${partnerTitle}的空间</span>
        <div class="app-chrome-controls">
          <label class="app-chrome-field" for="chrome-theme">
            <span class="app-chrome-label">外观</span>
            <select id="chrome-theme" aria-label="外观">
              <option value="light">亮色</option>
              <option value="dark">暗色</option>
            </select>
          </label>
        </div>
      </div>
      <div class="app-chrome-center">
        <div class="app-chrome-route hidden" id="chrome-route-notice"></div>
      </div>
      <div class="app-chrome-trailing">
        <div class="hidden" aria-hidden="true">
          ${handlers.onOpenModelKeys ? '<button type="button" class="app-chrome-menu-item" id="chrome-model-keys" role="menuitem">模型密钥</button>' : ""}
          ${handlers.onOpenSettings ? '<button type="button" class="app-chrome-menu-item" id="chrome-settings" role="menuitem">托管区</button>' : ""}
          <button type="button" class="app-chrome-menu-item" id="chrome-cli" role="menuitem">改用终端 (CLI)</button>
        </div>
      </div>
    </header>
  `;

  const themeSelect = root.querySelector<HTMLSelectElement>("#chrome-theme")!;
  const cliBtn = root.querySelector<HTMLButtonElement>("#chrome-cli")!;
  const routeNotice = root.querySelector<HTMLElement>("#chrome-route-notice")!;
  const overflowMenu = root.querySelector<HTMLDetailsElement>(".app-chrome-menu");

  themeSelect.value = theme;
  if (overflowMenu) bindOverflowMenu(overflowMenu);

  let routeTimer: number | null = null;
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

  const modelKeysBtn = root.querySelector<HTMLButtonElement>("#chrome-model-keys");
  modelKeysBtn?.addEventListener("click", () => {
    handlers.onOpenModelKeys?.();
  });

  return {
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
