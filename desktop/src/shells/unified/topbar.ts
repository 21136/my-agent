import type { ProposalItem } from "../../api/ws";
import { escapeHtml } from "../chat-state";

export interface TopbarState {
  proposals: ProposalItem[];
  /** @deprecated M2 — no longer rendered in chrome */
  intentLabel: string;
  /** @deprecated M2 — no longer rendered in chrome */
  checkerLabel: string;
  /** @deprecated M2 — no longer rendered in chrome */
  memoryLabel: string;
  /** Current project id (display name). */
  projectLabel: string;
  /** One-line goal / status from deriveProjectGoalViewModel.title */
  contextLabel: string;
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
  const handlers: TopbarHandlers = {
    onNewChat: onNewSession,
    onNewProject,
    onNewThread,
    onOpenSessions,
    onOpenProposals,
  };
  renderTopbarV2(container, state, handlers);
}

function renderOverflowMenu(state: TopbarState, handlers: TopbarHandlers): string {
  const items: string[] = [];
  if (handlers.onNewChat && state.projectLabel) {
    items.push(`<button type="button" class="unified-topbar-menu-item" id="unified-new-chat">挂起项目，开普通对话</button>`);
  }
  if (handlers.onNewThread && state.projectLabel) {
    items.push(`<button type="button" class="unified-topbar-menu-item" id="unified-new-thread">同项目新开线</button>`);
  }
  if (handlers.onNewProject) {
    items.push(`<button type="button" class="unified-topbar-menu-item" id="unified-new-project">新建项目</button>`);
  }
  if (!items.length) return "";
  return `<details class="unified-topbar-menu">
    <summary class="unified-btn unified-btn-ghost unified-topbar-menu-trigger" aria-label="更多操作">⋯</summary>
    <div class="unified-topbar-menu-panel" role="menu">${items.join("")}</div>
  </details>`;
}

function bindTopbarHandlers(container: HTMLElement, handlers: TopbarHandlers): void {
  container.querySelector<HTMLButtonElement>("#unified-new-chat")?.addEventListener("click", () => handlers.onNewChat?.());
  container.querySelector<HTMLButtonElement>("#unified-new-thread")?.addEventListener("click", () => handlers.onNewThread?.());
  container.querySelector<HTMLButtonElement>("#unified-new-project")?.addEventListener("click", () => handlers.onNewProject?.());
  container.querySelector<HTMLButtonElement>("#unified-open-sessions")?.addEventListener("click", () => handlers.onOpenSessions?.());
  container.querySelector<HTMLButtonElement>("#unified-open-proposal")?.addEventListener("click", () => handlers.onOpenProposals?.());

  const menu = container.querySelector<HTMLDetailsElement>(".unified-topbar-menu");
  if (menu) {
    menu.querySelectorAll<HTMLButtonElement>(".unified-topbar-menu-item").forEach((btn) => {
      btn.addEventListener("click", () => {
        menu.open = false;
      });
    });
    menu.addEventListener("toggle", () => {
      if (!menu.open) return;
      const close = (ev: MouseEvent) => {
        if (!menu.contains(ev.target as Node)) {
          menu.open = false;
          document.removeEventListener("click", close);
        }
      };
      window.setTimeout(() => document.addEventListener("click", close), 0);
    });
  }
}

/** UX-029 — project + overflow menu, one-line context, sessions on the right. */
export function renderTopbarV2(
  container: HTMLElement,
  state: TopbarState,
  handlers: TopbarHandlers,
): void {
  const proposal = state.proposals[0];
  const projectName = state.projectLabel
    ? `<span class="unified-topbar-project" title="${escapeHtml(state.projectLabel)}">${escapeHtml(state.projectLabel)}</span>`
    : "";
  const newProjectStandalone = !state.projectLabel && handlers.onNewProject
    ? `<button type="button" class="unified-btn unified-btn-ghost" id="unified-new-project">新建项目</button>`
    : "";
  const leading = `<div class="unified-topbar-leading">${projectName}${renderOverflowMenu(state, handlers)}${newProjectStandalone}</div>`;

  const sessionsBtn = handlers.onOpenSessions
    ? `<button type="button" class="unified-btn unified-btn-ghost" id="unified-open-sessions" title="最近会话">${state.sessionCount ? `会话 ${state.sessionCount}` : "会话"}</button>`
    : "";
  const trailing = `<div class="unified-topbar-trailing">${sessionsBtn}</div>`;

  if (!proposal) {
    const context = state.contextLabel
      ? `<span class="unified-topbar-context-text" title="${escapeHtml(state.contextLabel)}">${escapeHtml(state.contextLabel)}</span>`
      : `<span class="unified-topbar-context-text is-muted">当前无待处理</span>`;
    container.innerHTML = `${leading}<div class="unified-topbar-context">${context}</div>${trailing}`;
  } else {
    const count = state.proposals.length;
    const label = proposal.summary || proposal.proposal_id;
    const context = `<span class="unified-topbar-context-text">${count} 条待处理 · ${escapeHtml(label)}</span>
      <button type="button" class="unified-btn unified-btn-accent unified-topbar-proposal-btn" id="unified-open-proposal">去处理</button>`;
    container.innerHTML = `${leading}<div class="unified-topbar-context is-proposal">${context}</div>${trailing}`;
  }

  bindTopbarHandlers(container, handlers);
}
