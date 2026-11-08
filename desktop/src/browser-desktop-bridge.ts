export type BrowserDesktopBridgeConfig = {
  host: string;
  port: number;
  storage?: Pick<Storage, "getItem" | "setItem" | "removeItem">;
};

export type BrowserBridgePolicyInput = {
  devBuild: boolean;
  href: string;
};

const BRIDGE_QUERY_FLAG = "myAgentBrowserBridge";
const CONSTELLATION_STORAGE_KEY = "my-agent-desktop:constellation:v1";
const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "::1", "[::1]"]);

export function isBrowserBridgeAllowed(input: BrowserBridgePolicyInput): boolean {
  if (!input.devBuild) return false;
  try {
    const url = new URL(input.href);
    return url.protocol === "http:"
      && LOOPBACK_HOSTS.has(url.hostname.toLowerCase())
      && url.searchParams.get(BRIDGE_QUERY_FLAG) === "1";
  } catch {
    return false;
  }
}

function isLoopbackHost(host: string): boolean {
  return LOOPBACK_HOSTS.has(host.toLowerCase());
}

function parseConstellation(raw: string | null): { version: 1; stars: unknown[]; links: unknown[] } {
  if (!raw) return { version: 1, stars: [], links: [] };
  try {
    const value: unknown = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value)) return { version: 1, stars: [], links: [] };
    const row = value as Record<string, unknown>;
    return {
      version: 1,
      stars: Array.isArray(row.stars) ? row.stars : [],
      links: Array.isArray(row.links) ? row.links : [],
    };
  } catch {
    return { version: 1, stars: [], links: [] };
  }
}

/**
 * Deliberately small renderer bridge for browser-based development. It never
 * proxies Electron IPC, reads credentials, or enables sandbox/candidate controls.
 */
export function createBrowserDesktopApi(config: BrowserDesktopBridgeConfig) {
  if (!isLoopbackHost(config.host) || !Number.isInteger(config.port) || config.port < 1 || config.port > 65535) {
    throw new Error("Browser bridge requires a valid loopback sidecar endpoint");
  }
  const storage = config.storage ?? (typeof localStorage === "undefined" ? undefined : localStorage);
  return {
    getSidecar: async () => ({ host: config.host, port: config.port }),
    getSessionLaneCapabilities: async () => ({ legacySessionLanes: false }),
    getApprovalPreferences: async () => ({ type: "approval_preferences.state" as const, status: "rejected" as const, state_known: false, approval_mode: "manual" as const, saved_approval_mode: "manual" as const, available_modes: ["manual", "auto", "full"] as ("manual" | "auto" | "full")[], message: "浏览器预览不提供审批设置授权；请在可信 Electron 工作台中操作。" }),
    setApprovalMode: async () => ({ type: "approval_preferences.done" as const, status: "rejected" as const, approval_mode: "manual" as const, saved_approval_mode: "manual" as const, available_modes: ["manual", "auto", "full"] as ("manual" | "auto" | "full")[], message: "浏览器预览不提供审批设置授权；请在可信 Electron 工作台中操作。" }),
    setEvolutionConversationId: async () => false,
    onSessionControl: () => () => undefined,
    switchToCli: async () => { throw new Error("This action is only available in Electron"); },
    openExternal: async () => false,
    pickDirectory: async () => null,
    getDownloadsPath: async () => "",
    getDesktopPath: async () => "",
    readAgentImageDataUrl: async () => null,
    readAgentTextPreview: async () => null,
    getPathForFile: () => "",
    readConstellation: async () => parseConstellation(storage?.getItem(CONSTELLATION_STORAGE_KEY) ?? null),
    writeConstellation: async (payload: { version: 1; stars: unknown[]; links: unknown[] }) => {
      if (!storage) return false;
      try {
        storage.setItem(CONSTELLATION_STORAGE_KEY, JSON.stringify({ version: 1, stars: payload.stars, links: payload.links }));
        return true;
      } catch {
        return false;
      }
    },
    clearConstellation: async () => {
      if (!storage) return false;
      try {
        storage.removeItem(CONSTELLATION_STORAGE_KEY);
        return true;
      } catch {
        return false;
      }
    },
  };
}


export function installBrowserDesktopBridge(): boolean {
  if (typeof window === "undefined") return false;
  if (!isBrowserBridgeAllowed({
    devBuild: import.meta.env.DEV,
    href: window.location.href,
  })) return false;
  if (window.myAgentDesktop) return false;
  try {
    window.myAgentDesktop = createBrowserDesktopApi({
      host: "127.0.0.1",
      port: Number(import.meta.env.VITE_MY_AGENT_BROWSER_WS_PORT ?? "8765"),
    }) as NonNullable<typeof window.myAgentDesktop>;
    document.documentElement.dataset.myAgentBrowserBridge = "enabled";
    return true;
  } catch {
    return false;
  }
}
