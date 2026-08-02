import type { AgentWsClient } from "./api/ws";
import { createWsClient } from "./api/ws";
import { mountPetShell } from "./shells/pet";

let client: AgentWsClient | null = null;
let cleanupShell: (() => void) | null = null;

async function connectAndMount(): Promise<void> {
  const root = document.querySelector<HTMLElement>("#pet-app");
  if (!root) {
    throw new Error("#pet-app missing");
  }

  if (!client) {
    client = await createWsClient();
  }

  if (cleanupShell) {
    cleanupShell();
  }
  cleanupShell = mountPetShell(root, client);
}

async function suspendSession(): Promise<void> {
  cleanupShell?.();
  cleanupShell = null;
  client?.destroy();
  client = null;
  const root = document.querySelector<HTMLElement>("#pet-app");
  if (root) {
    root.innerHTML = `<p style="padding:1rem;font-size:12px;color:#666">已切到工作台…</p>`;
  }
}

async function boot(): Promise<void> {
  const root = document.querySelector<HTMLElement>("#pet-app");
  if (!root) {
    throw new Error("#pet-app missing");
  }

  document.addEventListener(
    "click",
    (event) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      const anchor = target.closest("a[href]");
      if (!(anchor instanceof HTMLAnchorElement)) return;
      const href = anchor.getAttribute("href") || "";
      if (!/^https?:\/\//i.test(href)) return;
      event.preventDefault();
      event.stopPropagation();
      void window.myAgentDesktop?.openExternal?.(href);
    },
    true,
  );

  try {
    await connectAndMount();
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    root.innerHTML = `<p style="padding:1rem;font-size:12px;color:#c44">连接失败：${message}</p>`;
  }

  window.myAgentDesktop?.onSessionControl?.((action) => {
    if (action === "suspend") {
      void suspendSession();
    } else if (action === "resume") {
      void connectAndMount().catch((err) => {
        const message = err instanceof Error ? err.message : String(err);
        root.innerHTML = `<p style="padding:1rem;font-size:12px;color:#c44">重连失败：${message}</p>`;
      });
    }
  });
}

void boot();
