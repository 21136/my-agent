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

export type TopbarHandlers = {
  onNewChat?: () => void;
  onNewProject?: () => void;
  onNewThread?: () => void;
  onOpenSessions?: () => void;
  onOpenProposals?: () => void;
};

export function renderTopbar(
  container: HTMLElement,
  state: TopbarState,
  onOpenProposals: () => void,
  onNewSession?: () => void,
  onOpenSessions?: () => void,
  onNewProject?: () => void,
  onNewThread?: () => void,
): void {
  // Backward-compatible: onNewSession = 普通对话 +
  const handlers: TopbarHandlers = {
    onNewChat: onNewSession,
    onNewProject,
    onNewThread,
    onOpenSessions,
    onOpenProposals,
  };
  renderTopbarV2(container, state, handlers);
}

/** Preferred entry: dual plus buttons (UX-POLISH §7.6). */
export function renderTopbarV2(
  container: HTMLElement,
  state: TopbarState,
  handlers: TopbarHandlers,
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
  const newChatBtn = handlers.onNewChat
    ? `<button type="button" class="unified-btn" id="unified-new-chat" title="普通对话">+ 对话</button>`
    : "";
  const newProjectBtn = handlers.onNewProject
    ? `<button type="button" class="unified-btn" id="unified-new-project" title="新建项目">+ 项目</button>`
    : "";
  const newThreadBtn = handlers.onNewThread && state.projectLabel
    ? `<button type="button" class="unified-btn" id="unified-new-thread" title="同项目新开线（归档当前聊天）">+ 新开线</button>`
    : "";
  const sessionsBtn = handlers.onOpenSessions
    ? `<button type="button" class="unified-btn" id="unified-open-sessions" title="最近会话">${state.sessionCount ? `会话 (${state.sessionCount})` : "会话"}</button>`
    : "";
  const meta = [project, intent, checker, memory].filter(Boolean).join(" · ");
  const metaText = meta || `<span class="unified-topbar-muted">当前无待处理</span>`;
  const leading = `${newChatBtn}${newThreadBtn}${newProjectBtn}${sessionsBtn}`;

  if (!proposal) {
    container.innerHTML = `
      ${leading}
      <span class="unified-topbar-text">${metaText}</span>
    `;
  } else {
    const count = state.proposals.length;
    const label = proposal.summary || proposal.proposal_id;
    container.innerHTML = `
      ${leading}
      <span class="unified-topbar-mark" aria-hidden="true">■</span>
      <span class="unified-topbar-text">当前：${count} 条 proposal 待接受 · ${escapeHtml(label)}</span>
      <button type="button" class="unified-btn unified-btn-accent" id="unified-open-proposal">去处理</button>
    `;
    container.querySelector<HTMLButtonElement>("#unified-open-proposal")?.addEventListener("click", () => handlers.onOpenProposals?.());
  }

  container.querySelector<HTMLButtonElement>("#unified-new-chat")?.addEventListener("click", () => handlers.onNewChat?.());
  container.querySelector<HTMLButtonElement>("#unified-new-thread")?.addEventListener("click", () => handlers.onNewThread?.());
  container.querySelector<HTMLButtonElement>("#unified-new-project")?.addEventListener("click", () => handlers.onNewProject?.());
  container.querySelector<HTMLButtonElement>("#unified-open-sessions")?.addEventListener("click", () => handlers.onOpenSessions?.());
}
