import { existsSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { BrowserWindow, ipcMain, type BrowserWindowConstructorOptions } from "electron";

export type ManagedConfirmSection = {
  heading: string;
  body?: string;
  bullets?: string[];
  pairs?: Array<[string, string]>;
  tone?: "default" | "warn" | "danger";
};

export type ManagedConfirmOptions = {
  parent: BrowserWindow;
  /** Absolute directory that contains managed-confirm-dialog.html + managed-confirm-preload.js */
  assetDir: string;
  title: string;
  message: string;
  sections?: ManagedConfirmSection[];
  confirmLabel: string;
  cancelLabel?: string;
  width?: number;
  height?: number;
};

/**
 * Host-owned Neon Twin confirm window.
 * Confirmation stays on the main process (no pack serialization to the workbench),
 * with Cancel as the safe default focus — same posture as showMessageBox defaultId/cancelId = 1.
 */
export function showManagedConfirmDialog(options: ManagedConfirmOptions): Promise<boolean> {
  const parent = options.parent;
  if (!parent || parent.isDestroyed()) return Promise.resolve(false);

  const htmlFile = path.join(options.assetDir, "managed-confirm-dialog.html");
  const preloadFile = path.join(options.assetDir, "managed-confirm-preload.js");
  if (!existsSync(htmlFile) || !existsSync(preloadFile)) return Promise.resolve(false);

  const payload = {
    title: options.title,
    message: options.message,
    sections: options.sections ?? [],
    confirmLabel: options.confirmLabel,
    cancelLabel: options.cancelLabel ?? "取消",
  };

  const winOptions: BrowserWindowConstructorOptions = {
    width: options.width ?? 760,
    height: options.height ?? 640,
    parent,
    modal: true,
    show: false,
    resizable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    autoHideMenuBar: true,
    backgroundColor: "#0f1220",
    webPreferences: {
      preload: preloadFile,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  };

  const win = new BrowserWindow(winOptions);
  const channel = "managed-confirm-result";

  return new Promise<boolean>((resolve) => {
    let settled = false;
    const finish = (ok: boolean) => {
      if (settled) return;
      settled = true;
      ipcMain.removeListener(channel, onResult);
      if (!win.isDestroyed()) {
        win.removeAllListeners("closed");
        try { win.close(); } catch { /* ignore */ }
      }
      resolve(Boolean(ok));
    };

    const onResult = (event: Electron.IpcMainEvent, ok: unknown) => {
      if (event.sender !== win.webContents) return;
      finish(ok === true);
    };

    ipcMain.on(channel, onResult);
    win.on("closed", () => finish(false));

    const target = pathToFileURL(htmlFile).href + "#" + encodeURIComponent(JSON.stringify(payload));
    void win.loadURL(target).then(() => {
      if (!win.isDestroyed()) {
        win.show();
        win.focus();
      }
    }).catch(() => finish(false));
  });
}

/** Summarize blocker codes for confirm cards. */
export function summarizeBlockers(label: string, values: unknown): ManagedConfirmSection {
  const list = Array.isArray(values) ? values : [];
  const counts = new Map<string, number>();
  for (const item of list) {
    const value = item && typeof item === "object" && !Array.isArray(item) ? item as Record<string, unknown> : {};
    const code = typeof value.code === "string" && value.code ? value.code : "unknown_blocker";
    counts.set(code, (counts.get(code) ?? 0) + 1);
  }
  return {
    heading: `${label} · ${list.length} 项 / ${counts.size} 类`,
    bullets: [...counts].map(([code, count]) => `${code} · ${count} 项`),
    tone: list.length ? "warn" : "default",
  };
}
