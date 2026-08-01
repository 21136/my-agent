import "./styles/theme.css";
import type { AgentWsClient } from "./api/ws";
import { mountAppChrome } from "./app-chrome";
import { createWsClient } from "./api/ws";
import { mountHostSettings } from "./host-settings";
import { mountShell } from "./shell-router";
import { mountContextSwitchOverlay } from "./context-switch-overlay";
import { applyTheme } from "./settings";

async function boot(): Promise<void> {
  const app = document.querySelector<HTMLElement>("#app");
  if (!app) {
    throw new Error("#app missing");
  }

  let client: AgentWsClient | null = null;
  let teardownUi: (() => void) | null = null;

  async function mountUi(): Promise<void> {
    applyTheme();
    app!.innerHTML = `
    <div class="app-frame">
      <div id="app-chrome"></div>
      <div id="shell-root"><p class="text-muted" style="padding:1rem">正在连接 sidecar…</p></div>
    </div>
  `;

    const chromeRoot = app!.querySelector<HTMLElement>("#app-chrome")!;
    const shellRoot = app!.querySelector<HTMLElement>("#shell-root")!;

    if (!client) {
      client = await createWsClient();
    }

    shellRoot.innerHTML = "";
    const host = document.createElement("div");
    host.className = "shell-host";
    shellRoot.appendChild(host);

    const hostSettings = mountHostSettings(client);

    mountAppChrome(chromeRoot, {
      client: client!,
      onSwitchToCli: async () => {
        if (!window.myAgentDesktop?.switchToCli) {
          throw new Error("switchToCli unavailable");
        }
        await window.myAgentDesktop.switchToCli();
      },
      onOpenSettings: () => {
        hostSettings.openSettings();
      },
    });

    const teardownContextSwitch = mountContextSwitchOverlay(app!, client, {});

    const cleanup = mountShell(host, "grow", client);

    teardownUi = () => {
      teardownContextSwitch();
      cleanup();
    };
  }

  async function suspendSession(): Promise<void> {
    teardownUi?.();
    teardownUi = null;
    client?.destroy();
    client = null;
    app!.innerHTML = `<p class="text-muted" style="padding:1rem">已切到伴侶…</p>`;
  }

  window.myAgentDesktop?.onSessionControl?.((action) => {
    if (action === "suspend") {
      void suspendSession();
    } else if (action === "resume") {
      void mountUi().catch((err) => {
        const message = err instanceof Error ? err.message : String(err);
        app!.innerHTML = `<p class="text-muted" style="padding:1rem">启动失败：${message}</p>`;
      });
    }
  });

  try {
    await mountUi();
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    app.innerHTML = `<p class="text-muted" style="padding:1rem">启动失败：${message}</p>`;
  }

  window.addEventListener("beforeunload", () => {
    teardownUi?.();
    client?.destroy();
  });
}

void boot();
