import type { ProposalItem } from "../../api/ws";
import { escapeHtml } from "../chat-state";

export interface TopbarState {
  proposals: ProposalItem[];
  intentLabel: string;
  checkerLabel: string;
  memoryLabel: string;
  projectLabel: string;
  sessionCount: number;
}

export function renderTopbar(
  container: HTMLElement,
  state: TopbarState,
  onOpenProposals: () => void,
  onNewSession?: () => void,
  onOpenSessions?: () => void,
): void {
  const proposal = state.proposals[0];
  const project = state.projectLabel
    ? `<span class="unified-topbar-mark" aria-hidden="true">◆</span><span class="unified-topbar-text">${escapeHtml(state.projectLabel)}</span>`
    : "";
  const memory = state.memoryLabel
    ? `<span class="unified-topbar-muted">${escapeHtml(state.memoryLabel)}</span>`
    : "";
  const intent = state.intentLabel
    ? `<span class="unified-topbar-muted">${escapeHtml(state.intentLabel)}</span>`
    : "";
  const checker = state.checkerLabel
    ? `<span class="unified-topbar-muted">${escapeHtml(state.checkerLabel)}</span>`
    : "";
  const newSessionBtn = onNewSession
    ? `<button type="button" class="unified-btn" id="unified-new-session" title="新会话">+</button>`
    : "";
  const sessionsBtn = onOpenSessions
    ? `<button type="button" class="unified-btn" id="unified-open-sessions" title="最近会话">${state.sessionCount ? `会话 (${state.sessionCount})` : "会话"}</button>`
    : "";
  const meta = [project, intent, checker, memory].filter(Boolean).join(" · ");
  const metaText = meta || `<span class="unified-topbar-muted">当前无待处理</span>`;

  if (!proposal) {
    container.innerHTML = `
      ${newSessionBtn}
      ${sessionsBtn}
      <span class="unified-topbar-text">${metaText}</span>
    `;
    container.querySelector<HTMLButtonElement>("#unified-new-session")?.addEventListener("click", () => onNewSession?.());
    container.querySelector<HTMLButtonElement>("#unified-open-sessions")?.addEventListener("click", () => onOpenSessions?.());
    return;
  }

  const count = state.proposals.length;
  const label = proposal.summary || proposal.proposal_id;
  container.innerHTML = `
    ${newSessionBtn}
    ${sessionsBtn}
    <span class="unified-topbar-mark" aria-hidden="true">■</span>
    <span class="unified-topbar-text">当前：${count} 条 proposal 待接受 · ${escapeHtml(label)}</span>
    <button type="button" class="unified-btn unified-btn-accent" id="unified-open-proposal">去处理</button>
  `;
  container.querySelector<HTMLButtonElement>("#unified-new-session")?.addEventListener("click", () => onNewSession?.());
  container.querySelector<HTMLButtonElement>("#unified-open-sessions")?.addEventListener("click", () => onOpenSessions?.());
  container.querySelector<HTMLButtonElement>("#unified-open-proposal")?.addEventListener("click", onOpenProposals);
}
