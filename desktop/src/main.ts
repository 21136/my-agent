import "./styles/theme.css";
import type { AgentWsClient, ServerEvent } from "./api/ws";
import { mountAppChrome, type AppChromeApi } from "./app-chrome";
import { createWsClient } from "./api/ws";
import { mountHostSettings } from "./host-settings";
import { isAgentBusy, setActiveShell } from "./agent-busy";
import { mountShell } from "./shell-router";
import { mountContextSwitchOverlay } from "./context-switch-overlay";
import {
  applyTheme,
  readActiveShell,
  readShellRouteLocked,
  SHELL_LABELS,
  type ShellId,
} from "./settings";

async function boot(): Promise<void> {
  (window as Window & { __myAgentIsBusy?: () => boolean }).__myAgentIsBusy = isAgentBusy;

  const app = document.querySelector<HTMLElement>("#app");
  if (!app) {
    throw new Error("#app missing");
  }

  let client: AgentWsClient | null = null;
  let offRoute: (() => void) | null = null;
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

    const shellHosts = new Map<ShellId, HTMLElement>();
    const shellCleanups = new Map<ShellId, () => void>();
    let activeShell: ShellId = readActiveShell();
    let chromeApi: AppChromeApi | null = null;
    let undoShell: ShellId | null = null;

    function ensureShell(shell: ShellId): void {
      if (shellHosts.has(shell)) return;

      const host = document.createElement("div");
      host.className = "shell-host";
      host.dataset.shell = shell;
      host.hidden = true;
      shellRoot.appendChild(host);

      const cleanup = mountShell(host, shell, client!);
      shellHosts.set(shell, host);
      shellCleanups.set(shell, cleanup);
    }

    function showShell(shell: ShellId): void {
      ensureShell(shell);
      for (const [id, host] of shellHosts) {
        host.hidden = id !== shell;
      }
      activeShell = shell;
      client!.setActiveShell(shell);
      if (shell === "grow" || shell === "daily" || shell === "project") {
        setActiveShell(shell);
        client!.shellSwitch(shell);
      }
    }

    function applyAutoRoute(event: Extract<ServerEvent, { type: "ui.route" }>): void {
      if (!event.auto || readShellRouteLocked()) return;
      if (!chromeApi) return;

      const target = event.shell;
      if (!(target in SHELL_LABELS) || target === activeShell) return;

      undoShell = activeShell;
      const topicNote =
        event.topics_added && event.topics_added.length
          ? ` · 已加主题 ${event.topics_added.join(", ")}`
          : "";
      const notice = `已切到 ${SHELL_LABELS[target]}（${event.reason}${topicNote}）`;

      chromeApi.setShell(target);
      showShell(target);
      chromeApi.showRouteNotice(notice, () => {
        if (undoShell) {
          chromeApi?.setShell(undoShell);
          showShell(undoShell);
        }
      });
    }

    offRoute = client.onEvent((event: ServerEvent) => {
      if (event.type === "ui.route") {
        applyAutoRoute(event);
      }
    });

    const hostSettings = mountHostSettings(client);

    chromeApi = mountAppChrome(chromeRoot, {
      onShellChange: (shell, meta) => {
        showShell(shell);
        if (meta.manual) {
          undoShell = null;
        }
      },
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

    const teardownContextSwitch = mountContextSwitchOverlay(app!, client, {
      onShellApplied: (shell) => {
        chromeApi?.setShell(shell);
        showShell(shell);
      },
    });

    showShell(activeShell);
    chromeApi.setShell(activeShell);

    teardownUi = () => {
      teardownContextSwitch();
      offRoute?.();
      offRoute = null;
      for (const cleanup of shellCleanups.values()) {
        cleanup();
      }
      shellHosts.clear();
      shellCleanups.clear();
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
