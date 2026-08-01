import "./app-chrome.css";
import type { AgentWsClient } from "./api/ws";
import {
  readTheme,
  writeTheme,
  type ThemeId,
} from "./settings";

export type SessionModelId = "deepseek-v4-flash" | "deepseek-v4-pro";

export type AppChromeHandlers = {
  onSwitchToCli: () => Promise<void>;
  onOpenSettings?: () => void;
  client?: AgentWsClient;
};

export type AppChromeApi = {
  showRouteNotice: (text: string, onUndo?: () => void) => void;
  setModel: (model: string) => void;
};

const MODEL_FLASH: SessionModelId = "deepseek-v4-flash";
const MODEL_PRO: SessionModelId = "deepseek-v4-pro";

function normalizeModelSelectValue(model: string | undefined): SessionModelId {
  const key = (model || "").trim().toLowerCase();
  if (key.includes("pro")) return MODEL_PRO;
  return MODEL_FLASH;
}

export function mountAppChrome(
  root: HTMLElement,
  handlers: AppChromeHandlers,
): AppChromeApi {
  const theme = readTheme();

  root.innerHTML = `
    <header class="app-chrome">
      <span class="app-chrome-title">my-agent</span>
      <div class="app-chrome-group">
        <span class="app-chrome-label">外观</span>
        <select id="chrome-theme" aria-label="外观">
          <option value="light">亮色</option>
          <option value="dark">暗色</option>
        </select>
      </div>
      <div class="app-chrome-group">
        <span class="app-chrome-label">模型</span>
        <select id="chrome-model" aria-label="模型">
          <option value="${MODEL_FLASH}">Flash</option>
          <option value="${MODEL_PRO}">Pro</option>
        </select>
      </div>
      <span class="app-chrome-spacer"></span>
      <div class="app-chrome-route hidden" id="chrome-route-notice"></div>
      ${handlers.onOpenSettings ? '<button type="button" class="app-chrome-btn" id="chrome-settings">托管区</button>' : ""}
      <button type="button" class="app-chrome-btn" id="chrome-cli">改用终端 (CLI)</button>
    </header>
  `;

  const themeSelect = root.querySelector<HTMLSelectElement>("#chrome-theme")!;
  const modelSelect = root.querySelector<HTMLSelectElement>("#chrome-model")!;
  const cliBtn = root.querySelector<HTMLButtonElement>("#chrome-cli")!;
  const routeNotice = root.querySelector<HTMLElement>("#chrome-route-notice")!;

  themeSelect.value = theme;

  let routeTimer: number | null = null;
  let syncingModel = false;

  themeSelect.addEventListener("change", () => {
    const next = themeSelect.value as ThemeId;
    writeTheme(next);
  });

  modelSelect.addEventListener("change", () => {
    if (syncingModel) return;
    const next = normalizeModelSelectValue(modelSelect.value);
    modelSelect.value = next;
    handlers.client?.setSessionModel(next);
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

  const unsubBanner = handlers.client?.onEvent((event) => {
    if (event.type !== "session.banner") return;
    syncingModel = true;
    modelSelect.value = normalizeModelSelectValue(event.llm_model);
    syncingModel = false;
  });

  void unsubBanner;

  modelSelect.title = "切换主 Agent 模型（Flash 128k / Pro 1M）";

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
    setModel(model: string): void {
      syncingModel = true;
      modelSelect.value = normalizeModelSelectValue(model);
      syncingModel = false;
    },
  };
}
