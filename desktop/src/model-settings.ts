import type { AgentWsClient, LlmKeySlot } from "./api/ws";
import "./host-settings.css";

export type ModelSettingsApi = {
  openSettings: () => void;
};

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function sourceLabel(source: LlmKeySlot["source"]): string {
  if (source === "env") return "环境变量（优先）";
  if (source === "file") return "已保存到本机";
  return "未配置";
}

export function mountModelSettings(client: AgentWsClient): ModelSettingsApi {
  const overlay = document.createElement("div");
  overlay.className = "host-settings-overlay hidden";
  overlay.innerHTML = `
    <div class="host-settings-panel" role="dialog" aria-labelledby="model-keys-title">
      <header class="host-settings-header">
        <span class="host-settings-title" id="model-keys-title">模型 API 密钥</span>
        <button type="button" class="host-settings-btn" data-action="close">关闭</button>
      </header>
      <div class="host-settings-body" id="model-keys-body"></div>
      <footer class="host-settings-footer" id="model-keys-footer"></footer>
    </div>
  `;
  document.body.appendChild(overlay);

  const body = overlay.querySelector<HTMLElement>("#model-keys-body")!;
  const footer = overlay.querySelector<HTMLElement>("#model-keys-footer")!;
  let keys: LlmKeySlot[] = [];
  let statusText = "";
  let listRetryCount = 0;
  let listRetryTimer: number | null = null;

  function scheduleListRetry(): void {
    if (overlay.classList.contains("hidden")) return;
    if (keys.length > 0) return;
    if (listRetryCount >= 4) {
      statusText = "后端仍在启动，可点「刷新」或稍后再试";
      render();
      return;
    }
    listRetryCount += 1;
    if (listRetryTimer !== null) {
      window.clearTimeout(listRetryTimer);
    }
    listRetryTimer = window.setTimeout(() => {
      listRetryTimer = null;
      if (!overlay.classList.contains("hidden")) {
        client.listLlmKeys();
      }
    }, listRetryCount <= 1 ? 2000 : 4000);
  }

  function closeSettings(): void {
    overlay.classList.add("hidden");
    statusText = "";
    listRetryCount = 0;
    if (listRetryTimer !== null) {
      window.clearTimeout(listRetryTimer);
      listRetryTimer = null;
    }
  }

  function render(): void {
    body.innerHTML = "";
    footer.innerHTML = "";

    const hint = document.createElement("p");
    hint.className = "host-settings-hint";
    hint.textContent =
      "密钥保存在本机 data/llm_secrets.json（不会提交到 git）。若已设置同名环境变量，环境变量优先。";
    body.append(hint);

    if (!keys.length) {
      const empty = document.createElement("p");
      empty.className = "host-settings-empty";
      empty.textContent = "暂无需要配置的模型密钥。";
      body.append(empty);
    } else {
      const list = document.createElement("ul");
      list.className = "host-settings-list";
      for (const slot of keys) {
        const item = document.createElement("li");
        item.className = "host-settings-item";
        item.innerHTML = `
          <div class="host-settings-item-head">
            <span class="host-settings-id">${escapeHtml(slot.label)}</span>
            <span class="host-settings-badge">${escapeHtml(sourceLabel(slot.source))}</span>
          </div>
          <div class="host-settings-path">${escapeHtml(slot.env)}${
            slot.masked ? ` · ${escapeHtml(slot.masked)}` : ""
          }</div>
          <div class="host-settings-form" style="margin-top:0.5rem">
            <div class="host-settings-field">
              <label for="key-${escapeHtml(slot.env)}">新 API Key</label>
              <input
                id="key-${escapeHtml(slot.env)}"
                type="password"
                autocomplete="off"
                placeholder="粘贴 API Key"
                data-env="${escapeHtml(slot.env)}"
              />
            </div>
          </div>
          <div class="host-settings-actions">
            <button type="button" class="host-settings-btn host-settings-btn-accent" data-save="${escapeHtml(slot.env)}">保存</button>
            ${
              slot.source === "file"
                ? `<button type="button" class="host-settings-btn host-settings-btn-danger" data-clear="${escapeHtml(slot.env)}">清除本机</button>`
                : ""
            }
          </div>
        `;
        list.append(item);
      }
      body.append(list);

      list.querySelectorAll<HTMLButtonElement>("[data-save]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const env = btn.dataset.save ?? "";
          const input = list.querySelector<HTMLInputElement>(`[data-env="${env}"]`);
          const value = input?.value.trim() ?? "";
          if (!value) {
            statusText = "请先输入 API Key";
            render();
            return;
          }
          client.setLlmKey(env, value);
          if (input) input.value = "";
          statusText = `正在保存 ${env}…`;
          render();
        });
      });

      list.querySelectorAll<HTMLButtonElement>("[data-clear]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const env = btn.dataset.clear ?? "";
          client.clearLlmKey(env);
          statusText = `正在清除 ${env}…`;
          render();
        });
      });
    }

    if (statusText) {
      const status = document.createElement("p");
      status.className = "host-settings-hint";
      status.textContent = statusText;
      footer.append(status);
    }

    const actions = document.createElement("div");
    actions.className = "host-settings-actions";
    const refreshBtn = document.createElement("button");
    refreshBtn.type = "button";
    refreshBtn.className = "host-settings-btn";
    refreshBtn.textContent = "刷新";
    refreshBtn.addEventListener("click", () => {
      client.listLlmKeys();
    });
    actions.append(refreshBtn);
    footer.append(actions);
  }

  overlay.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof HTMLElement)) return;
    if (target.dataset.action === "close") {
      closeSettings();
      return;
    }
    if (target === overlay) {
      closeSettings();
    }
  });

  const unsub = client.onEvent((event) => {
    if (event.type === "llm_keys.state") {
      keys = event.keys;
      statusText = keys.length ? "" : "后端仍在启动，正在重试拉取密钥槽…";
      listRetryCount = 0;
      render();
      if (!keys.length) {
        scheduleListRetry();
      }
      return;
    }
    if (event.type === "llm_keys.updated") {
      statusText = "已更新";
      render();
    }
    if (event.type === "error") {
      statusText = event.message;
      render();
      scheduleListRetry();
    }
  });

  void unsub;

  return {
    openSettings(): void {
      overlay.classList.remove("hidden");
      listRetryCount = 0;
      statusText = "正在连接后端…";
      render();
      client.listLlmKeys();
      scheduleListRetry();
    },
  };
}
