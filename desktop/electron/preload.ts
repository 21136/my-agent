import { contextBridge, ipcRenderer, webUtils } from "electron";

type SessionControlAction = "suspend" | "resume";
type PetBoundsMode = "collapsed" | "expanded";

contextBridge.exposeInMainWorld("myAgentDesktop", {
  getSidecar: () => ipcRenderer.invoke("sidecar:get"),
  switchToCli: () => ipcRenderer.invoke("app:switch-to-cli"),
  openWorkbench: () => ipcRenderer.invoke("app:open-workbench") as Promise<void>,
  openPet: () => ipcRenderer.invoke("app:open-pet") as Promise<void>,
  petSetIgnoreMouseEvents: (ignore: boolean) => ipcRenderer.send("pet:set-ignore-mouse-events", ignore),
  petSetBounds: (mode: PetBoundsMode) => ipcRenderer.invoke("pet:set-bounds", mode),
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
});

export type MyAgentDesktopApi = {
  getSidecar: () => Promise<{ host: string; port: number } | null>;
  switchToCli: () => Promise<void>;
  openWorkbench: () => Promise<void>;
  openPet: () => Promise<void>;
  petSetIgnoreMouseEvents: (ignore: boolean) => void;
  petSetBounds: (mode: PetBoundsMode) => Promise<void>;
  onSessionControl: (handler: (action: SessionControlAction) => void) => () => void;
  pickDirectory: () => Promise<string | null>;
  getDownloadsPath: () => Promise<string>;
  getDesktopPath: () => Promise<string>;
  getPathForFile: (file: File) => string;
  readConstellation: () => Promise<{ version: 1; stars: unknown[]; links: unknown[] }>;
  writeConstellation: (payload: { version: 1; stars: unknown[]; links: unknown[] }) => Promise<boolean>;
  clearConstellation: () => Promise<boolean>;
};
