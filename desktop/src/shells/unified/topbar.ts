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
  /** @deprecated M2 — no longer rendered in chrome */
  intentLabel: string;
  /** @deprecated M2 — no longer rendered in chrome */
  checkerLabel: string;
  /** @deprecated M2 — no longer rendered in chrome */
  memoryLabel: string;
  /** Accepted long-term memories count (shelf chip). */
  memoryCount: number;
  /** Pending evolve proposals count for shelf chip. */
  pendingMemoryCount: number;
  /** Current project id (display name). */
  projectLabel: string;
  /** One-line goal / status from deriveProjectGoalViewModel.title */
  contextLabel: string;
  sessionCount: number;
  headerCtas: HeaderNextStepAction[];
  currentWorkOverview?: CurrentWorkOverviewPanel;
  autoRecognition?: { enabled: boolean; pending: boolean; available: boolean; error: boolean };
}

export type TopbarHandlers = {
  onAutoRecognition?: (action: "enable" | "revoke") => void;
  onSelectTool?: (tool: "now" | "docs" | "settings" | "music" | "shell-dev" | "runtime") => void;
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
  onAutoRecognition?: TopbarHandlers["onAutoRecognition"],
): void {
  const handlers: TopbarHandlers = {
    onSelectTool,
    onAutoRecognition,
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
  };
  renderTopbarV2(container, state, handlers);
}

function renderOverflowMenu(state: TopbarState, handlers: TopbarHandlers): string {
  const items: string[] = [];
  if (handlers.onSelectTool) {
    for (const [id, label] of [["now", "返回对话"], ["docs", "文档"], ["settings", "设置"], ["music", "听歌"], ["shell-dev", "双车道"], ["runtime", "终端与服务"]] as const) {
      items.push(`<button type="button" class="unified-topbar-menu-item" id="unified-tool-${id}">${label}</button>`);
    }
  }
  if (handlers.onAutoRecognition && state.autoRecognition) {
    const recognition = state.autoRecognition;
    items.push(`<div class="unified-topbar-menu-item" role="group" aria-label="当前会话自动识别"><strong>当前会话自动识别：${recognition.pending ? "等待服务确认" : recognition.enabled ? "已开启" : "关闭"}</strong><p>开启后将当前会话新消息和已有事项的标题、范围、标识发送给 0x567 gpt-6-luna 识别；候选保存仍需你确认。切换会话或断连后授权失效，可随时撤销。撤销阻止尚未开始的识别；已启动的请求可能仍会发出，无法撤回。</p>${recognition.error ? '<p role="status">请求未获确认，请核对当前状态后重试。</p>' : ''}<button type="button" id="unified-auto-recognition-${recognition.enabled ? 'revoke' : 'enable'}" ${recognition.pending || !recognition.available ? 'disabled' : ''}>${recognition.enabled ? '撤销自动识别' : '同意并开启自动识别'}</button></div>`);
  }
  if (handlers.onNewChat && state.projectLabel) {
    items.push(`<button type="button" class="unified-topbar-menu-item" id="unified-new-chat">挂起项目，开普通对话</button>`);
  }
  if (handlers.onNewThread && state.projectLabel) {
    items.push(`<button type="button" class="unified-topbar-menu-item" id="unified-new-thread">同项目新开线</button>`);
  }
  if (handlers.onNewProject) {
    items.push(`<button type="button" class="unified-topbar-menu-item" id="unified-new-project">新建项目</button>`);
  }
  if (handlers.onOpenSessions && state.headerCtas.length) {
    items.push(`<button type="button" class="unified-topbar-menu-item" id="unified-open-sessions">会话${state.sessionCount ? ` ${state.sessionCount}` : ""}</button>`);
  }
  if (!items.length) return "";
  return `<details class="unified-topbar-menu">
    <summary class="unified-btn unified-btn-ghost unified-topbar-menu-trigger" aria-label="更多操作">⋯</summary>
    <div class="unified-topbar-menu-panel" role="menu">${items.join("")}</div>
  </details>`;
}

function bindTopbarHandlers(container: HTMLElement, handlers: TopbarHandlers): void {
  for (const tool of ["now", "docs", "settings", "music", "shell-dev", "runtime"] as const) {
    container.querySelector<HTMLButtonElement>(`#unified-tool-${tool}`)?.addEventListener("click", () => handlers.onSelectTool?.(tool));
  }
  container.querySelector<HTMLButtonElement>("#unified-auto-recognition-enable")?.addEventListener("click", () => handlers.onAutoRecognition?.("enable"));
  container.querySelector<HTMLButtonElement>("#unified-auto-recognition-revoke")?.addEventListener("click", () => handlers.onAutoRecognition?.("revoke"));
  container.querySelector<HTMLButtonElement>("#unified-new-chat")?.addEventListener("click", () => handlers.onNewChat?.());
  container.querySelector<HTMLButtonElement>("#unified-new-thread")?.addEventListener("click", () => handlers.onNewThread?.());
  container.querySelector<HTMLButtonElement>("#unified-new-project")?.addEventListener("click", () => handlers.onNewProject?.());
  container.querySelector<HTMLButtonElement>("#unified-open-sessions")?.addEventListener("click", () => handlers.onOpenSessions?.());
  container.querySelector<HTMLButtonElement>("#unified-open-proposal")?.addEventListener("click", () => handlers.onOpenProposals?.());
  container.querySelector<HTMLButtonElement>("#unified-open-memory")?.addEventListener("click", () => handlers.onOpenMemory?.());
  container.querySelector<HTMLButtonElement>("#unified-request-current-work")?.addEventListener("click", () => handlers.onToggleCurrentWorkOverview?.());
  container.querySelector<HTMLButtonElement>("#unified-current-work-close")?.addEventListener("click", () => handlers.onToggleCurrentWorkOverview?.());
  container.querySelector<HTMLButtonElement>("#unified-current-work-more")?.addEventListener("click", () => handlers.onLoadMoreCurrentWorkOverview?.());
  container.querySelector<HTMLButtonElement>("#unified-current-work-ask")?.addEventListener("click", () => handlers.onRequestCurrentWorkOverview?.());
  container.querySelectorAll<HTMLButtonElement>(".unified-header-cta").forEach((btn) => {
    btn.addEventListener("click", () => handlers.onHeaderAction?.(btn.dataset.action || ""));
  });

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
  // A background state refresh replaces the DOM; do not dismiss a menu the user is using.
  const menuWasOpen = container.querySelector<HTMLDetailsElement>(".unified-topbar-menu")?.open === true;
  const proposal = state.proposals[0];
  const projectName = state.projectLabel
    ? `<span class="unified-topbar-project" title="${escapeHtml(state.projectLabel)}">${escapeHtml(state.projectLabel)}</span>`
    : "";
  const newProjectStandalone = !state.projectLabel && handlers.onNewProject
    ? `<button type="button" class="unified-btn unified-btn-ghost" id="unified-new-project">新建项目</button>`
    : "";
  const leading = `<div class="unified-topbar-leading">${projectName}${renderOverflowMenu(state, handlers)}${newProjectStandalone}</div>`;

  const sessionsBtn = handlers.onOpenSessions && !state.headerCtas.length
    ? `<button type="button" class="unified-btn unified-btn-ghost" id="unified-open-sessions" title="最近会话">${state.sessionCount ? `会话 ${state.sessionCount}` : "会话"}</button>`
    : "";
  const memoryCount = state.memoryCount ?? 0;
  const pendingCount = state.pendingMemoryCount ?? 0;
  const memoryChipLabel = pendingCount > 0
    ? `记忆 ${memoryCount} · 待审 ${pendingCount}`
    : memoryCount > 0
      ? `记忆 ${memoryCount}`
      : "记忆";
  const memoryBtn = handlers.onOpenMemory
    ? `<button type="button" class="unified-btn unified-btn-ghost unified-topbar-memory-btn" id="unified-open-memory" title="记住了什么">${memoryChipLabel}</button>`
    : "";

  const overview = state.currentWorkOverview;
  const currentWorkBtn = overview?.visible
    ? `<div class="unified-topbar-current-work-wrap"><button type="button" class="unified-btn unified-btn-ghost unified-topbar-current-work" id="unified-request-current-work" aria-controls="unified-current-work-popover" aria-expanded="${overview.open ? "true" : "false"}" title="查看当前会话工作概况">${escapeHtml(overview.label)}</button>${overview.open ? `<section id="unified-current-work-popover" class="unified-current-work-popover" aria-label="当前工作概况"><header class="unified-current-work-heading"><div><span>当前会话</span><h3>工作概况</h3></div><button type="button" class="unified-current-work-close" id="unified-current-work-close" aria-label="关闭工作概况" title="关闭">×</button></header><div class="unified-current-work-content">${overview.bodyHtml}</div><div class="unified-current-work-actions">${overview.canExpand ? `<button type="button" class="unified-btn unified-btn-ghost" id="unified-current-work-more" ${overview.loadingMore ? "disabled" : ""}>${escapeHtml(overview.moreLabel)}</button>` : ""}<button type="button" class="unified-btn unified-btn-ghost" id="unified-current-work-ask">在对话中查询</button></div></section>` : ""}</div>`
    : "";
  const trailing = `<div class="unified-topbar-trailing">${currentWorkBtn}${memoryBtn}${sessionsBtn}</div>`;

  const headerCtas = state.headerCtas.length
    ? `<div class="unified-header-ctas">${state.headerCtas.map((item) => {
      const accent = item.accent ? " unified-btn-accent" : "";
      return `<button type="button" class="unified-btn${accent} unified-header-cta" data-action="${escapeHtml(item.action)}">${escapeHtml(item.label)}</button>`;
    }).join("")}</div>`
    : "";

  if (!proposal) {
    if (!state.projectLabel && !state.contextLabel && !state.headerCtas.length && !handlers.onOpenMemory && !handlers.onSelectTool && !state.currentWorkOverview?.visible) {
      container.innerHTML = "";
      container.classList.add("is-empty-bar");
      return;
    }
    container.classList.remove("is-empty-bar");
    const context = state.contextLabel
      ? `<span class="unified-topbar-context-text" title="${escapeHtml(state.contextLabel)}">${escapeHtml(state.contextLabel)}</span>`
      : "";
    container.innerHTML = `${leading}<div class="unified-topbar-context${headerCtas ? " has-next-step" : ""}">${context}${headerCtas}</div>${trailing}`;
  } else {
    container.classList.remove("is-empty-bar");
    const count = state.proposals.length;
    const label = proposal.summary || proposal.proposal_id;
    const context = `<span class="unified-topbar-context-text">${count} 条待处理 · ${escapeHtml(label)}</span>
      <button type="button" class="unified-btn unified-btn-accent unified-topbar-proposal-btn" id="unified-open-proposal">去处理</button>`;
    container.innerHTML = `${leading}<div class="unified-topbar-context is-proposal">${context}</div>${trailing}`;
  }

  bindTopbarHandlers(container, handlers);
  const menu = container.querySelector<HTMLDetailsElement>(".unified-topbar-menu");
  if (menuWasOpen && menu) menu.open = true;
}
