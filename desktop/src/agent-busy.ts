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
    modelSelect.title = on ? "回合进行中，结束后再切换模型" : "切换主 Agent 模型（Flash 128k / Pro 1M）";
  }
}

export function isAgentBusy(): boolean {
  return busy;
}
