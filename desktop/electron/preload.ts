import { contextBridge, ipcRenderer, webUtils } from "electron";

type SessionControlAction = "suspend" | "resume";

contextBridge.exposeInMainWorld("myAgentDesktop", {
  getSidecar: () => ipcRenderer.invoke("sidecar:get"),
  switchSidecarCandidate: (root: string) => ipcRenderer.invoke("sidecar:switch-candidate", root),
  switchToCli: () => ipcRenderer.invoke("app:switch-to-cli"),
  openWorkbench: () => ipcRenderer.invoke("app:open-workbench") as Promise<void>,
  openExternal: (url: string) => ipcRenderer.invoke("app:open-external", url) as Promise<boolean>,
  openBilibiliSearch: (query: string, bounds: { x: number; y: number; width: number; height: number }) => ipcRenderer.invoke("music:open-bilibili-search", query, bounds) as Promise<boolean>,
  setBilibiliViewState: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => ipcRenderer.invoke("music:set-bilibili-view-state", state) as Promise<boolean>,
  getShellDevPreviewUrl: () => ipcRenderer.invoke("shell-dev:get-preview-url") as Promise<string>,
  getShellDevMainUrl: () => ipcRenderer.invoke("shell-dev:get-main-url") as Promise<string>,
  promoteShellDevPreview: () => ipcRenderer.invoke("shell-dev:promote-preview") as Promise<{ ok: boolean; mainUrl?: string; previewUrl?: string; message?: string }>,
  getSandboxAdoptStatus: () => ipcRenderer.invoke("shell-dev:sandbox-adopt-status") as Promise<Record<string, unknown>>,
  mergeSandboxIntoStable: (options?: {
    autoCommitSandbox?: boolean;
    sandboxCommitMessage?: string;
    autoStashStable?: boolean;
  }) =>
    ipcRenderer.invoke("shell-dev:merge-sandbox", options ?? {}) as Promise<{
      ok: boolean;
      message: string;
      detail?: string;
      steps?: string[];
    }>,
  openShellDevPreview: (bounds: { x: number; y: number; width: number; height: number }) =>
    ipcRenderer.invoke("shell-dev:open-preview", bounds) as Promise<{
      ok: boolean;
      url?: string;
      configuredUrl?: string;
      fallbackToMain?: boolean;
      message?: string;
    }>,
  openShellDevPopout: () =>
    ipcRenderer.invoke("shell-dev:open-popout") as Promise<{
      ok: boolean;
      url?: string;
      configuredUrl?: string;
      fallbackToMain?: boolean;
      message?: string;
    }>,
  reloadShellDevPreview: () => ipcRenderer.invoke("shell-dev:reload-preview") as Promise<boolean>,
  setShellDevViewState: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => ipcRenderer.invoke("shell-dev:set-view-state", state) as Promise<boolean>,
  onSessionControl: (handler: (action: SessionControlAction) => void) => {
    const listener = (_event: unknown, action: SessionControlAction) => handler(action);
    ipcRenderer.on("session:control", listener);
    return () => ipcRenderer.removeListener("session:control", listener);
  },
  pickDirectory: () => ipcRenderer.invoke("dialog:pick-directory") as Promise<string | null>,
  getDownloadsPath: () => ipcRenderer.invoke("app:get-downloads-path") as Promise<string>,
  getDesktopPath: () => ipcRenderer.invoke("app:get-desktop-path") as Promise<string>,
  getPathForFile: (file: File) => {
    try {
      return webUtils.getPathForFile(file);
    } catch {
      return "";
    }
  },
  readConstellation: () => ipcRenderer.invoke("constellation:read"),
  writeConstellation: (payload: { version: 1; stars: unknown[]; links: unknown[] }) =>
    ipcRenderer.invoke("constellation:write", payload),
  clearConstellation: () => ipcRenderer.invoke("constellation:clear"),
  writeTempStagingFile: (name: string, data: Uint8Array) =>
    ipcRenderer.invoke("staging:write-temp", name, data) as Promise<string>,
  readClipboardImageToTemp: () =>
    ipcRenderer.invoke("clipboard:read-image-temp") as Promise<string | null>,
  readAgentImageDataUrl: (agentRelativePath: string) =>
    ipcRenderer.invoke("agent:read-image-data-url", agentRelativePath) as Promise<string | null>,
});

export type MyAgentDesktopApi = {
  getSidecar: () => Promise<{ host: string; port: number } | null>;
  switchSidecarCandidate: (root: string) => Promise<Record<string, unknown>>;
  switchToCli: () => Promise<void>;
  openWorkbench: () => Promise<void>;
  openExternal: (url: string) => Promise<boolean>;
  openBilibiliSearch: (query: string, bounds: { x: number; y: number; width: number; height: number }) => Promise<boolean>;
  setBilibiliViewState: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => Promise<boolean>;
  getShellDevPreviewUrl: () => Promise<string>;
  getShellDevMainUrl: () => Promise<string>;
  promoteShellDevPreview: () => Promise<{ ok: boolean; mainUrl?: string; previewUrl?: string; message?: string }>;
  getSandboxAdoptStatus: () => Promise<Record<string, unknown>>;
  mergeSandboxIntoStable: (options?: {
    autoCommitSandbox?: boolean;
    sandboxCommitMessage?: string;
    autoStashStable?: boolean;
  }) => Promise<{ ok: boolean; message: string; detail?: string; steps?: string[] }>;
  openShellDevPreview: (bounds: { x: number; y: number; width: number; height: number }) => Promise<{
    ok: boolean;
    url?: string;
    configuredUrl?: string;
    fallbackToMain?: boolean;
    message?: string;
  }>;
  reloadShellDevPreview: () => Promise<boolean>;
  setShellDevViewState: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => Promise<boolean>;
  onSessionControl: (handler: (action: SessionControlAction) => void) => () => void;
  pickDirectory: () => Promise<string | null>;
  getDownloadsPath: () => Promise<string>;
  getDesktopPath: () => Promise<string>;
  getPathForFile: (file: File) => string;
  writeTempStagingFile: (name: string, data: Uint8Array) => Promise<string>;
  readClipboardImageToTemp: () => Promise<string | null>;
  readAgentImageDataUrl: (agentRelativePath: string) => Promise<string | null>;
  readConstellation: () => Promise<{ version: 1; stars: unknown[]; links: unknown[] }>;
  writeConstellation: (payload: { version: 1; stars: unknown[]; links: unknown[] }) => Promise<boolean>;
  clearConstellation: () => Promise<boolean>;
};
