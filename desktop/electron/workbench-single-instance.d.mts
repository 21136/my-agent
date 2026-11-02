export interface WorkbenchWindow {
  isDestroyed(): boolean;
  isVisible(): boolean;
  isMinimized(): boolean;
  restore(): void;
  show(): void;
  focus(): void;
}

export function activateWorkbenchWindow<W extends WorkbenchWindow>(
  window: W | null,
  sendSessionControl: (window: W, action: "resume") => void,
): boolean;

export function installWorkbenchSingleInstance<W extends Pick<WorkbenchWindow, "isDestroyed">>(options: {
  app: {
    requestSingleInstanceLock(additionalData: Record<string, unknown>): boolean;
    on(event: "second-instance", listener: () => void): unknown;
  };
  profile: { kind: "normal" | "fixed-host" | "evolution-test"; userDataPath: string };
  processType?: string;
  getWindow: () => W | null;
  activateWindow: (window: W) => void;
}): {
  ownsInstance: boolean;
  windowReady(): void;
  windowUnavailable(): void;
};
