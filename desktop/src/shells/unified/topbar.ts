import type { CurrentWorkOverviewPanel } from "./current-work-overview";
import type { ProposalItem } from "../../api/ws";
import { escapeHtml } from "../chat-state";

export interface HeaderNextStepAction {
  action: string;
  label: string;
  accent?: boolean;
}

export interface TopbarState {
  proposals: ProposalItem[];
  intentLabel: string;
  checkerLabel: string;
  memoryLabel: string;
  memoryCount: number;
  pendingMemoryCount: number;
  projectLabel: string;
  contextLabel: string;
  sessionCount: number;
  headerCtas: HeaderNextStepAction[];
  currentWorkOverview?: CurrentWorkOverviewPanel;
  autoRecognition?: { enabled: boolean; pending: boolean; available: boolean; error: boolean };
}

export type TopbarHandlers = {
  onSelectTool?: (tool: "now" | "docs" | "settings" | "music" | "shell-dev" | "runtime") => void;
  onAutoRecognition?: (action: "enable" | "revoke") => void;
  onNewChat?: () => void;
  onNewProject?: () => void;
  onNewThread?: () => void;
  onOpenSessions?: () => void;
  onOpenProposals?: () => void;
  onOpenMemory?: () => void;
  onRequestCurrentWorkOverview?: () => void;
  onToggleCurrentWorkOverview?: () => void;
  onLoadMoreCurrentWorkOverview?: () => void;
  onHeaderAction?: (action: string) => void;
};

export function renderTopbar(
  container: HTMLElement,
  state: TopbarState,
  onOpenProposals: () => void,
  onNewSession?: () => void,
  onOpenSessions?: () => void,
  onNewProject?: () => void,
  onNewThread?: () => void,
  onHeaderAction?: (action: string) => void,
  onOpenMemory?: () => void,
  onRequestCurrentWorkOverview?: () => void,
  onToggleCurrentWorkOverview?: () => void,
  onLoadMoreCurrentWorkOverview?: () => void,
  onSelectTool?: TopbarHandlers["onSelectTool"],
): void {
  renderTopbarV2(container, state, {
    onSelectTool,
    onNewChat: onNewSession,
    onNewProject,
    onNewThread,
    onOpenSessions,
    onOpenProposals,
    onOpenMemory,
    onRequestCurrentWorkOverview,
    onToggleCurrentWorkOverview,
    onLoadMoreCurrentWorkOverview,
    onHeaderAction,
  });
}

const DESTINATIONS = [
  ["docs", "文档"],
  ["music", "听歌"],
  ["shell-dev", "双车道"],
  ["settings", "设置"],
] as const;

function renderTopbarShortcuts(handlers: TopbarHandlers): string {
  if (!handlers.onSelectTool) return "";
  return `<nav class="unified-topbar-shortcuts" aria-label="主导航">${DESTINATIONS.map(([id, label]) =>
    `<button type="button" class="unified-btn unified-btn-ghost unified-topbar-shortcut" id="unified-tool-${id}" aria-label="${label}">${label}</button>`
  ).join("")}</nav>`;
}

function bindTopbarHandlers(container: HTMLElement, handlers: TopbarHandlers): void {
  for (const [id] of DESTINATIONS) {
    container.querySelector<HTMLButtonElement>(`#unified-tool-${id}`)?.addEventListener("click", () => handlers.onSelectTool?.(id));
  }
  container.querySelector<HTMLButtonElement>("#unified-new-project")?.addEventListener("click", () => handlers.onNewProject?.());
  container.querySelector<HTMLButtonElement>("#unified-open-sessions")?.addEventListener("click", () => handlers.onOpenSessions?.());
  container.querySelector<HTMLButtonElement>("#unified-open-proposal")?.addEventListener("click", () => handlers.onOpenProposals?.());
  container.querySelector<HTMLButtonElement>("#unified-open-memory")?.addEventListener("click", () => handlers.onOpenMemory?.());
  container.querySelector<HTMLButtonElement>("#unified-request-current-work")?.addEventListener("click", () => handlers.onToggleCurrentWorkOverview?.());
  container.querySelectorAll<HTMLButtonElement>(".unified-header-cta").forEach(button => {
    button.addEventListener("click", () => handlers.onHeaderAction?.(button.dataset.action || ""));
  });
}

/** Direct destinations stay visible; contextual and utility entries remain available without an overflow menu. */
export function renderTopbarV2(container: HTMLElement, state: TopbarState, handlers: TopbarHandlers): void {
  const proposal = state.proposals[0];
  const projectName = state.projectLabel
    ? `<span class="unified-topbar-project" title="${escapeHtml(state.projectLabel)}">${escapeHtml(state.projectLabel)}</span>`
    : "";
  const newProject = !state.projectLabel && handlers.onNewProject
    ? `<button type="button" class="unified-btn unified-btn-ghost" id="unified-new-project">新建项目</button>`
    : "";
  const leading = `<div class="unified-topbar-leading">${projectName}${renderTopbarShortcuts(handlers)}${newProject}</div>`;

  // Single-chat callers leave this callback unset; retaining the optional path does not add a session list.
  const sessionsButton = handlers.onOpenSessions && !state.headerCtas.length
    ? `<button type="button" class="unified-btn unified-btn-ghost" id="unified-open-sessions" title="最近会话">会话${state.sessionCount ? ` ${state.sessionCount}` : ""}</button>`
    : "";
  const pendingMemoryCount = state.pendingMemoryCount ?? 0;
  const memoryCount = state.memoryCount ?? 0;
  const memoryLabel = pendingMemoryCount > 0
    ? `记忆 ${memoryCount} · 待审 ${pendingMemoryCount}`
    : memoryCount > 0 ? `记忆 ${memoryCount}` : "记忆";
  const memoryButton = handlers.onOpenMemory
    ? `<button type="button" class="unified-btn unified-btn-ghost unified-topbar-memory-btn" id="unified-open-memory" title="记住了什么">${memoryLabel}</button>`
    : "";

  const overview = state.currentWorkOverview;
  const currentWorkButton = overview?.visible
    ? `<div class="unified-topbar-current-work-wrap"><button type="button" class="unified-btn unified-btn-ghost unified-topbar-current-work" id="unified-request-current-work" aria-controls="unified-current-work-popover" aria-expanded="${overview.open ? "true" : "false"}" title="在主界面展开或收起当前工作">${escapeHtml(overview.label)}</button></div>`
    : "";
  const trailing = `<div class="unified-topbar-trailing">${currentWorkButton}${memoryButton}${sessionsButton}</div>`;
  const headerCtas = state.headerCtas.length
    ? `<div class="unified-header-ctas">${state.headerCtas.map(item => `<button type="button" class="unified-btn${item.accent ? " unified-btn-accent" : ""} unified-header-cta" data-action="${escapeHtml(item.action)}">${escapeHtml(item.label)}</button>`).join("")}</div>`
    : "";

  if (proposal) {
    const proposalLabel = proposal.summary || proposal.proposal_id;
    const context = `<span class="unified-topbar-context-text">${state.proposals.length} 条待处理 · ${escapeHtml(proposalLabel)}</span><button type="button" class="unified-btn unified-btn-accent unified-topbar-proposal-btn" id="unified-open-proposal">去处理</button>`;
    container.innerHTML = `${leading}<div class="unified-topbar-context is-proposal">${context}</div>${trailing}`;
  } else {
    if (!state.projectLabel && !state.contextLabel && !state.headerCtas.length && !handlers.onOpenMemory && !handlers.onSelectTool && !overview?.visible) {
      container.innerHTML = "";
      container.classList.add("is-empty-bar");
      return;
    }
    const context = state.contextLabel
      ? `<span class="unified-topbar-context-text" title="${escapeHtml(state.contextLabel)}">${escapeHtml(state.contextLabel)}</span>`
      : "";
    container.innerHTML = `${leading}<div class="unified-topbar-context${headerCtas ? " has-next-step" : ""}">${context}${headerCtas}</div>${trailing}`;
  }
  container.classList.remove("is-empty-bar");
  bindTopbarHandlers(container, handlers);
}
