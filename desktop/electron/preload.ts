import { contextBridge, ipcRenderer, webUtils } from "electron";
import type { EvolutionHostRequest } from "./evolution/main-bridge";
import { isEvolutionConversationId } from "./evolution/conversation-id";

type SessionControlAction = "suspend" | "resume";
type EvolutionRequestResult = { status: "queued" | "disabled"; message?: string };
function isEvolutionHostRequest(value: unknown): value is EvolutionHostRequest {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const request = value as Record<string, unknown>;
  return Object.keys(request).length === 2
    && Object.hasOwn(request, "type")
    && Object.hasOwn(request, "conversation_id")
    && (request.type === "evolution.begin" || request.type === "evolution.inspect" || request.type === "evolution.stop")
    && isEvolutionConversationId(request.conversation_id);
}

contextBridge.exposeInMainWorld("myAgentDesktop", {
  getEvolutionState: () => ipcRenderer.invoke("evolution:getState", null) as Promise<EvolutionRequestResult>,
  beginEvolution: () => ipcRenderer.invoke("evolution:begin", null) as Promise<EvolutionRequestResult>,
  stopEvolution: () => ipcRenderer.invoke("evolution:stop", null) as Promise<EvolutionRequestResult>,
  setEvolutionConversationId: (sessionId: string | null) => ipcRenderer.invoke("evolution:session", sessionId) as Promise<boolean>,
  onEvolutionRequest: (handler: (request: EvolutionHostRequest) => void) => {
    const listener = (_event: unknown, value: unknown) => {
      if (isEvolutionHostRequest(value)) handler(value);
    };
    ipcRenderer.on("evolution:request", listener);
    return () => ipcRenderer.removeListener("evolution:request", listener);
  },
  getSidecar: () => ipcRenderer.invoke("sidecar:get"),
  getSessionLaneCapabilities: () => ipcRenderer.invoke("sidecar:session-lane-capabilities") as Promise<{ legacySessionLanes: boolean }>,
  getSandboxSidecar: () => ipcRenderer.invoke("sidecar:get-sandbox") as Promise<{ host: string; port: number } | null>,
  ensureSandboxSidecar: () => ipcRenderer.invoke("sidecar:ensure-sandbox") as Promise<{ ok: boolean; host?: string; port?: number; message?: string }>,
  switchSidecarCandidate: (root: string) => ipcRenderer.invoke("sidecar:switch-candidate", root),
  switchToCli: () => ipcRenderer.invoke("app:switch-to-cli"),
  openWorkbench: () => ipcRenderer.invoke("app:open-workbench") as Promise<void>,
  openExternal: (url: string) => ipcRenderer.invoke("app:open-external", url) as Promise<boolean>,
  openBilibiliSearch: (query: string, bounds: { x: number; y: number; width: number; height: number }) => ipcRenderer.invoke("music:open-bilibili-search", query, bounds) as Promise<boolean>,
  setBilibiliViewState: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => ipcRenderer.invoke("music:set-bilibili-view-state", state) as Promise<boolean>,
  getShellDevPreviewUrl: () => ipcRenderer.invoke("shell-dev:get-preview-url") as Promise<string>,
  getShellDevMainUrl: () => ipcRenderer.invoke("shell-dev:get-main-url") as Promise<string>,
  promoteShellDevPreview: () => ipcRenderer.invoke("shell-dev:promote-preview") as Promise<{ ok: boolean; mainUrl?: string; previewUrl?: string; message?: string }>,
  resetShellDevMain: () => ipcRenderer.invoke("shell-dev:reset-main") as Promise<{ ok: boolean; message?: string; mainUrl?: string }>,
  getSandboxAdoptStatus: () => ipcRenderer.invoke("shell-dev:sandbox-adopt-status") as Promise<Record<string, unknown>>,
  syncSandboxWorktree: () => ipcRenderer.invoke("shell-dev:sync-sandbox") as Promise<{ ok: boolean; message: string; detail?: string }>,
  resetShellDevMainLane: () => ipcRenderer.invoke("shell-dev:reset-main-lane") as Promise<{ ok: boolean; message: string; mainUrl?: string }>,
  inspectManagedSourceReview: () => ipcRenderer.invoke("shell-dev:managed-source-review") as Promise<{ ok: true; report: Record<string, any> } | { ok: false; message: string }>,
  proposeManagedSourceSelection: (request: { format: 1; mode: "managed_source_selection_request_v1"; reviewDigest: string; selectedPaths: string[] }) => ipcRenderer.invoke("shell-dev:managed-source-selection", request) as Promise<{ ok: true; proposal: Record<string, unknown> } | { ok: false; message: string }>,
  createManagedSourceCheckpoint: (request: { format: 1; mode: "managed_source_checkpoint_request_v1"; reviewDigest: string; proposalDigest: string; selectedPaths: string[] }) => ipcRenderer.invoke("shell-dev:managed-source-checkpoint", request) as Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; message: string }>,
  executeManagedSelectedMerge: (request: { format: 1; mode: "managed_selected_merge_request_v1"; checkpointDigest: string }) => ipcRenderer.invoke("shell-dev:managed-selected-merge", request) as Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; message: string; maintenanceRequired?: boolean }>,
  mergeSandboxIntoStable: () =>
    ipcRenderer.invoke("shell-dev:merge-sandbox") as Promise<{
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
  getEvolutionState: () => Promise<EvolutionRequestResult>;
  beginEvolution: () => Promise<EvolutionRequestResult>;
  stopEvolution: () => Promise<EvolutionRequestResult>;
  setEvolutionConversationId: (sessionId: string | null) => Promise<boolean>;
  onEvolutionRequest: (handler: (request: EvolutionHostRequest) => void) => () => void;
  getSidecar: () => Promise<{ host: string; port: number } | null>;
  getSessionLaneCapabilities: () => Promise<{ legacySessionLanes: boolean }>;
  getSandboxSidecar: () => Promise<{ host: string; port: number } | null>;
  ensureSandboxSidecar: () => Promise<{ ok: boolean; host?: string; port?: number; message?: string }>;
  switchSidecarCandidate: (root: string) => Promise<Record<string, unknown>>;
  switchToCli: () => Promise<void>;
  openWorkbench: () => Promise<void>;
  openExternal: (url: string) => Promise<boolean>;
  openBilibiliSearch: (query: string, bounds: { x: number; y: number; width: number; height: number }) => Promise<boolean>;
  setBilibiliViewState: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => Promise<boolean>;
  getShellDevPreviewUrl: () => Promise<string>;
  getShellDevMainUrl: () => Promise<string>;
  promoteShellDevPreview: () => Promise<{ ok: boolean; mainUrl?: string; previewUrl?: string; message?: string }>;
  resetShellDevMain: () => Promise<{ ok: boolean; message?: string; mainUrl?: string }>;
  getSandboxAdoptStatus: () => Promise<Record<string, unknown>>;
  syncSandboxWorktree: () => Promise<{ ok: boolean; message: string; detail?: string }>;
  resetShellDevMainLane: () => Promise<{ ok: boolean; message: string; mainUrl?: string }>;
  inspectManagedSourceReview: () => Promise<{ ok: true; report: Record<string, any> } | { ok: false; message: string }>;
  proposeManagedSourceSelection: (request: { format: 1; mode: "managed_source_selection_request_v1"; reviewDigest: string; selectedPaths: string[] }) => Promise<{ ok: true; proposal: Record<string, unknown> } | { ok: false; message: string }>;
  createManagedSourceCheckpoint: (request: { format: 1; mode: "managed_source_checkpoint_request_v1"; reviewDigest: string; proposalDigest: string; selectedPaths: string[] }) => Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; message: string }>;
  executeManagedSelectedMerge: (request: { format: 1; mode: "managed_selected_merge_request_v1"; checkpointDigest: string }) => Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; message: string; maintenanceRequired?: boolean }>;
  mergeSandboxIntoStable: () => Promise<{ ok: boolean; message: string; detail?: string; steps?: string[]; merged?: boolean; synced?: boolean; recoveryStash?: string }>;
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
