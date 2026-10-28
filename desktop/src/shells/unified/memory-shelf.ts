import type { AgentWsClient, MemoryItem, ProposalItem } from "../../api/ws";
import { escapeHtml } from "../chat-state";
import { memoryShelfLabel } from "./memory-shelf-model";

export { memoryShelfLabel } from "./memory-shelf-model";

export interface MemoryShelfState {
  memories: MemoryItem[];
  pending: ProposalItem[];
  expandOpen: boolean;
}

export function renderMemoryShelf(
  container: HTMLElement,
  state: MemoryShelfState,
  client: AgentWsClient,
  onOpenProposals?: () => void,
): void {
  if (!state.expandOpen) {
    container.classList.add("hidden");
    container.innerHTML = "";
    return;
  }

  container.classList.remove("hidden");
  const memoriesHtml = state.memories.length
    ? state.memories
        .map((item) => {
          const topics = item.topics?.length
            ? `<span class="text-muted">${escapeHtml(item.topics.join(" · "))}</span>`
            : "";
          return `<div class="unified-expand-item unified-memory-item">
            <div class="unified-expand-item-title">${escapeHtml(item.memory_id)}</div>
            <div class="unified-expand-item-meta">${escapeHtml(item.summary)}</div>
            ${topics}
          </div>`;
        })
        .join("")
    : `<div class="text-muted unified-memory-empty">还没记住什么。说「记住…」或压缩对话后，会生成待你审阅的沉淀。</div>`;

  const pendingHtml = state.pending.length
    ? `<div class="unified-memory-pending">
        <div class="unified-expand-title">待审 ${state.pending.length}</div>
        <div class="text-muted">压缩或「记住」生成的提案，接受后才会进久远记忆。</div>
        <button type="button" class="unified-btn unified-btn-accent" data-action="open-proposals">去处理提案</button>
      </div>`
    : "";

  container.innerHTML = `
    <div class="unified-surface unified-memory-shelf">
      <div class="unified-expand-title">记住了什么</div>
      <div class="text-muted">久远记忆（已接受）· ${memoryShelfLabel(state)}</div>
      <div class="unified-memory-list">${memoriesHtml}</div>
      ${pendingHtml}
      <div class="unified-expand-actions">
        <button type="button" class="unified-btn" data-action="refresh">刷新</button>
        <button type="button" class="unified-btn" data-action="close">收起</button>
      </div>
    </div>
  `;

  container.querySelector<HTMLButtonElement>('[data-action="close"]')?.addEventListener("click", () => {
    container.dispatchEvent(new CustomEvent("memory:close"));
  });
  container.querySelector<HTMLButtonElement>('[data-action="refresh"]')?.addEventListener("click", () => {
    client.listMemories();
  });
  container.querySelector<HTMLButtonElement>('[data-action="open-proposals"]')?.addEventListener("click", () => {
    onOpenProposals?.();
  });
}
