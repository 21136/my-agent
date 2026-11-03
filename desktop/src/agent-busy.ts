let busy = false;

function syncAppFrame(): void {
  const frame = document.querySelector<HTMLElement>(".app-frame");
  if (!frame) return;
  frame.classList.toggle("is-agent-busy", busy);
}

export function setAgentBusy(on: boolean, _shell?: string): void {
  busy = on;
  syncAppFrame();
}

export function isAgentBusy(): boolean {
  return busy;
}
