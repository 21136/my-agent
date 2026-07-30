import type { AgentWsClient, ProposalItem, ServerEvent } from "../../api/ws";
import { setAgentBusy } from "../../agent-busy";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import { renderMarkdown } from "../../markdown";
import { formatUserMessageHtml } from "../../user-message";
import { createChatSession, escapeHtml, turnEndStatusText, checkerVerdictStatusText, type ChatBlock } from "../chat-state";
import { renderTopbar, type TopbarState } from "./topbar";
import { renderProposals, currentProposal, nextProposalIndex, type ProposalsState } from "./proposals";
import {
  setupProjectPanel,
  renderProjectSidebar,
  applyProjectStateEvent,
  applyProjectListEvent,
  applyProjectPlanState,
  parseTasksMarkdown,
  type OverlayPanel,
  type ProjectPanelState,
  type ProjectPanelCallbacks,
  type TaskItem,
} from "./project-panel";
import "./unified.css";

export type Perspective = "default" | "project" | "night";

const FOCUS_TURNS = 2;
const RECALL_TURNS = 3;

function isRecallIntent(intent: string, intentLabel: string): boolean {
  return intent === "recall" || intentLabel.includes("回顾");
}

function recentTurnIndices(blocks: ChatBlock[], k: number): number[] {
  const turns = new Set<number>();
  for (const block of blocks) {
    if (block.kind === "user" || block.kind === "assistant" || block.kind === "assistant-streaming") {
      turns.add(block.turnIndex);
    }
  }
  return [...turns].sort((a, b) => b - a).slice(0, k);
}

function isRecentTurn(turnIndex: number, currentTurnIndex: number): boolean {
  return turnIndex >= currentTurnIndex - (FOCUS_TURNS - 1);
}

function computeInitialPerspective(
  activeShell: string | undefined,
  projectRoot: string | undefined,
): Perspective {
  if (projectRoot) return "project";
  if (activeShell === "project" && !projectRoot) return "default";
  return "default";
}

export function mountUnifiedShell(
  root: HTMLElement,
  client: AgentWsClient,
  shellId: string = "grow",
): () => void {
  // ---- perspective state ----
  let perspective: Perspective = "default";
  let perspectiveLocked = false;

  function setPerspective(p: Perspective, reason: string = "manual"): void {
    if (perspectiveLocked && reason === "auto") return;
    perspective = p;
    shellEl.setAttribute("data-perspective", p);
    sidebarEl.classList.toggle("hidden", p !== "project");
    updatePlaceholder();
    renderChat();
    renderConfirmGlass();
    syncWorkingVisual();
  }

  // ---- ui state ----
  const topbarState: TopbarState = {
    proposals: [] as ProposalItem[],
    intentLabel: "",
    checkerLabel: "",
    memoryLabel: "",
    projectLabel: "",
    sessionCount: 0,
  };

  let sessionsDropdown: Array<{ session_id: string; title: string; updated_at: string }> = [];
  let sessionsOpen = false;

  const proposalsState: ProposalsState = {
    proposals: [] as ProposalItem[],
    proposalIndex: 0,
    expandOpen: false,
  };

  const projectState: ProjectPanelState = {
    projectId: "",
    planStatus: "",
    tasksMarkdown: "",
    mapMarkdown: "",
    tasksDone: 0,
    tasksTotal: 0,
    tasksAllDone: false,
    acceptanceCommand: "",
    canVerify: false,
    planOverlay: null,
    projects: [],
    switchOverlay: null,
    switchInProgress: false,
    pendingPickerId: "",
    verifyResult: null,
    verifyRunning: false,
    overlayPanel: null,
    taskPhases: [],
    planBannerCollapsed: true,
    switchConfirmTarget: null,
    projectSearchQuery: "",
    planChangeLog: [],
    taskSnapshot: { lines: new Set(), lineTexts: new Map() },
    highlightChanges: false,
    highlightedLines: new Set(),
    projectDocs: [],
    currentDocPath: "",
    currentDocContent: "",
    newDocName: "",
    quickAddText: "",
    detectedProject: null,
    planWarnings: [],
    undoDescription: "",
    undoTimerId: null,
  };

  let statusText = "连接中…";
  let cancelledStatusTimer: number | null = null;
  let cancelSafetyTimer: number | null = null;
  let destroyed = false;
  let thinkingStarted = 0;
  let thinkingTimer: number | null = null;
  let renderThrottleTimer: number | null = null;
  let renderedPrints: string[] = [];
  // night perspective state
  let recallHighlightTurns = new Set<number>();
  let focusObserver: IntersectionObserver | null = null;

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

  function clearCancelSafety(): void {
    if (cancelSafetyTimer !== null) {
      window.clearTimeout(cancelSafetyTimer);
      cancelSafetyTimer = null;
    }
  }

  // ---- chat session ----
  const chat = createChatSession(
    { showProcess: true, confirmInBlocks: true },
    {
      onChange: () => {
        renderChat();
        renderConfirmGlass();
        syncWorkingVisual();
      },
      onTurnStart: (event) => {
        topbarState.intentLabel = event.intent_label;
        topbarState.checkerLabel = "";
        if (perspective === "night" && isRecallIntent(event.intent, event.intent_label)) {
          recallHighlightTurns = new Set(recentTurnIndices(chat.model.blocks, RECALL_TURNS));
          renderChat();
          requestAnimationFrame(() => scrollToRecallTurns());
        }
        renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        setStatus(event.intent_label);
      },
      onCheckerVerdict: (event) => {
        topbarState.checkerLabel = checkerVerdictStatusText(event.verdict);
        renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        setStatus(topbarState.checkerLabel);
      },
      onConfirmRequest: () => {
        setComposerEnabled(false);
        setStatus("等待确认…");
      },
      onConfirmDone: (choice) => {
        setComposerEnabled(choice !== "cancelled");
        if (choice === "cancelled") {
          setStatus("正在停止…");
          return;
        }
        if (choice === "n" || choice === "timeout" || choice === "stale") {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        } else {
          setStatus("执行中…");
        }
      },
      onToolStart: () => {
        setStatus("处理中…");
      },
      onToolEnd: () => {
        if (!chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        }
      },
      onAssistantDone: () => {
        recallHighlightTurns = new Set();
        if (chat.model.cancelRequested) return;
        if (!chat.model.confirmPending) {
          setStatus("就绪");
        }
      },
      onTurnEnd: (_ok, finishReason) => {
        clearCancelSafety();
        setComposerEnabled(true);
        if (!chat.model.confirmPending) {
          setTurnEndStatus(finishReason);
        }
        // UX-017: notify when user switched away
        if (document.visibilityState !== "visible" && ("Notification" in window)) {
          if (Notification.permission === "granted") {
            new Notification("my-agent", { body: "已回复", silent: true });
          } else if (Notification.permission === "default") {
            Notification.requestPermission().then((perm) => {
              if (perm === "granted") {
                new Notification("my-agent", { body: "已回复", silent: true });
              }
            });
          }
        }
      },
      onCancelTimeout: () => {
        setComposerEnabled(true);
        setTurnEndStatus("cancelled");
      },
      onError: () => {
        setComposerEnabled(true);
        setStatus("错误");
      },
    },
  );

  // ---- DOM layout ----
  root.innerHTML = `
    <div class="unified-shell" data-perspective="default">
      <aside class="unified-sidebar hidden" id="unified-sidebar">
        <div class="sidebar-resize-handle" id="sidebar-resize-handle"></div>
        <div class="unified-sidebar-header">
          <div class="unified-sidebar-title" id="project-sidebar-title">项目</div>
          <div class="unified-sidebar-meta" id="project-sidebar-meta">未绑定项目</div>
          <div class="sidebar-progress-bar-wrap hidden" id="project-sidebar-progress">
            <div class="sidebar-progress-bar-fill" id="sidebar-progress-fill" style="width:0%"></div>
          </div>
        </div>
        <div class="sidebar-task-flow" id="sidebar-task-flow"></div>
        <div class="sidebar-change-banner hidden" id="sidebar-change-banner"></div>
        <div class="sidebar-icon-bar" id="sidebar-icon-bar">
          <button type="button" class="sidebar-icon-btn is-active" data-panel="tasks" title="任务"><span class="sidebar-icon">◎</span></button>
          <button type="button" class="sidebar-icon-btn" data-panel="docs" title="文档"><span class="sidebar-icon">📄</span></button>
          <button type="button" class="sidebar-icon-btn" data-panel="verify" title="验收" id="icon-btn-verify"><span class="sidebar-icon">✓</span></button>
          <button type="button" class="sidebar-icon-btn" data-panel="projects" title="我的项目" id="icon-btn-projects">
            <span class="sidebar-icon">☰</span>
            <span class="sidebar-icon-badge" id="project-count-badge">0</span>
          </button>
        </div>
        <div class="sidebar-overlay hidden" id="sidebar-overlay">
          <div class="sidebar-overlay-header">
            <button type="button" class="sidebar-overlay-back" id="overlay-back-btn">← 返回</button>
            <span class="sidebar-overlay-title" id="overlay-title"></span>
          </div>
          <div class="sidebar-overlay-body" id="overlay-body"></div>
        </div>
        <!-- compat: hidden elements for old event wiring -->
        <div id="project-picker-list" class="hidden"></div>
        <div id="project-picker-refresh" class="hidden"></div>
        <div class="hidden" id="project-switch-card"><div id="project-switch-title"></div><p id="project-switch-message"></p><div><button id="project-switch-confirm"></button><button id="project-switch-cancel"></button></div></div>
        <div class="hidden" id="project-plan-card"><div id="project-plan-title"></div><pre id="project-plan-preview"></pre><div><button id="project-plan-confirm"></button><button id="project-plan-edit"></button></div></div>
        <div class="hidden" id="project-sidebar-tabs"></div>
        <div class="hidden" id="project-panel-tasks"></div>
        <div class="hidden" id="project-panel-map"></div>
        <div class="hidden" id="project-verify-card"><div id="project-verify-title"></div><pre id="project-verify-command"></pre><button id="project-verify-run"></button><pre id="project-verify-result"></pre></div>
      </aside>
      <div class="unified-main">
        <header class="unified-topbar" id="unified-topbar"></header>
        <section class="unified-expand hidden" id="unified-expand"></section>
        <main class="unified-chat" id="unified-chat"></main>
        <div class="unified-status" id="unified-status"></div>
        <div class="unified-token-bar hidden" id="unified-token-bar"></div>
        <footer class="unified-composer" id="unified-composer">
          <button type="button" class="unified-btn" id="unified-stop" hidden>停止</button>
          <textarea class="unified-input" id="unified-input" rows="1" placeholder="输入消息，或拖入文件…"></textarea>
          <button type="button" class="unified-btn unified-btn-accent" id="unified-send">发送</button>
        </footer>
      </div>
      <div class="unified-confirm-glass hidden" id="unified-confirm-glass" role="dialog" aria-modal="true"></div>
    </div>
  `;

  // ---- element refs ----
  const shellEl = root.querySelector<HTMLElement>(".unified-shell")!;
  const sidebarEl = root.querySelector<HTMLElement>("#unified-sidebar")!;
  const topbarEl = root.querySelector<HTMLElement>("#unified-topbar")!;
  const expandEl = root.querySelector<HTMLElement>("#unified-expand")!;
  const chatEl = root.querySelector<HTMLElement>("#unified-chat")!;
  const statusEl = root.querySelector<HTMLElement>("#unified-status")!;
  const composer = root.querySelector<HTMLElement>("#unified-composer")!;
  const input = root.querySelector<HTMLTextAreaElement>("#unified-input")!;
  const stopBtn = root.querySelector<HTMLButtonElement>("#unified-stop")!;
  const sendBtn = root.querySelector<HTMLButtonElement>("#unified-send")!;
  const confirmGlass = root.querySelector<HTMLElement>("#unified-confirm-glass")!;
  const tokenBar = root.querySelector<HTMLElement>("#unified-token-bar")!;

  // project panel elements (only used in project perspective)
  const projectEls = setupProjectPanel(root);

  // ---- resize handle ----
  const resizeHandle = root.querySelector<HTMLElement>("#sidebar-resize-handle")!;
  let resizeDragging = false;

  function loadSidebarWidth(): number {
    try {
      const raw = localStorage.getItem("sidebar-width");
      if (raw) {
        const n = parseInt(raw, 10);
        if (n >= 200 && n <= window.innerWidth * 0.5) return n;
      }
    } catch { /* ignore */ }
    return 280;
  }

  function saveSidebarWidth(w: number): void {
    try { localStorage.setItem("sidebar-width", String(w)); } catch { /* ignore */ }
  }

  function applySidebarWidth(w: number): void {
    shellEl.style.setProperty("--sidebar-width", `${w}px`);
  }

  function clampSidebarWidth(w: number): number {
    return Math.max(200, Math.min(Math.round(w), Math.floor(window.innerWidth * 0.5)));
  }

  applySidebarWidth(loadSidebarWidth());

  resizeHandle.addEventListener("mousedown", (ev) => {
    ev.preventDefault();
    resizeDragging = true;
    resizeHandle.classList.add("is-dragging");
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (ev) => {
    if (!resizeDragging) return;
    const rect = shellEl.getBoundingClientRect();
    const w = clampSidebarWidth(ev.clientX - rect.left);
    applySidebarWidth(w);
  });

  document.addEventListener("mouseup", () => {
    if (!resizeDragging) return;
    resizeDragging = false;
    resizeHandle.classList.remove("is-dragging");
    document.body.style.cursor = "";
    document.body.style.userSelect = "";
    const w = parseInt(
      shellEl.style.getPropertyValue("--sidebar-width").replace("px", ""),
      10,
    );
    if (!isNaN(w)) saveSidebarWidth(w);
  });

  // ---- perspective-dependent placeholder ----
  function updatePlaceholder(): void {
    input.placeholder = perspective === "project"
      ? "输入消息，或拖入代码文件…"
      : "输入消息，或拖入文件…";
  }

  // ---- file drop + composer ----
  const fileDrop = mountFileDrop({
    composer,
    client,
    shell: shellId,
    canAccept: () => {
      if (chat.model.confirmPending) return false;
      if (perspective === "project" && !projectState.projectId) return false;
      return true;
    },
    onChange: () => composerWire.syncSendEnabled(),
    onNotice: (text) => setStatus(text),
  });

  const composerWire = wireComposerAttachments({
    input,
    sendBtn,
    client,
    chat,
    fileDrop,
    onStatus: (text) => setStatus(text),
    beforeSend: () => {
      topbarState.intentLabel = "";
      setStatus("发送中…");
    },
  });

  // ---- visual sync ----
  function syncWorkingVisual(): void {
    const working = chat.isWorking();
    shellEl.classList.toggle("is-working", working);
    stopBtn.hidden = !(working || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    setAgentBusy(working, perspective === "project" ? "project" : "grow");
  }

  function setStatus(text: string): void {
    statusText = text;
    if (text === "思考中…") {
      thinkingStarted = Date.now();
      if (thinkingTimer === null) {
        thinkingTimer = window.setInterval(() => {
          if (statusText === "思考中…" && thinkingStarted > 0) {
            const elapsed = Math.round((Date.now() - thinkingStarted) / 1000);
            statusEl.textContent = `思考中…（${elapsed}s）`;
          }
        }, 1000);
      }
    } else {
      if (thinkingTimer !== null) {
        window.clearInterval(thinkingTimer);
        thinkingTimer = null;
      }
      thinkingStarted = 0;
      statusEl.textContent = text;
    }
  }

  function setTurnEndStatus(finishReason: string): void {
    if (cancelledStatusTimer !== null) {
      window.clearTimeout(cancelledStatusTimer);
      cancelledStatusTimer = null;
    }
    const label = turnEndStatusText(finishReason);
    if (label === null) {
      setStatus("就绪");
      return;
    }
    setStatus(label);
    cancelledStatusTimer = window.setTimeout(() => {
      if (!chat.isWorking() && !chat.model.confirmPending) setStatus("就绪");
      cancelledStatusTimer = null;
    }, 2000);
  }

  function updateTokenBar(usage: number | undefined, limit: number | undefined): void {
    if (usage === undefined || limit === undefined || limit <= 0) {
      tokenBar.classList.add("hidden");
      return;
    }
    tokenBar.classList.remove("hidden");
    const ratio = usage / limit;
    let cls = "unified-token-bar";
    if (ratio >= 0.95) cls += " unified-token-red";
    else if (ratio >= 0.85) cls += " unified-token-yellow";
    const usageK = Math.round(usage / 1000);
    const limitK = Math.round(limit / 1000);
    tokenBar.className = cls;
    tokenBar.textContent = `${usageK}k / ${limitK}k tokens`;
  }

  function setComposerEnabled(enabled: boolean): void {
    input.disabled = !enabled;
    composer.classList.toggle("disabled", !enabled);
    if (enabled) {
      composerWire.syncSendEnabled();
    } else {
      sendBtn.disabled = true;
    }
  }

  // ---- new session ----
  function handleNewSession(): void {
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再开新会话");
      return;
    }
    try {
      client.sendCommand("新会话");
      setStatus("新会话…");
    } catch (err) {
      setStatus(`新会话失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // ---- sessions dropdown ----
  function handleOpenSessions(): void {
    try {
      client.listSessions();
    } catch (err) {
      setStatus(`获取会话列表失败：${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    sessionsOpen = !sessionsOpen;
    if (!sessionsOpen) {
      expandEl.classList.add("hidden");
      expandEl.innerHTML = "";
      return;
    }
    renderSessionsDropdown();
  }

  function renderSessionsDropdown(): void {
    let html = `<div class="unified-expand-title">最近会话 <button type="button" class="unified-btn" id="unified-sessions-close">关闭</button></div>`;
    if (!sessionsDropdown.length) {
      html += `<p class="text-muted">加载中…</p>`;
    } else {
      for (const s of sessionsDropdown) {
        const isCurrent = s.session_id === (chat.model as any).sessionId;
        html += `<div class="unified-expand-item ${isCurrent ? "is-current" : ""}">
          <span class="unified-expand-item-title">${escapeHtml(s.title)}</span>
          <span class="unified-expand-item-meta">${escapeHtml(s.session_id)}</span>
          <button type="button" class="unified-btn" data-open-session="${escapeHtml(s.session_id)}" ${isCurrent ? "disabled" : ""}>${isCurrent ? "当前" : "打开"}</button>
        </div>`;
      }
    }
    expandEl.innerHTML = html;
    expandEl.classList.remove("hidden");

    expandEl.querySelector("#unified-sessions-close")?.addEventListener("click", () => {
      sessionsOpen = false;
      expandEl.classList.add("hidden");
      expandEl.innerHTML = "";
    });
    expandEl.querySelectorAll<HTMLButtonElement>("[data-open-session]").forEach((btn) => {
      btn.addEventListener("click", () => {
        const sid = btn.dataset.openSession;
        if (!sid) return;
        try {
          client.openSession(sid);
          sessionsOpen = false;
          expandEl.classList.add("hidden");
          expandEl.innerHTML = "";
          setStatus(`打开会话 ${sid}…`);
        } catch (err) {
          setStatus(`打开失败：${err instanceof Error ? err.message : String(err)}`);
        }
      });
    });
  }

  // ---- proposals panel helpers ----
  function syncProposalsState(): void {
    proposalsState.proposals = topbarState.proposals;
    // proposalsState and topbarState share proposals array;
    // sync is called before each renderProposalsPanel()
  }

  function openProposals(): void {
    proposalsState.expandOpen = true;
    syncProposalsState();
    renderProposalsPanel();
  }

  function renderProposalsPanel(): void {
    syncProposalsState();
    renderProposals(expandEl, proposalsState, client);

    // Listen for custom events dispatched by proposals.ts buttons
    expandEl.addEventListener("proposals:next", () => {
      proposalsState.proposalIndex = nextProposalIndex(proposalsState);
      renderProposalsPanel();
    }, { once: true });

    expandEl.addEventListener("proposals:close", () => {
      proposalsState.expandOpen = false;
      renderProposalsPanel();
    }, { once: true });
  }

  function handleProposalsEvent(items: ProposalItem[]): void {
    proposalsState.proposals = items;
    proposalsState.proposalIndex = 0;
    if (!items.length) proposalsState.expandOpen = false;
    // also sync topbarState for renderTopbar
    topbarState.proposals = items;
    syncProposalsState();
    renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
    renderProposalsPanel();
  }

  // ---- project panel helpers ----
  const projectCallbacks: ProjectPanelCallbacks = {
    onProjectSwitch: (projectId: string) => {
      const target = projectId.trim();
      if (!target || target === projectState.projectId) return;
      if (chat.isWorking()) {
        setStatus("助手执行中，请稍后再切换项目");
        return;
      }
      projectState.switchInProgress = true;
      projectState.pendingPickerId = target;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      try {
        client.switchProject(target);
      } catch (err) {
        projectState.switchInProgress = false;
        projectState.pendingPickerId = "";
        setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
    },
    onProjectSwitchConfirm: () => {
      if (!projectState.switchOverlay) return;
      projectState.switchInProgress = true;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      try {
        client.switchProject(projectState.switchOverlay.projectId, {
          confirm: true,
          requestId: projectState.switchOverlay.requestId,
        });
      } catch (err) {
        projectState.switchInProgress = false;
        setStatus(`切换失败：${err instanceof Error ? err.message : String(err)}`);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
    },
    onProjectSwitchCancel: () => {
      projectState.switchOverlay = null;
      projectState.switchInProgress = false;
      projectState.pendingPickerId = "";
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
    },
    onPlanConfirm: async () => {
      const overlay = projectState.planOverlay;
      if (!overlay) {
        try {
          client.sendCommand("项目 确认");
          setStatus("正在确认计划…");
        } catch (err) {
          setStatus(`计划确认失败：${err instanceof Error ? err.message : String(err)}`);
        }
        return;
      }
      try {
        client.sendPlanResponse(overlay.requestId, "confirm");
        setStatus("正在确认计划…");
      } catch (err) {
        setStatus(`计划确认失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onPlanEdit: async () => {
      const overlay = projectState.planOverlay;
      if (!overlay) return;
      try {
        client.sendPlanResponse(overlay.requestId, "edit");
        projectState.planOverlay = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        setStatus("继续修改计划…");
      } catch (err) {
        setStatus(`计划确认失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onRefreshProjects: () => {
      try {
        client.listProjects();
        setStatus("刷新项目列表…");
      } catch (err) {
        setStatus(`刷新失败：${err instanceof Error ? err.message : String(err)}`);
      }
    },
    onRunVerify: () => {
      if (!projectState.canVerify || projectState.verifyRunning) return;
      projectState.verifyRunning = true;
      projectState.verifyResult = null;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      setStatus("运行验收…");
      try {
        client.runProjectVerify();
      } catch (err) {
        projectState.verifyRunning = false;
        setStatus(`验收失败：${err instanceof Error ? err.message : String(err)}`);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
    },
  };

  // ---- chat rendering ----
  function isRecentTurnBlock(turnIndex: number): boolean {
    return turnIndex >= chat.currentTurnIndex() - (FOCUS_TURNS - 1);
  }

  function renderBlock(block: ChatBlock): string {
    if (block.kind === "user") {
      let cls = "unified-turn unified-turn-user";
      if (perspective === "night") {
        if (!isRecentTurnBlock(block.turnIndex)) cls += " unified-turn-dim";
        if (recallHighlightTurns.has(block.turnIndex)) cls += " unified-turn-recall";
      }
      return `<article class="${cls}" data-turn-index="${block.turnIndex}">
        <div class="unified-turn-label">你</div>
        <div class="unified-turn-body">${formatUserMessageHtml(block.text)}</div>
      </article>`;
    }
    if (block.kind === "assistant" || block.kind === "assistant-streaming") {
      let cls = "unified-turn unified-turn-assistant";
      if (perspective === "night") {
        if (!isRecentTurnBlock(block.turnIndex)) cls += " unified-turn-dim";
        if (recallHighlightTurns.has(block.turnIndex)) cls += " unified-turn-recall";
        if (block.kind === "assistant-streaming") cls += " unified-turn-streaming";
      }
      return `<article class="${cls}" data-turn-index="${block.turnIndex}">
        <div class="unified-turn-label">助手</div>
        <div class="unified-turn-body unified-markdown">${renderMarkdown(block.text)}</div>
      </article>`;
    }
    if (block.kind === "notice") {
      return `<p class="text-muted">${escapeHtml(block.text)}</p>`;
    }
    // night perspective: skip process and confirm blocks (use overlay instead)
    if (block.kind === "process") {
      if (perspective === "night") return "";
      const lines = block.lines.map((l) => `<div class="unified-process-line">${escapeHtml(l)}</div>`).join("");
      const reasoning = block.reasoning
        ? `<div class="unified-process-reasoning">${escapeHtml(block.reasoning)}</div>`
        : "";
      const title = block.reasoning ? "思考中…" : "过程";
      const toggle = block.collapsed ? "展开" : "收起";
      return `
        <div class="unified-process ${block.collapsed ? "collapsed" : ""}" data-turn="${block.turnKey}">
          <div class="unified-process-header">
            <span>${title}</span>
            <button type="button" class="unified-btn" data-process-toggle="${block.turnKey}">${toggle}</button>
          </div>
          <div class="unified-process-lines">${reasoning}${lines}</div>
        </div>`;
    }
    if (block.kind === "confirm") {
      if (perspective === "night") return ""; // shown in overlay
      const disabled = block.resolved ? "disabled" : "";
      const resolved = block.resolved
        ? `<div class="text-muted">${escapeHtml(block.resolved)}</div>`
        : `
        <div class="unified-expand-actions">
          <button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${block.requestId}" ${disabled}>同意</button>
          <button type="button" class="unified-btn unified-btn-danger" data-confirm="n" data-id="${block.requestId}" ${disabled}>拒绝</button>
          ${
            block.allowApproveAll
              ? `<button type="button" class="unified-btn" data-confirm="a" data-id="${block.requestId}" ${disabled}>本会话 workspace 均允许</button>`
              : ""
          }
        </div>`;
      return `
        <div class="unified-surface unified-confirm ${block.resolved ? "resolved" : ""}">
          <div class="unified-expand-title">工具确认</div>
          <pre class="unified-confirm-preview">${escapeHtml(block.preview)}</pre>
          ${resolved}
        </div>`;
    }
    return "";
  }

  function setupFocusObserver(): void {
    focusObserver?.disconnect();
    if (perspective !== "night") return;
    focusObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const el = entry.target as HTMLElement;
          if (entry.isIntersecting) {
            el.classList.add("in-view");
          }
        }
      },
      { root: chatEl, threshold: 0.15 },
    );
    chatEl.querySelectorAll<HTMLElement>("[data-turn-index]").forEach((el) => {
      focusObserver?.observe(el);
    });
  }

  function scrollToRecallTurns(): void {
    const first = chatEl.querySelector<HTMLElement>(".unified-turn-recall");
    first?.scrollIntoView({ behavior: motionQuery.matches ? "auto" : "smooth", block: "center" });
  }

  function bounceLatestUserTurn(): void {
    if (motionQuery.matches) return;
    const turns = chatEl.querySelectorAll<HTMLElement>(".unified-turn-user");
    const last = turns[turns.length - 1];
    if (!last) return;
    last.classList.add("unified-turn-enter");
    last.addEventListener("animationend", () => {
      last.classList.remove("unified-turn-enter");
    }, { once: true });
  }

  function blockPrint(block: ChatBlock): string {
    switch (block.kind) {
      case "user":
        return `U${block.turnIndex}:${block.text.length}`;
      case "assistant":
        return `A${block.turnIndex}:${block.text.length}`;
      case "assistant-streaming":
        return `AS${block.turnIndex}:${block.turnKey}:${block.text.length}`;
      case "notice":
        return `N:${block.text.length}`;
      case "process":
        return `P${block.turnKey}:${block.lines.length}:${block.reasoning.length}:${block.collapsed ? 1 : 0}`;
      case "confirm":
        return `C${block.requestId}:${block.resolved ?? "_"}`;
    }
  }

  function bindNewProcessToggles(container: HTMLElement): void {
    container.querySelectorAll<HTMLButtonElement>("[data-process-toggle]:not([data-process-bound])").forEach((btn) => {
      btn.dataset.processBound = "1";
      btn.addEventListener("click", () => {
        const turnKey = btn.dataset.processToggle;
        if (turnKey) chat.toggleProcessCollapsed(turnKey);
      });
    });
  }

  function scrollToBottomIfNear(): void {
    const distFromBottom = chatEl.scrollHeight - chatEl.scrollTop - chatEl.clientHeight;
    if (distFromBottom < 50) {
      chatEl.scrollTop = chatEl.scrollHeight;
    }
  }

  function doRender(): void {
    const curPrints = chat.model.blocks.map(blockPrint);
    // A3-2: pure tail-append — insertAdjacentHTML instead of full innerHTML
    if (renderedPrints.length > 0 && curPrints.length > renderedPrints.length) {
      const isTailAppend = renderedPrints.every(
        (fp, i) => fp === curPrints[i],
      );
      if (isTailAppend) {
        const newBlocks = chat.model.blocks.slice(renderedPrints.length);
        const newHtml = newBlocks.map(renderBlock).join("");
        chatEl.insertAdjacentHTML("beforeend", newHtml);
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-3: same block count, only tail changed — replace just those elements
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length) {
      let firstDiff = -1;
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) { firstDiff = i; break; }
      }
      // Only use incremental path when changes are at the tail (last 2 blocks)
      // and DOM child count matches (falls back if night-perspective empty blocks)
      if (firstDiff >= 0 && firstDiff >= curPrints.length - 2 && chatEl.children.length === curPrints.length) {
        const children = chatEl.children;
        for (let i = firstDiff; i < curPrints.length; i++) {
          children[i].outerHTML = renderBlock(chat.model.blocks[i]);
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-4: tail-shrink — blocks removed from end (assistant.done with empty text)
    if (renderedPrints.length > 0 && curPrints.length < renderedPrints.length && chatEl.children.length === renderedPrints.length) {
      let firstDiff = -1;
      const prefixLen = Math.min(curPrints.length, renderedPrints.length);
      for (let i = 0; i < prefixLen; i++) {
        if (curPrints[i] !== renderedPrints[i]) { firstDiff = i; break; }
      }
      // All unchanged blocks match, and changes are near the tail
      const tailLen = renderedPrints.length - curPrints.length;
      if (firstDiff === -1 || firstDiff >= curPrints.length - 2) {
        // Remove extra trailing DOM children
        for (let r = 0; r < tailLen; r++) {
          chatEl.lastElementChild?.remove();
        }
        // Replace remaining changed tail elements
        if (firstDiff >= 0) {
          const children = chatEl.children;
          for (let i = firstDiff; i < curPrints.length; i++) {
            if (i < children.length) {
              children[i].outerHTML = renderBlock(chat.model.blocks[i]);
            }
          }
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-5: confirm resolved only — replace just the confirm elements at changed positions
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length && chatEl.children.length === curPrints.length) {
      const diffs: number[] = [];
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) diffs.push(i);
      }
      const allAreConfirm = diffs.length > 0 && diffs.every(i =>
        curPrints[i].startsWith("C") && renderedPrints[i].startsWith("C")
      );
      if (allAreConfirm) {
        const children = chatEl.children;
        for (const i of diffs) {
          if (i < children.length) {
            children[i].outerHTML = renderBlock(chat.model.blocks[i]);
          }
        }
        bindNewProcessToggles(chatEl);
        setupFocusObserver();
        scrollToBottomIfNear();
        renderedPrints = curPrints;
        return;
      }
    }

    // A3-6: process collapsed toggle only — flip CSS class, skip full render
    if (renderedPrints.length > 0 && curPrints.length === renderedPrints.length && chatEl.children.length === curPrints.length) {
      const diffs: number[] = [];
      let collapseToggle = "";
      for (let i = 0; i < curPrints.length; i++) {
        if (curPrints[i] !== renderedPrints[i]) {
          diffs.push(i);
          // Detect process collapse toggle: P{key}:...:0 ↔ P{key}:...:1
          const curP = curPrints[i];
          const prevP = renderedPrints[i];
          if (curP.startsWith("P") && prevP.startsWith("P") &&
              curP.length === prevP.length &&
              curP.slice(0, -1) === prevP.slice(0, -1)) {
            collapseToggle = curP.split(":")[0].slice(1); // extract turnKey
          }
        }
      }
      if (collapseToggle && diffs.length === 1) {
        const el = chatEl.querySelector(`.unified-process[data-turn="${collapseToggle}"]`);
        if (el) {
          el.classList.toggle("collapsed");
          const btn = el.querySelector<HTMLButtonElement>("[data-process-toggle]");
          if (btn) {
            btn.textContent = el.classList.contains("collapsed") ? "展开" : "收起";
          }
          renderedPrints = curPrints;
          return;
        }
      }
    }

    // full render (initial, or escape hatch for unhandled change patterns)
    chatEl.innerHTML = chat.model.blocks.map(renderBlock).join("");
    bindNewProcessToggles(chatEl);
    setupFocusObserver();
    scrollToBottomIfNear();
    renderedPrints = curPrints;
  }

  const RENDER_THROTTLE_MS = 100;

  function renderChat(): void {
    if (renderThrottleTimer !== null) return;
    renderThrottleTimer = window.setTimeout(() => {
      renderThrottleTimer = null;
      doRender();
    }, RENDER_THROTTLE_MS);
    doRender();
  }

  function renderConfirmGlass(): void {
    if (perspective !== "night") {
      confirmGlass.classList.add("hidden");
      confirmGlass.innerHTML = "";
      return;
    }

    const confirm = chat.model.confirmOverlay;
    if (!confirm) {
      confirmGlass.classList.add("hidden");
      confirmGlass.innerHTML = "";
      return;
    }

    confirmGlass.classList.remove("hidden");
    const disabled = confirm.resolved ? "disabled" : "";
    const resolved = confirm.resolved
      ? `<div class="unified-confirm-resolved">${escapeHtml(confirm.resolved)}</div>`
      : `
        <div class="unified-confirm-glass-actions">
          <button type="button" class="unified-btn unified-btn-accent" data-confirm="y" data-id="${confirm.requestId}" ${disabled}>同意</button>
          <button type="button" class="unified-btn unified-btn-danger" data-confirm="n" data-id="${confirm.requestId}" ${disabled}>拒绝</button>
          ${
            confirm.allowApproveAll
              ? `<button type="button" class="unified-btn" data-confirm="a" data-id="${confirm.requestId}" ${disabled}>本会话均允许</button>`
              : ""
          }
        </div>`;

    confirmGlass.innerHTML = `
      <div class="unified-confirm-glass-card ${confirm.resolved ? "resolved" : ""}">
        <div class="unified-confirm-glass-title">工具确认</div>
        <pre class="unified-confirm-glass-preview">${escapeHtml(confirm.preview)}</pre>
        ${resolved}
      </div>
    `;
  }

  // ---- input events ----
  sendBtn.addEventListener("click", () => {
    composerWire.sendCurrentMessage();
    if (perspective === "night") bounceLatestUserTurn();
  });
  stopBtn.addEventListener("click", () => {
    if (!chat.requestCancel()) return;
    setComposerEnabled(false);
    setStatus("正在停止…");
    clearCancelSafety();
    cancelSafetyTimer = window.setTimeout(() => {
      if (chat.model.cancelRequested) {
        chat.model.cancelRequested = false;
        chat.model.confirmPending = false;
        setComposerEnabled(true);
        setStatus("就绪");
        syncWorkingVisual();
      }
      cancelSafetyTimer = null;
    }, 3000);
    try {
      client.sendTurnCancel();
    } catch (err) {
      clearCancelSafety();
      chat.model.cancelRequested = false;
      setComposerEnabled(!chat.model.confirmPending);
      setStatus(`停止发送失败：${err instanceof Error ? err.message : String(err)}`);
      syncWorkingVisual();
    }
  });
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.shiftKey) {
      ev.preventDefault();
      composerWire.sendCurrentMessage();
      if (perspective === "night") bounceLatestUserTurn();
    }
  });

  // auto-expand textarea on input
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 200) + "px";
  });

  // Escape key to stop anywhere in the shell
  document.addEventListener("keydown", (ev) => {
    if (destroyed) return;
    if (ev.key !== "Escape") return;
    if (!(chat.isWorking() || chat.model.confirmPending)) return;
    if (!chat.requestCancel()) return;
    setComposerEnabled(false);
    setStatus("正在停止…");
    clearCancelSafety();
    cancelSafetyTimer = window.setTimeout(() => {
      if (chat.model.cancelRequested) {
        chat.model.cancelRequested = false;
        chat.model.confirmPending = false;
        setComposerEnabled(true);
        setStatus("就绪");
        syncWorkingVisual();
      }
      cancelSafetyTimer = null;
    }, 3000);
    try {
      client.sendTurnCancel();
    } catch (err) {
      clearCancelSafety();
      chat.model.cancelRequested = false;
      setComposerEnabled(!chat.model.confirmPending);
      setStatus(`停止发送失败：${err instanceof Error ? err.message : String(err)}`);
      syncWorkingVisual();
    }
  });

  // ---- confirm keyboard shortcuts ----
  document.addEventListener("keydown", (ev) => {
    if (destroyed) return;
    if (!chat.model.confirmPending) return;
    // don't steal keys from the input field
    if (document.activeElement === input) return;
    const key = ev.key.toLowerCase();
    if (!["y", "n", "a"].includes(key)) return;

    const overlay = chat.model.confirmOverlay;
    let requestId: string | null = null;
    let allowApproveAll = false;
    if (overlay) {
      requestId = overlay.requestId;
      allowApproveAll = overlay.allowApproveAll;
    } else {
      // find most recent unresolved inline confirm block
      const blocks = chat.model.blocks;
      for (let i = blocks.length - 1; i >= 0; i--) {
        const b = blocks[i];
        if (b.kind === "confirm" && !b.resolved) {
          requestId = b.requestId;
          allowApproveAll = b.allowApproveAll;
          break;
        }
      }
    }
    if (!requestId) return;
    if (key === "a" && !allowApproveAll) return;

    ev.preventDefault();
    submitConfirmById(requestId, key as "y" | "n" | "a");
  });

  // ---- confirm submission helper (shared between click and keyboard) ----
  function submitConfirmById(id: string, choice: "y" | "n" | "a"): void {
    if (!chat.submitConfirm(id, choice)) {
      setStatus("请点最新一张工具确认卡");
      return;
    }
    try {
      client.sendConfirm(id, choice);
      setStatus(choice === "n" ? "已提交拒绝…" : "确认中…");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`确认发送失败：${message}`);
      chat.model.confirmSubmitting = false;
      const block = chat.model.blocks.find((b) => b.kind === "confirm" && b.requestId === id);
      if (block?.kind === "confirm") {
        block.resolved = undefined;
      }
      if (chat.model.confirmOverlay?.requestId === id) {
        chat.model.confirmOverlay = { ...chat.model.confirmOverlay, resolved: undefined };
      }
      renderChat();
      renderConfirmGlass();
      syncWorkingVisual();
    }
  }

  // ---- confirm clicks ----
  chatEl.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement | null;
    const btn = target?.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!id || !choice) return;
    submitConfirmById(id, choice);
  });

  // ---- confirm glass clicks (night perspective) ----
  confirmGlass.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement | null;
    const btn = target?.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const id = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!id || !choice) return;
    submitConfirmById(id, choice);
    syncWorkingVisual();
  });

  // ---- project sidebar events ----

  // Icon bar: switch overlay panel
  projectEls.iconBar.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".sidebar-icon-btn");
    if (!btn?.dataset.panel) return;
    const panel = btn.dataset.panel;
    if (panel === "tasks") {
      projectState.overlayPanel = null;
      projectState.currentDocPath = "";
      projectState.currentDocContent = "";
    } else {
      projectState.switchConfirmTarget = null;
      projectState.projectSearchQuery = "";
      projectState.currentDocPath = "";
      projectState.currentDocContent = "";
      projectState.overlayPanel = panel as OverlayPanel;
      // Auto-fetch docs list when entering docs panel
      if (panel === "docs") {
        try { client.listDocs(); } catch { /* ignore */ }
      }
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  });

  // Overlay back button
  projectEls.overlayBackBtn.addEventListener("click", () => {
    projectState.overlayPanel = null;
    projectState.switchConfirmTarget = null;
    projectState.currentDocPath = "";
    projectState.currentDocContent = "";
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  });

  // Task flow: quick-add input
  projectEls.taskFlow.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      const input = (ev.target as HTMLElement).closest<HTMLInputElement>("#sidebar-quick-add-input");
      if (input) {
        ev.preventDefault();
        const desc = input.value.trim();
        if (desc) {
          projectState.quickAddText = "";

          // Optimistic: add task to first phase
          const phase = projectState.taskPhases.find((p) => p.tasks.length > 0);
          if (phase) {
            const newTask: TaskItem = {
              line: -1,  // placeholder; server will assign real line
              text: desc,
              done: false,
              status: "new",
            };
            phase.tasks.push(newTask);
          }

          renderProjectSidebar(projectEls, projectState, projectCallbacks);

          try {
            client.addTask(desc);
          } catch (err) {
            setStatus(`添加任务失败：${err instanceof Error ? err.message : String(err)}`);
            client.refreshProject(); // fallback reload from server
          }
        }
      }
    }
  });

  projectEls.taskFlow.addEventListener("input", (ev) => {
    const input = (ev.target as HTMLElement).closest<HTMLInputElement>("#sidebar-quick-add-input");
    if (input) {
      projectState.quickAddText = input.value;
    }
  });

  // Overlay: project search + list + switch confirm + verify
  projectEls.overlayBody.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;

    // Project item click
    const projectBtn = target.closest<HTMLButtonElement>(".overlay-project-item");
    if (projectBtn?.dataset.projectId && !projectBtn.disabled) {
      const pid = projectBtn.dataset.projectId;
      const item = projectState.projects.find((p) => p.id === pid);
      if (item && item.sessionId && !item.isCurrent) {
        // needs confirm: show inline switch confirm
        projectState.switchConfirmTarget = item;
      } else {
        // no session or current: switch directly
        projectCallbacks.onProjectSwitch(pid);
        projectState.overlayPanel = null;
      }
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      return;
    }

    // Switch confirm actions (inside overlay body)
    if (target.closest("#overlay-switch-confirm")) {
      const confirmBtn = target.closest<HTMLButtonElement>("#overlay-switch-confirm-btn");
      const cancelBtn = target.closest<HTMLButtonElement>("#overlay-switch-cancel-btn");
      if (confirmBtn && projectState.switchConfirmTarget) {
        projectCallbacks.onProjectSwitch(projectState.switchConfirmTarget.id);
        projectState.overlayPanel = null;
        projectState.switchConfirmTarget = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      } else if (cancelBtn) {
        projectState.switchConfirmTarget = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
      return;
    }

    // Document item click
    const docBtn = target.closest<HTMLButtonElement>(".overlay-doc-item");
    if (docBtn?.dataset.docPath) {
      projectState.currentDocPath = docBtn.dataset.docPath;
      projectState.currentDocContent = "";
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      try { client.readDoc(projectState.currentDocPath); } catch { /* ignore */ }
      return;
    }

    // New doc button
    if (target.closest("#overlay-new-doc-btn")) {
      const name = projectState.newDocName.trim();
      if (name) {
        try { client.createDoc(name); } catch { /* ignore */ }
        projectState.newDocName = "";
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
      }
      return;
    }

    // Verify run button
    if (target.closest("#overlay-verify-run")) {
      projectCallbacks.onRunVerify();
      return;
    }
  });

  // Overlay: search input + new-doc input
  projectEls.overlayBody.addEventListener("input", (ev) => {
    const input = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-project-search");
    if (input) {
      projectState.projectSearchQuery = input.value;
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      return;
    }
    const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input");
    if (docInput) {
      projectState.newDocName = docInput.value;
      return;
    }
  });

  // Overlay: new-doc input Enter key
  projectEls.overlayBody.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      const docInput = (ev.target as HTMLElement).closest<HTMLInputElement>("#overlay-new-doc-input");
      if (docInput) {
        const name = projectState.newDocName.trim();
        if (name) {
          try { client.createDoc(name); } catch { /* ignore */ }
          projectState.newDocName = "";
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
      }
    }
  });

  // Change banner actions
  projectEls.changeBanner.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement;
    const btn = target.closest<HTMLButtonElement>("[data-action]");
    if (!btn?.dataset.action) return;
    const action = btn.dataset.action;
    switch (action) {
      case "confirm-plan":
        void projectCallbacks.onPlanConfirm();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;
      case "confirm-changes":
        try { client.planConfirmChanges(); } catch { /* ignore */ }
        break;
      case "edit-plan":
        void projectCallbacks.onPlanEdit();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;
      case "toggle-highlight":
        projectState.highlightChanges = !projectState.highlightChanges;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.highlightChanges) {
          requestAnimationFrame(() => {
            const first = projectEls.taskFlow.querySelector<HTMLElement>(".is-highlighted");
            first?.scrollIntoView({ behavior: "smooth", block: "center" });
          });
        }
        return;
      case "collapse-banner":
        projectState.planBannerCollapsed = true;
        projectState.highlightChanges = false;
        break;
      case "detect-switch":
        {
          const pid = btn.dataset.projectId;
          if (pid) {
            projectState.detectedProject = null;
            projectCallbacks.onProjectSwitch(pid);
          }
        }
        return;
      case "detect-dismiss":
        projectState.detectedProject = null;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "undo-last":
        projectState.undoDescription = "";
        if (projectState.undoTimerId) { window.clearTimeout(projectState.undoTimerId); projectState.undoTimerId = null; }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        try { client.undoLastPlanOp(); } catch { /* ignore */ }
        return;
      case "dismiss-warnings":
        projectState.planWarnings = [];
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        return;
      case "expand-banner":
        projectState.planBannerCollapsed = false;
        break;
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);
  });

  // Task checkbox click — optimistic update + WS toggle
  projectEls.taskFlow.addEventListener("click", (ev) => {
    const cb = (ev.target as HTMLElement).closest<HTMLInputElement>(".task-checkbox");
    if (!cb?.dataset.line) return;
    const line = parseInt(cb.dataset.line, 10);
    if (isNaN(line)) return;
    const done = cb.checked;

    // Capture pre-toggle state for rollback
    const prevPhases: Array<{ title: string; tasks: Array<{ line: number; done: boolean; status: string }> }> = [];
    for (const phase of projectState.taskPhases) {
      prevPhases.push({
        title: phase.title,
        tasks: phase.tasks.map((t) => ({ line: t.line, done: t.done, status: t.status })),
      });
    }

    // Optimistic local update
    for (const phase of projectState.taskPhases) {
      for (const task of phase.tasks) {
        if (task.line === line) {
          task.done = done;
          task.status = done ? "done" : "pending";
        }
      }
    }
    // reassign current task
    let found = false;
    for (const phase of projectState.taskPhases) {
      for (const task of phase.tasks) {
        if (!task.done && !found) { task.status = "current"; found = true; }
        else if (!task.done) { task.status = "pending"; }
        else { task.status = "done"; }
      }
    }
    renderProjectSidebar(projectEls, projectState, projectCallbacks);

    // Send WS
    try {
      client.toggleTask(line, done);
    } catch (err) {
      // Rollback on send failure
      for (const phase of projectState.taskPhases) {
        for (const task of phase.tasks) {
          const prev = prevPhases.flatMap((p) => p.tasks).find((t) => t.line === task.line);
          if (prev) { task.done = prev.done; task.status = prev.status as TaskItem["status"]; }
        }
      }
      renderProjectSidebar(projectEls, projectState, projectCallbacks);
      setStatus(`任务更新失败：${err instanceof Error ? err.message : String(err)}`);
    }
  });

  // ---- context menu ----
  let contextMenuEl: HTMLElement | null = null;
  let contextMenuTask: TaskItem | null = null;

  function destroyContextMenu(): void {
    if (contextMenuEl) {
      contextMenuEl.remove();
      contextMenuEl = null;
      contextMenuTask = null;
    }
  }

  function showContextMenu(task: TaskItem, x: number, y: number): void {
    destroyContextMenu();

    const menu = document.createElement("div");
    menu.className = "context-menu";
    menu.style.left = `${x}px`;
    menu.style.top = `${y}px`;

    const items: Array<{ label: string; action: string; danger?: boolean; disabled?: boolean; shortcut?: string }> = [
      { label: task.done ? "标记未完成" : "标记完成", action: "toggle", shortcut: "点击 checkbox" },
      { label: "上移", action: "move-up", disabled: !canMoveTaskUp(task) },
      { label: "下移", action: "move-down", disabled: !canMoveTaskDown(task) },
      null as unknown as { label: string; action: string }, // separator
      { label: "跳过（暂缓）", action: "skip" },
      { label: "拆分…", action: "split", shortcut: "Plan Agent" },
      null as unknown as { label: string; action: string }, // separator
      { label: "删除", action: "delete", danger: true },
    ];

    for (const item of items) {
      if (item === null) {
        const sep = document.createElement("div");
        sep.className = "context-menu-sep";
        menu.appendChild(sep);
        continue;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `context-menu-item${item.danger ? " is-danger" : ""}`;
      btn.textContent = item.label;
      btn.dataset.action = item.action;
      if (item.disabled) btn.disabled = true;
      if (item.shortcut) {
        const lbl = document.createElement("span");
        lbl.className = "context-menu-label";
        lbl.textContent = item.shortcut;
        btn.appendChild(lbl);
      }
      menu.appendChild(btn);
    }

    document.body.appendChild(menu);
    contextMenuEl = menu;
    contextMenuTask = task;

    // Click handler on menu
    menu.addEventListener("click", (ev) => {
      const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".context-menu-item");
      if (!btn?.dataset.action || btn.disabled) return;
      const action = btn.dataset.action;
      handleContextMenuAction(action);
    });

    // Close on outside click
    setTimeout(() => {
      document.addEventListener("click", destroyContextMenu, { once: true });
      document.addEventListener("contextmenu", destroyContextMenu, { once: true });
    }, 0);
  }

  function findTaskByLine(line: number): { phaseIdx: number; taskIdx: number } | null {
    for (let pi = 0; pi < projectState.taskPhases.length; pi++) {
      const tasks = projectState.taskPhases[pi].tasks;
      for (let ti = 0; ti < tasks.length; ti++) {
        if (tasks[ti].line === line) return { phaseIdx: pi, taskIdx: ti };
      }
    }
    return null;
  }

  function canMoveTaskUp(task: TaskItem): boolean {
    const pos = findTaskByLine(task.line);
    if (!pos) return false;
    return pos.taskIdx > 0;
  }

  function canMoveTaskDown(task: TaskItem): boolean {
    const pos = findTaskByLine(task.line);
    if (!pos) return false;
    return pos.taskIdx < projectState.taskPhases[pos.phaseIdx].tasks.length - 1;
  }

  function handleContextMenuAction(action: string): void {
    destroyContextMenu();
    if (!contextMenuTask) return;
    const task = contextMenuTask;

    try {
      switch (action) {
        case "toggle":
          client.planToggleTask(task.line, !task.done);
          break;
        case "move-up":
          client.planReorderTask(task.line, "up");
          break;
        case "move-down":
          client.planReorderTask(task.line, "down");
          break;
        case "skip":
          client.planSkipTask(task.line);
          break;
        case "delete":
          client.planDropTask(task.line);
          break;
        case "split":
          setStatus("项目管理器分析中…");
          try { client.splitPlanTask(task.line); } catch (err) {
            setStatus(`拆分失败：${err instanceof Error ? err.message : String(err)}`);
          }
          return;
      }
    } catch (err) {
      setStatus(`操作失败：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  // contextmenu on task flow
  projectEls.taskFlow.addEventListener("contextmenu", (ev) => {
    const taskEl = (ev.target as HTMLElement).closest<HTMLElement>(".task-item");
    if (!taskEl?.dataset.line) return;
    ev.preventDefault();
    const line = parseInt(taskEl.dataset.line, 10);
    if (isNaN(line)) return;
    // find task in state
    for (const phase of projectState.taskPhases) {
      const t = phase.tasks.find((tk) => tk.line === line);
      if (t) { showContextMenu(t, ev.clientX, ev.clientY); return; }
    }
  });

  // compat: keep old event wiring working for elements hidden in DOM
  projectEls.pickerRefreshBtn.addEventListener("click", () => projectCallbacks.onRefreshProjects());
  projectEls.switchConfirmBtn.addEventListener("click", () => projectCallbacks.onProjectSwitchConfirm());
  projectEls.switchCancelBtn.addEventListener("click", () => projectCallbacks.onProjectSwitchCancel());
  projectEls.planConfirmBtn.addEventListener("click", () => { void projectCallbacks.onPlanConfirm(); });
  projectEls.planEditBtn.addEventListener("click", () => { void projectCallbacks.onPlanEdit(); });
  projectEls.verifyRunBtn.addEventListener("click", () => projectCallbacks.onRunVerify());

  // ---- server events ----
  const off = client.onEvent((event: ServerEvent) => {
    switch (event.type) {
      case "session.banner":
        chat.handleEvent(event);
        if (event.project_id) {
          projectState.projectId = event.project_id;
          projectState.planStatus = event.project_plan_status ?? projectState.planStatus;
          projectState.tasksDone = event.project_tasks_done ?? projectState.tasksDone;
          projectState.tasksTotal = event.project_tasks_total ?? projectState.tasksTotal;
          const plan = event.project_plan_label ?? "计划待确认";
          topbarState.projectLabel = `项目 · ${event.project_id} · ${plan}`;
          setPerspective("project", "session");
        } else {
          topbarState.projectLabel = "";
          if (!perspectiveLocked) setPerspective("default", "session");
        }
        updatePlaceholder();
        setStatus(`会话 ${event.session_id} · ${event.turn_mode_label}`);
        renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        if (perspective === "project") {
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        break;

      case "session.list":
        sessionsDropdown = event.sessions;
        topbarState.sessionCount = event.sessions.length;
        renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        if (sessionsOpen) renderSessionsDropdown();
        break;

      case "session.memory":
        topbarState.memoryLabel = `${event.message_count} 条 · ${event.memory_mode_label}`;
        renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        updateTokenBar(event.token_usage, event.token_limit);
        break;

      case "evolve.proposals":
        handleProposalsEvent(event.items);
        break;

      case "project.state":
        applyProjectStateEvent(projectState, event);
        if (!perspectiveLocked) setPerspective("project", "session");
        updatePlaceholder();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        }
        break;

      case "project.plan.state":
        applyProjectPlanState(projectState, event);
        if (!perspectiveLocked) setPerspective("project", "session");
        updatePlaceholder();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        }
        break;

      case "project.list":
        applyProjectListEvent(projectState, event);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.switch.request":
        projectState.switchInProgress = false;
        projectState.switchOverlay = {
          requestId: event.request_id,
          projectId: event.project_id,
          message: event.message,
          action: event.action,
        };
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.switch.done":
        projectState.switchInProgress = false;
        projectState.switchOverlay = null;
        projectState.pendingPickerId = "";
        projectState.projectId = event.project_id;
        if (event.session_replaced) {
          client.refreshSession();
        }
        client.listProjects();
        client.refreshProject();
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        }
        updatePlaceholder();
        setStatus(event.message);
        break;

      case "plan.request":
        projectState.planOverlay = {
          requestId: event.request_id,
          title: event.title,
          summary: event.summary,
          tasksPreview: event.tasks_preview,
          planStatus: event.plan_status,
        };
        projectState.planStatus = event.plan_status;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "plan.done":
        if (event.choice === "confirm") {
          projectState.planStatus = "confirmed";
          projectState.planOverlay = null;
          setStatus("计划已确认，可以开始写代码。");
        } else {
          projectState.planOverlay = null;
        }
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        if (projectState.projectId) {
          topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
          renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
        }
        client.refreshProject();
        break;

      case "project.verify.done": {
        projectState.verifyRunning = false;
        const passed = Boolean(event.passed);
        const lines = [
          passed ? "验收通过" : "验收未通过",
          event.exit_code !== undefined
            ? `退出码 ${event.exit_code}（期望 ${event.expected_exit_code ?? 0}）`
            : "",
          event.error ? `错误：${event.error}` : "",
          event.stdout ? `stdout:\n${event.stdout}` : "",
          event.stderr ? `stderr:\n${event.stderr}` : "",
        ].filter(Boolean);
        projectState.verifyResult = { passed, text: lines.join("\n\n") };
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        setStatus(passed ? "验收通过" : "验收未通过");
        client.refreshProject();
        break;
      }

      case "project.task.toggle.error":
        // Rollback optimistic toggle
        projectState.taskPhases = parseTasksMarkdown(projectState.tasksMarkdown);
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        setStatus(`任务更新失败：${event.message}`);
        break;

      case "project.task.toggle.done":
        // Confirmed; project.state will follow with authoritative data
        break;

      case "project.plan.confirm_changes.done":
        break;

      case "project.doc.list.done":
        projectState.projectDocs = event.docs;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.doc.read.done":
        projectState.currentDocContent = event.content;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.doc.create.done":
        projectState.newDocName = "";
        client.listDocs();
        break;

      case "project.task.add.done":
        // Refresh tasks from server to replace optimistic placeholder
        client.refreshProject();
        break;

      case "project.undo.available":
        projectState.undoDescription = event.description;
        if (projectState.undoTimerId) { window.clearTimeout(projectState.undoTimerId); }
        projectState.undoTimerId = window.setTimeout(() => {
          projectState.undoDescription = "";
          projectState.undoTimerId = null;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }, 3000) as unknown as number;
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        break;

      case "project.detect":
        // Auto-detect: workspace directory looks like a project
        projectState.detectedProject = { id: event.project_id, reason: event.reason };
        renderProjectSidebar(projectEls, projectState, projectCallbacks);
        setStatus(event.reason);
        break;

      case "tool.start":
        chat.handleEvent(event);
        setStatus(`· ${event.tool}`);
        break;

      case "tool.end":
        chat.handleEvent(event);
        if (!chat.model.confirmPending) {
          setStatus(event.ok ? (chat.isWorking() ? "处理中…" : "就绪") : "工具失败");
        }
        break;

      case "reasoning.delta":
        chat.handleEvent(event);
        if (!chat.model.cancelRequested) {
          setStatus("思考中…");
        }
        break;

      case "assistant.done":
        chat.handleEvent(event);
        if (perspective === "project") {
          client.refreshProject();
        }
        if (!chat.model.cancelRequested && !chat.model.confirmPending) {
          setStatus("就绪");
        }
        break;

      case "turn.end":
        chat.handleEvent(event);
        break;

      case "notice":
        chat.model.blocks.push({ kind: "notice", text: event.text });
        renderChat();
        if (
          projectState.planStatus !== "confirmed" &&
          /计划已确认|可以开始写代码/.test(event.text)
        ) {
          projectState.planStatus = "confirmed";
          projectState.planOverlay = null;
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
          if (projectState.projectId) {
            topbarState.projectLabel = `项目 · ${projectState.projectId} · ${planStatusLabel()}`;
            renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
          }
          client.refreshProject();
        }
        break;

      case "confirm.request":
      case "confirm.done":
        chat.handleEvent(event);
        break;

      case "error":
        if (projectState.verifyRunning) {
          projectState.verifyRunning = false;
          projectState.verifyResult = { passed: false, text: event.message };
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        if (projectState.switchInProgress || projectState.switchOverlay) {
          projectState.switchInProgress = false;
          projectState.pendingPickerId = "";
          renderProjectSidebar(projectEls, projectState, projectCallbacks);
        }
        chat.handleEvent(event);
        setStatus("错误");
        break;

      default:
        chat.handleEvent(event);
        break;
    }
  });

  function planStatusLabel(): string {
    if (projectState.planStatus === "confirmed") {
      if (projectState.tasksAllDone && projectState.tasksTotal > 0) return "全部完成";
      const open = Math.max(0, projectState.tasksTotal - projectState.tasksDone);
      return `${open}/${projectState.tasksTotal} 未完成`;
    }
    if (projectState.planStatus === "plan_dirty") return "计划已变更 · 待确认";
    return "计划待确认";
  }

  // ---- initial render ----
  updatePlaceholder();
  renderTopbar(topbarEl, topbarState, openProposals, handleNewSession, handleOpenSessions);
  renderProposalsPanel();
  renderChat();
  setComposerEnabled(true);
  setStatus("已连接");

  // ---- initial perspective from shellId hint ----
  const initPerspective = computeInitialPerspective(shellId, undefined);
  if (initPerspective !== "default") setPerspective(initPerspective, "auto");

  return () => {
    destroyed = true;
    if (cancelledStatusTimer !== null) window.clearTimeout(cancelledStatusTimer);
    clearCancelSafety();
    if (thinkingTimer !== null) window.clearInterval(thinkingTimer);
    if (renderThrottleTimer !== null) window.clearTimeout(renderThrottleTimer);
    renderedPrints = [];
    focusObserver?.disconnect();
    fileDrop.destroy();
    off();
    shellEl.classList.remove("is-working");
    setAgentBusy(false);
    root.innerHTML = "";
  };
}
