export type AgentBusyShell = "daily" | "grow" | "project";

const shellBusy: Record<AgentBusyShell, boolean> = { daily: false, grow: false, project: false };
let activeShell: AgentBusyShell = "grow";

function syncAppFrame(): void {
  const frame = document.querySelector<HTMLElement>(".app-frame");
  if (!frame) return;

  const on = shellBusy[activeShell];
  frame.classList.toggle("is-agent-busy", on);
  if (on) {
    frame.dataset.busyShell = activeShell;
  } else {
    delete frame.dataset.busyShell;
  }
}

/** 当前可见外壳（决定全窗染色的色带） */
export function setActiveShell(shell: AgentBusyShell): void {
  activeShell = shell;
  syncAppFrame();
}

export function setAgentBusy(busy: boolean, shell?: AgentBusyShell): void {
  if (shell) {
    shellBusy[shell] = busy;
  }
  syncAppFrame();
}

/** 任外壳在跑（关窗确认等） */
export function isAgentBusy(): boolean {
  return shellBusy.daily || shellBusy.grow || shellBusy.project;
}
