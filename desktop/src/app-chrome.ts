import "./app-chrome.css";
import type { AgentWsClient, LlmModelListItem } from "./api/ws";
import {
  readTheme,
  writeTheme,
  type ThemeId,
} from "./settings";

export type AppChromeHandlers = {
  onSwitchToCli: () => Promise<void>;
  onOpenSettings?: () => void;
  onOpenModelKeys?: () => void;
  client?: AgentWsClient;
};

export type AppChromeApi = {
  showRouteNotice: (text: string, onUndo?: () => void) => void;
  setModel: (model: string) => void;
};

const FALLBACK_MODEL_ID = "tokeness-luna";

function pickModelId(
  model: string | undefined,
  models: LlmModelListItem[],
  preferredDefault?: string,
): string {
  const key = (model || "").trim();
  if (!key) {
    const fallback = preferredDefault?.trim() || FALLBACK_MODEL_ID;
    if (models.some((item) => item.id === fallback)) return fallback;
    return models[0]?.id ?? fallback;
  }
  const exact = models.find((item) => item.id === key);
  if (exact) return exact.id;
  const lowered = key.toLowerCase();
  const fuzzy = models.find(
    (item) =>
      item.id.toLowerCase() === lowered ||
      item.name.toLowerCase() === lowered ||
      item.tier.toLowerCase() === lowered,
  );
  return fuzzy?.id ?? models[0]?.id ?? key;
}

function formatContextTokensShort(tokens: number): string {
  if (tokens >= 1_000_000) {
    const millions = tokens / 1_000_000;
    return Number.isInteger(millions) ? `${millions}M` : `${millions.toFixed(1)}M`;
  }
  if (tokens >= 1_000) {
    const thousands = tokens / 1_000;
    return Number.isInteger(thousands) ? `${thousands}k` : `${thousands.toFixed(1)}k`;
  }
  return String(tokens);
}

/** Closed-state / option label: name only, no ctx/vision dump. */
function formatModelOptionLabel(item: LlmModelListItem): string {
  return item.configured ? item.name : `${item.name} · 未配置`;
}

function formatModelTitle(item: LlmModelListItem): string {
  const parts = [item.name, `${formatContextTokensShort(item.max_input_tokens)} ctx`];
  if (item.supports_image_input) parts.push("视觉");
  const vendor = item.vendor.trim();
  if (vendor && !item.name.toLowerCase().includes(vendor.toLowerCase())) {
    parts.push(vendor);
  }
  if (!item.configured) parts.push("未配置 key");
  return `切换主 Agent 模型（${parts.join(" · ")}）`;
}

function renderModelOptions(models: LlmModelListItem[], booting: boolean): string {
  if (booting) {
    return `<option value="${FALLBACK_MODEL_ID}">加载中…</option>`;
  }
  if (!models.length) {
    return `<option value="${FALLBACK_MODEL_ID}">Flash</option>`;
  }
  return models
    .map((item) => {
      return `<option value="${item.id}">${formatModelOptionLabel(item)}</option>`;
    })
    .join("");
}

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
  let knownModels: LlmModelListItem[] = [];
  let modelsBooting = true;
  let defaultFlashId = FALLBACK_MODEL_ID;
  let sessionModelReceived = false;

  root.innerHTML = `
    <header class="app-chrome">
      <div class="app-chrome-leading">
        <span class="app-chrome-title">my-agent</span>
        <div class="app-chrome-controls">
          <label class="app-chrome-field" for="chrome-theme">
            <span class="app-chrome-label">外观</span>
            <select id="chrome-theme" aria-label="外观">
              <option value="light">亮色</option>
              <option value="dark">暗色</option>
            </select>
          </label>
          <label class="app-chrome-field app-chrome-field-model" for="chrome-model">
            <span class="app-chrome-label">模型</span>
            <select id="chrome-model" aria-label="模型">
              <option value="${FALLBACK_MODEL_ID}">Flash</option>
            </select>
          </label>
        </div>
      </div>
      <div class="app-chrome-center">
        <div class="app-chrome-route hidden" id="chrome-route-notice"></div>
      </div>
      <div class="app-chrome-trailing">
        <button type="button" class="app-chrome-runaway" id="chrome-runaway" role="switch" aria-checked="false" aria-pressed="false" disabled>
          <span class="app-chrome-runaway-track" aria-hidden="true"><span class="app-chrome-runaway-thumb"></span></span>
          <span class="app-chrome-runaway-copy">
            <span class="app-chrome-runaway-name">狂奔</span>
            <span class="app-chrome-runaway-state" id="chrome-runaway-state">关</span>
          </span>
        </button>
        <details class="app-chrome-menu">
          <summary class="app-chrome-menu-trigger" aria-label="更多操作">⋯</summary>
          <div class="app-chrome-menu-panel" role="menu">
            ${handlers.onOpenModelKeys ? '<button type="button" class="app-chrome-menu-item" id="chrome-model-keys" role="menuitem">模型密钥</button>' : ""}
            ${handlers.onOpenSettings ? '<button type="button" class="app-chrome-menu-item" id="chrome-settings" role="menuitem">托管区</button>' : ""}
            <button type="button" class="app-chrome-menu-item" id="chrome-pet" role="menuitem">伴侣窗</button>
            <button type="button" class="app-chrome-menu-item" id="chrome-cli" role="menuitem">改用终端 (CLI)</button>
          </div>
        </details>
      </div>
    </header>
  `;

  const themeSelect = root.querySelector<HTMLSelectElement>("#chrome-theme")!;
  const modelSelect = root.querySelector<HTMLSelectElement>("#chrome-model")!;
  const cliBtn = root.querySelector<HTMLButtonElement>("#chrome-cli")!;
  const petBtn = root.querySelector<HTMLButtonElement>("#chrome-pet")!;
  const routeNotice = root.querySelector<HTMLElement>("#chrome-route-notice")!;
  const runawayBtn = root.querySelector<HTMLButtonElement>("#chrome-runaway")!;
  const runawayState = root.querySelector<HTMLElement>("#chrome-runaway-state")!;
  const overflowMenu = root.querySelector<HTMLDetailsElement>(".app-chrome-menu")!;

  themeSelect.value = theme;
  bindOverflowMenu(overflowMenu);

  let routeTimer: number | null = null;
  let syncingModel = false;
  let runawayEnabled = false;
  let runawayProjectBound = false;

  const syncRunaway = (enabled: boolean, projectBound: boolean): void => {
    runawayEnabled = enabled;
    runawayProjectBound = projectBound;
    runawayBtn.disabled = !projectBound;
    runawayState.textContent = enabled ? "开" : "关";
    runawayBtn.setAttribute("aria-pressed", String(enabled));
    runawayBtn.setAttribute("aria-checked", String(enabled));
    runawayBtn.setAttribute(
      "aria-label",
      projectBound ? `狂奔 ${enabled ? "开" : "关"}` : "先打开一个项目才能开启狂奔运行",
    );
    runawayBtn.title = projectBound
      ? (enabled ? "狂奔运行已开启；点击暂停" : "开启后允许当前项目连续推进")
      : "先打开一个项目才能开启狂奔运行";
    runawayBtn.classList.toggle("is-active", enabled);
  };

  const applyModelCatalog = (models: LlmModelListItem[], selectedId?: string) => {
    knownModels = models;
    modelsBooting = false;
    modelSelect.disabled = false;
    const nextId = pickModelId(
      selectedId ?? modelSelect.value,
      models,
      defaultFlashId,
    );
    modelSelect.innerHTML = renderModelOptions(models, false);
    modelSelect.value = pickModelId(nextId, models, defaultFlashId);
    const selected = models.find((item) => item.id === modelSelect.value);
    modelSelect.title = selected ? formatModelTitle(selected) : "切换主 Agent 模型";
  };

  themeSelect.addEventListener("change", () => {
    const next = themeSelect.value as ThemeId;
    writeTheme(next);
  });

  modelSelect.addEventListener("change", () => {
    if (syncingModel) return;
    const next = pickModelId(modelSelect.value, knownModels, defaultFlashId);
    modelSelect.value = next;
    const selected = knownModels.find((item) => item.id === next);
    if (selected) modelSelect.title = formatModelTitle(selected);
    handlers.client?.setSessionModel(next);
  });

  runawayBtn.addEventListener("click", () => {
    if (!runawayProjectBound) return;
    runawayBtn.disabled = true;
    handlers.client?.setProjectRunaway(!runawayEnabled);
  });

  cliBtn.addEventListener("click", () => {
    cliBtn.disabled = true;
    void handlers.onSwitchToCli().finally(() => {
      cliBtn.disabled = false;
    });
  });

  petBtn.addEventListener("click", () => {
    void window.myAgentDesktop?.openPet?.();
  });

  const settingsBtn = root.querySelector<HTMLButtonElement>("#chrome-settings");
  settingsBtn?.addEventListener("click", () => {
    handlers.onOpenSettings?.();
  });

  const modelKeysBtn = root.querySelector<HTMLButtonElement>("#chrome-model-keys");
  modelKeysBtn?.addEventListener("click", () => {
    handlers.onOpenModelKeys?.();
  });

  const unsubModels = handlers.client?.onEvent((event) => {
    if (event.type !== "session.models") return;
    defaultFlashId = event.default_flash_id?.trim() || defaultFlashId;
    if (!sessionModelReceived) {
      applyModelCatalog(event.models, defaultFlashId);
      return;
    }
    applyModelCatalog(event.models);
  });

  let currentProjectId = "";

  const unsubBanner = handlers.client?.onEvent((event) => {
    if (event.type !== "session.banner") return;
    sessionModelReceived = true;
    currentProjectId = event.project_id || "";
    syncingModel = true;
    if (knownModels.length) {
      modelSelect.value = pickModelId(event.llm_model, knownModels, defaultFlashId);
      const selected = knownModels.find((item) => item.id === modelSelect.value);
      if (selected) modelSelect.title = formatModelTitle(selected);
    } else {
      modelSelect.value = event.llm_model || defaultFlashId || FALLBACK_MODEL_ID;
    }
    syncingModel = false;
  });

  const unsubProject = handlers.client?.onEvent((event) => {
    if (event.type !== "project.state") return;
    const eventProjectId = event.project_id || "";
    if (currentProjectId && eventProjectId !== currentProjectId) return;
    currentProjectId = eventProjectId;
    syncRunaway(Boolean(event.runaway_enabled), Boolean(event.project_id));
  });

  const unsubSwitch = handlers.client?.onEvent((event) => {
    if (event.type === "context.switch.done") {
      if (event.applied === false || event.choice !== "y") return;
      currentProjectId = event.project_id || "";
      syncRunaway(false, Boolean(event.project_id));
      return;
    }
    if (event.type !== "project.switch.done") return;
    currentProjectId = event.project_id;
    syncRunaway(false, Boolean(event.project_id));
  });

  void unsubModels;
  void unsubBanner;
  void unsubProject;
  void unsubSwitch;

  syncRunaway(false, false);

  modelSelect.innerHTML = renderModelOptions([], true);
  modelSelect.disabled = true;
  modelSelect.title = "正在加载模型列表…";

  handlers.client?.listModels();
  window.setTimeout(() => {
    if (!modelsBooting) return;
    handlers.client?.listModels();
  }, 3000);

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
      modelSelect.value = pickModelId(model, knownModels, defaultFlashId);
      const selected = knownModels.find((item) => item.id === modelSelect.value);
      if (selected) modelSelect.title = formatModelTitle(selected);
      syncingModel = false;
    },
  };
}
