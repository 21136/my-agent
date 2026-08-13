let busy = false;

function syncAppFrame(): void {
  const frame = document.querySelector<HTMLElement>(".app-frame");
  if (!frame) return;
  frame.classList.toggle("is-agent-busy", busy);
}

export function setAgentBusy(on: boolean, _shell?: string): void {
  busy = on;
  syncAppFrame();
  const modelSelect = document.querySelector<HTMLSelectElement>("#chrome-model");
  if (modelSelect) {
    modelSelect.disabled = on;
    if (on) {
      modelSelect.title = "回合进行中，结束后再切换模型";
    }
  }
}

export function isAgentBusy(): boolean {
  return busy;
}
