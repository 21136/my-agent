import type { AgentWsClient, ServerEvent, ShellId } from "../../api/ws";
import { setAgentBusy } from "../../agent-busy";
import { wireComposerAttachments } from "../../composer-attachments";
import { mountFileDrop } from "../../file-drop";
import { renderMarkdown } from "../../markdown";
import { formatUserMessageHtml } from "../../user-message";
import { createChatSession, escapeHtml, turnEndStatusText, checkerVerdictStatusText } from "../chat-state";
import "../grow/grow.css";
import "./project.css";

type PlanOverlay = {
  requestId: string;
  title: string;
  summary: string;
  tasksPreview: string;
  planStatus: string;
};

type SidebarTab = "tasks" | "map";

type VerifyResult = {
  passed: boolean;
  text: string;
};

type SwitchOverlay = {
  requestId: string;
  projectId: string;
  message: string;
  action: string;
};

type ProjectListItem = {
  id: string;
  tasksDone: number;
  tasksTotal: number;
  sessionId: string | null;
  isCurrent: boolean;
};

export function mountProjectShell(root: HTMLElement, client: AgentWsClient, shellId: ShellId = "project"): () => void {
  const ui = {
    projectId: "",
    planStatus: "",
    tasksMarkdown: "",
    mapMarkdown: "",
    tasksDone: 0,
    tasksTotal: 0,
    tasksAllDone: false,
    acceptanceCommand: "",
    canVerify: false,
    planOverlay: null as PlanOverlay | null,
    intentLabel: "",
    checkerLabel: "",
    memoryLabel: "",
    status: "连接中…",
    sidebarTab: "tasks" as SidebarTab,
    verifyResult: null as VerifyResult | null,
    verifyRunning: false,
    projects: [] as ProjectListItem[],
    switchOverlay: null as SwitchOverlay | null,
    switchInProgress: false,
    pendingPickerId: "",
  };
  let cancelledStatusTimer: number | null = null;

  const chat = createChatSession(
    { showProcess: true, confirmInBlocks: true },
    {
      onChange: () => {
        renderChat();
        syncWorkingVisual();
      },
      onTurnStart: (event) => {
        ui.intentLabel = event.intent_label;
        ui.checkerLabel = "";
        renderTopbar();
        setStatus(event.intent_label);
      },
      onCheckerVerdict: (event) => {
        ui.checkerLabel = checkerVerdictStatusText(event.verdict);
        renderTopbar();
        setStatus(ui.checkerLabel);
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
      onToolEnd: () => {
        if (!chat.model.confirmPending) {
          setStatus(chat.isWorking() ? "处理中…" : "就绪");
        }
      },
      onAssistantDone: () => {
        if (!chat.model.confirmPending) {
          setStatus("就绪");
        }
      },
      onTurnEnd: (_ok, finishReason) => {
        setComposerEnabled(true);
        if (!chat.model.confirmPending) {
          setTurnEndStatus(finishReason);
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

  root.innerHTML = `
    <div class="project-shell" id="project-shell">
      <aside class="project-sidebar" id="project-sidebar">
        <div class="project-sidebar-header">
          <div class="project-sidebar-title" id="project-sidebar-title">项目</div>
          <div class="project-sidebar-meta" id="project-sidebar-meta">未绑定项目</div>
        </div>
        <div class="project-picker" id="project-picker">
          <div class="project-picker-header">
            <span class="project-picker-title">我的项目</span>
            <button type="button" class="project-picker-refresh" id="project-picker-refresh">刷新</button>
          </div>
          <div class="project-picker-list" id="project-picker-list"></div>
        </div>
        <div class="project-switch-card hidden" id="project-switch-card">
          <div class="project-switch-card-title" id="project-switch-title">切换项目</div>
          <p class="project-switch-card-message" id="project-switch-message"></p>
          <div class="project-switch-card-actions">
            <button type="button" class="grow-btn grow-btn-accent" id="project-switch-confirm">确认切换</button>
            <button type="button" class="grow-btn" id="project-switch-cancel">取消</button>
          </div>
        </div>
        <div class="project-plan-card hidden" id="project-plan-card">
          <div class="project-plan-card-title" id="project-plan-title"></div>
          <pre class="project-plan-preview" id="project-plan-preview"></pre>
          <div class="project-plan-actions">
            <button type="button" class="grow-btn grow-btn-accent" id="project-plan-confirm">确认开工</button>
            <button type="button" class="grow-btn" id="project-plan-edit">修改计划</button>
          </div>
        </div>
        <div class="project-sidebar-tabs" id="project-sidebar-tabs">
          <button type="button" class="project-sidebar-tab is-active" data-tab="tasks">任务</button>
          <button type="button" class="project-sidebar-tab" data-tab="map">地图</button>
        </div>
        <div class="project-sidebar-panel project-markdown-panel" id="project-panel-tasks"></div>
        <div class="project-sidebar-panel project-markdown-panel hidden" id="project-panel-map"></div>
        <div class="project-verify-card hidden" id="project-verify-card">
          <div class="project-verify-title">验收</div>
          <pre class="project-verify-command" id="project-verify-command"></pre>
          <button type="button" class="grow-btn grow-btn-accent" id="project-verify-run">运行验收</button>
          <pre class="project-verify-result hidden" id="project-verify-result"></pre>
        </div>
      </aside>
      <div class="project-main">
        <div class="grow-shell" id="project-chat-shell">
          <header class="grow-topbar" id="grow-topbar"></header>
          <section class="grow-expand hidden" id="grow-expand"></section>
          <main class="grow-chat" id="grow-chat"></main>
          <div class="grow-status" id="grow-status"></div>
          <footer class="grow-composer" id="grow-composer">
            <button type="button" class="grow-btn" id="project-stop" hidden>停止</button>
            <textarea class="grow-input" id="grow-input" rows="1" placeholder="输入消息，或拖入代码文件…"></textarea>
            <button type="button" class="grow-btn grow-btn-accent" id="grow-send">发送</button>
          </footer>
        </div>
      </div>
    </div>
  `;

  const projectShell = root.querySelector<HTMLElement>("#project-shell")!;
  const sidebarTitle = root.querySelector<HTMLElement>("#project-sidebar-title")!;
  const sidebarMeta = root.querySelector<HTMLElement>("#project-sidebar-meta")!;
  const pickerList = root.querySelector<HTMLElement>("#project-picker-list")!;
  const pickerRefreshBtn = root.querySelector<HTMLButtonElement>("#project-picker-refresh")!;
  const switchCard = root.querySelector<HTMLElement>("#project-switch-card")!;
  const switchTitle = root.querySelector<HTMLElement>("#project-switch-title")!;
  const switchMessage = root.querySelector<HTMLElement>("#project-switch-message")!;
  const switchConfirmBtn = root.querySelector<HTMLButtonElement>("#project-switch-confirm")!;
  const switchCancelBtn = root.querySelector<HTMLButtonElement>("#project-switch-cancel")!;
  const planCard = root.querySelector<HTMLElement>("#project-plan-card")!;
  const planTitle = root.querySelector<HTMLElement>("#project-plan-title")!;
  const planPreview = root.querySelector<HTMLElement>("#project-plan-preview")!;
  const planConfirmBtn = root.querySelector<HTMLButtonElement>("#project-plan-confirm")!;
  const planEditBtn = root.querySelector<HTMLButtonElement>("#project-plan-edit")!;
  const sidebarTabs = root.querySelector<HTMLElement>("#project-sidebar-tabs")!;
  const tasksPanel = root.querySelector<HTMLElement>("#project-panel-tasks")!;
  const mapPanel = root.querySelector<HTMLElement>("#project-panel-map")!;
  const verifyCard = root.querySelector<HTMLElement>("#project-verify-card")!;
  const verifyCommand = root.querySelector<HTMLElement>("#project-verify-command")!;
  const verifyRunBtn = root.querySelector<HTMLButtonElement>("#project-verify-run")!;
  const verifyResultEl = root.querySelector<HTMLElement>("#project-verify-result")!;
  const topbar = root.querySelector<HTMLElement>("#grow-topbar")!;
  const chatEl = root.querySelector<HTMLElement>("#grow-chat")!;
  const statusEl = root.querySelector<HTMLElement>("#grow-status")!;
  const composer = root.querySelector<HTMLElement>("#grow-composer")!;
  const input = root.querySelector<HTMLTextAreaElement>("#grow-input")!;
  const stopBtn = root.querySelector<HTMLButtonElement>("#project-stop")!;
  const sendBtn = root.querySelector<HTMLButtonElement>("#grow-send")!;
  const chatShell = root.querySelector<HTMLElement>("#project-chat-shell")!;

  const fileDrop = mountFileDrop({
    composer,
    client,
    shell: "project",
    canAccept: () => !chat.model.confirmPending && Boolean(ui.projectId),
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
  });

  function syncWorkingVisual(): void {
    const working = chat.isWorking();
    chatShell.classList.toggle("is-working", working);
    projectShell.classList.toggle("is-working", working);
    stopBtn.hidden = !(working || chat.model.confirmPending);
    stopBtn.disabled = chat.model.cancelRequested;
    setAgentBusy(working, "project");
  }

  function setStatus(text: string): void {
    ui.status = text;
    statusEl.textContent = text;
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

  function setComposerEnabled(enabled: boolean): void {
    input.disabled = !enabled;
    composer.classList.toggle("disabled", !enabled);
    if (enabled) {
      composerWire.syncSendEnabled();
    } else {
      sendBtn.disabled = true;
    }
  }

  function planStatusLabel(): string {
    if (ui.planStatus === "confirmed") {
      if (ui.tasksAllDone && ui.tasksTotal > 0) return "全部完成";
      const open = Math.max(0, ui.tasksTotal - ui.tasksDone);
      return `${open}/${ui.tasksTotal} 未完成`;
    }
    if (ui.planStatus === "plan_dirty") return "计划已变更 · 待确认";
    return "计划待确认";
  }

  function projectProgressLabel(item: ProjectListItem): string {
    if (item.tasksTotal === 0) return "无任务";
    const open = Math.max(0, item.tasksTotal - item.tasksDone);
    if (open === 0) return "全部完成";
    return `${open}/${item.tasksTotal} 未完成`;
  }

  function projectSessionHint(item: ProjectListItem): string {
    if (item.isCurrent) return "当前";
    if (item.sessionId) return "可续接";
    return "新建会话";
  }

  function renderProjectPicker(): void {
    if (!ui.projects.length) {
      pickerList.innerHTML =
        '<p class="project-picker-empty">暂无项目 · 对话中说「项目 新建 &lt;id&gt;」</p>';
      return;
    }
    pickerList.innerHTML = ui.projects
      .map((item) => {
        const current = item.id === ui.projectId || item.isCurrent;
        const disabled = ui.switchInProgress || current;
        return `<button type="button" class="project-picker-item${current ? " is-current" : ""}" data-project-id="${escapeHtml(item.id)}"${disabled ? " disabled" : ""}>
          <span class="project-picker-item-name">${escapeHtml(item.id)}</span>
          <span class="project-picker-item-meta">${escapeHtml(projectProgressLabel(item))} · ${escapeHtml(projectSessionHint(item))}</span>
        </button>`;
      })
      .join("");
  }

  function renderSwitchCard(): void {
    if (ui.switchOverlay) {
      switchCard.classList.remove("hidden");
      switchTitle.textContent = `切换到 · ${ui.switchOverlay.projectId}`;
      switchMessage.textContent = ui.switchOverlay.message;
      switchConfirmBtn.textContent = "确认切换";
      switchConfirmBtn.disabled = ui.switchInProgress;
      switchCancelBtn.disabled = ui.switchInProgress;
    } else {
      switchCard.classList.add("hidden");
      switchMessage.textContent = "";
      switchConfirmBtn.textContent = "确认切换";
    }
  }

  function requestProjectSwitch(projectId: string): void {
    const target = projectId.trim();
    if (!target || target === ui.projectId) return;
    if (chat.isWorking()) {
      setStatus("助手执行中，请稍后再切换项目");
      return;
    }
    ui.switchInProgress = true;
    ui.pendingPickerId = target;
    renderProjectPicker();
    renderSwitchCard();
    try {
      client.switchProject(target);
    } catch (err) {
      ui.switchInProgress = false;
      ui.pendingPickerId = "";
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`切换失败：${message}`);
      renderProjectPicker();
      renderSwitchCard();
    }
  }

  function confirmProjectSwitch(): void {
    const overlay = ui.switchOverlay;
    if (!overlay) return;
    ui.switchInProgress = true;
    renderSwitchCard();
    try {
      client.switchProject(overlay.projectId, {
        confirm: true,
        requestId: overlay.requestId,
      });
    } catch (err) {
      ui.switchInProgress = false;
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`切换失败：${message}`);
      renderSwitchCard();
    }
  }

  function cancelProjectSwitch(): void {
    ui.switchOverlay = null;
    ui.switchInProgress = false;
    ui.pendingPickerId = "";
    renderProjectPicker();
    renderSwitchCard();
  }

  function finishProjectSwitch(
    event: Extract<ServerEvent, { type: "project.switch.done" }>,
  ): void {
    ui.switchInProgress = false;
    ui.switchOverlay = null;
    ui.pendingPickerId = "";
    ui.projectId = event.project_id;
    if (event.session_replaced) {
      client.refreshSession();
    }
    client.listProjects();
    client.refreshProject();
    renderProjectPicker();
    renderSwitchCard();
    setStatus(event.message);
  }

  function renderSidebarTabs(): void {
    for (const btn of sidebarTabs.querySelectorAll<HTMLButtonElement>(".project-sidebar-tab")) {
      const tab = btn.dataset.tab as SidebarTab | undefined;
      btn.classList.toggle("is-active", tab === ui.sidebarTab);
    }
    tasksPanel.classList.toggle("hidden", ui.sidebarTab !== "tasks");
    mapPanel.classList.toggle("hidden", ui.sidebarTab !== "map");
  }

  function renderMarkdownPanel(el: HTMLElement, markdown: string, emptyText: string): void {
    el.innerHTML = markdown
      ? renderMarkdown(markdown)
      : `<p class="text-muted">${escapeHtml(emptyText)}</p>`;
  }

  function renderVerifyCard(): void {
    if (ui.acceptanceCommand && ui.planStatus === "confirmed") {
      verifyCard.classList.remove("hidden");
      verifyCommand.textContent = ui.acceptanceCommand;
      verifyRunBtn.disabled = !ui.canVerify || ui.verifyRunning;
      if (ui.verifyResult) {
        verifyResultEl.classList.remove("hidden");
        verifyResultEl.classList.toggle("is-pass", ui.verifyResult.passed);
        verifyResultEl.classList.toggle("is-fail", !ui.verifyResult.passed);
        verifyResultEl.textContent = ui.verifyResult.text;
      } else {
        verifyResultEl.classList.add("hidden");
        verifyResultEl.textContent = "";
      }
    } else {
      verifyCard.classList.add("hidden");
    }
  }

  function renderSidebar(): void {
    if (ui.projectId) {
      sidebarTitle.textContent = `项目 · ${ui.projectId}`;
      sidebarMeta.textContent = planStatusLabel();
    } else {
      sidebarTitle.textContent = "项目";
      sidebarMeta.textContent = "未绑定项目 · 使用「项目 新建 <id>」";
    }

    renderMarkdownPanel(
      tasksPanel,
      ui.tasksMarkdown,
      "TASKS.md 将显示在这里",
    );
    renderMarkdownPanel(
      mapPanel,
      ui.mapMarkdown,
      "MAP.md 将随代码更新显示在这里",
    );
    renderSidebarTabs();

    if (ui.planOverlay && ui.planStatus !== "confirmed") {
      planCard.classList.remove("hidden");
      planTitle.textContent = ui.planOverlay.title;
      planPreview.textContent = ui.planOverlay.tasksPreview || ui.planOverlay.summary;
    } else if (ui.planStatus === "draft" || ui.planStatus === "plan_dirty") {
      planCard.classList.remove("hidden");
      planTitle.textContent =
        ui.planStatus === "plan_dirty" ? "计划已变更 · 请确认" : "计划待确认";
      planPreview.textContent = ui.tasksMarkdown.slice(0, 1200) || "（等待助手生成 TASKS.md）";
    } else {
      planCard.classList.add("hidden");
    }

    renderVerifyCard();
    renderProjectPicker();
    renderSwitchCard();
  }

  function renderTopbar(): void {
    const project = ui.projectId
      ? `<span class="grow-topbar-text">项目 · ${escapeHtml(ui.projectId)} · ${escapeHtml(planStatusLabel())}</span>`
      : "";
    const memory = ui.memoryLabel
      ? `<span class="grow-topbar-muted">${escapeHtml(ui.memoryLabel)}</span>`
      : "";
    const intent = ui.intentLabel
      ? `<span class="grow-topbar-muted">${escapeHtml(ui.intentLabel)}</span>`
      : "";
    const checker = ui.checkerLabel
      ? `<span class="grow-topbar-muted">${escapeHtml(ui.checkerLabel)}</span>`
      : "";
    const meta = [project, intent, checker, memory].filter(Boolean).join(" · ");
    topbar.innerHTML = meta
      ? `<span class="grow-topbar-text">${meta}</span>`
      : `<span class="grow-topbar-text grow-topbar-muted">项目模式</span>`;
  }

  function applyProjectState(event: Extract<ServerEvent, { type: "project.state" }>): void {
    ui.projectId = event.project_id ?? "";
    ui.planStatus = event.plan_status ?? "draft";
    ui.tasksMarkdown = event.tasks_markdown ?? "";
    ui.mapMarkdown = event.map_markdown ?? "";
    ui.tasksDone = event.tasks_done ?? 0;
    ui.tasksTotal = event.tasks_total ?? 0;
    ui.tasksAllDone = Boolean(event.tasks_all_done);
    ui.acceptanceCommand = event.acceptance_command ?? "";
    ui.canVerify = Boolean(event.can_verify);
    if (!event.needs_plan_confirm) {
      ui.planOverlay = null;
    }
    renderSidebar();
    renderTopbar();
  }

  function renderChat(): void {
    chatEl.innerHTML = "";
    for (const block of chat.model.blocks) {
      if (block.kind === "user") {
        chatEl.insertAdjacentHTML(
          "beforeend",
          `<article class="grow-turn"><div class="grow-turn-label">你</div><div class="grow-turn-body">${formatUserMessageHtml(block.text)}</div></article>`,
        );
      } else if (block.kind === "assistant" || block.kind === "assistant-streaming") {
        chatEl.insertAdjacentHTML(
          "beforeend",
          `<article class="grow-turn"><div class="grow-turn-label">助手</div><div class="grow-turn-body grow-markdown">${renderMarkdown(block.text)}</div></article>`,
        );
      } else if (block.kind === "notice") {
        chatEl.insertAdjacentHTML(
          "beforeend",
          `<p class="text-muted">${escapeHtml(block.text)}</p>`,
        );
      } else if (block.kind === "confirm") {
        const disabled = block.resolved ? "disabled" : "";
        const resolved = block.resolved
          ? `<div class="text-muted">${escapeHtml(block.resolved)}</div>`
          : `<div class="grow-expand-actions">
              <button type="button" class="grow-btn grow-btn-accent" data-confirm="y" data-id="${block.requestId}" ${disabled}>同意</button>
              <button type="button" class="grow-btn grow-btn-danger" data-confirm="n" data-id="${block.requestId}" ${disabled}>拒绝</button>
              ${
                block.allowApproveAll
                  ? `<button type="button" class="grow-btn" data-confirm="a" data-id="${block.requestId}" ${disabled}>本会话 workspace 均允许</button>`
                  : ""
              }
            </div>`;
        chatEl.insertAdjacentHTML(
          "beforeend",
          `<div class="grow-surface grow-confirm"><div class="grow-expand-title">工具确认</div><pre class="grow-confirm-preview">${escapeHtml(block.preview)}</pre>${resolved}</div>`,
        );
      }
    }
    chatEl.scrollTop = chatEl.scrollHeight;
    setComposerEnabled(!chat.model.confirmPending);
    syncWorkingVisual();
  }

  async function sendCurrentPlanResponse(choice: "confirm" | "edit"): Promise<void> {
    const overlay = ui.planOverlay;
    if (!overlay) {
      try {
        client.sendCommand("项目 确认");
        setStatus("正在确认计划…");
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        setStatus(`计划确认失败：${message}`);
      }
      return;
    }
    try {
      client.sendPlanResponse(overlay.requestId, choice);
      setStatus(choice === "confirm" ? "正在确认计划…" : "继续修改计划…");
      if (choice === "edit") {
        ui.planOverlay = null;
        renderSidebar();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`计划确认失败：${message}`);
    }
  }

  planConfirmBtn.addEventListener("click", () => {
    void sendCurrentPlanResponse("confirm");
  });
  planEditBtn.addEventListener("click", () => {
    void sendCurrentPlanResponse("edit");
  });

  pickerRefreshBtn.addEventListener("click", () => {
    try {
      client.listProjects();
      setStatus("刷新项目列表…");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`刷新失败：${message}`);
    }
  });

  pickerList.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".project-picker-item");
    if (!btn?.dataset.projectId || btn.disabled) return;
    requestProjectSwitch(btn.dataset.projectId);
  });

  switchConfirmBtn.addEventListener("click", () => {
    confirmProjectSwitch();
  });

  switchCancelBtn.addEventListener("click", () => {
    cancelProjectSwitch();
  });

  sidebarTabs.addEventListener("click", (ev) => {
    const btn = (ev.target as HTMLElement).closest<HTMLButtonElement>(".project-sidebar-tab");
    if (!btn?.dataset.tab) return;
    ui.sidebarTab = btn.dataset.tab as SidebarTab;
    renderSidebarTabs();
  });

  verifyRunBtn.addEventListener("click", () => {
    if (!ui.canVerify || ui.verifyRunning) return;
    ui.verifyRunning = true;
    ui.verifyResult = null;
    renderVerifyCard();
    setStatus("运行验收…");
    try {
      client.runProjectVerify();
    } catch (err) {
      ui.verifyRunning = false;
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`验收失败：${message}`);
      renderVerifyCard();
    }
  });

  sendBtn.addEventListener("click", () => composerWire.sendCurrentMessage());
  stopBtn.addEventListener("click", () => {
    if (!chat.requestCancel()) return;
    setComposerEnabled(false);
    setStatus("正在停止…");
    try {
      client.sendTurnCancel();
    } catch (err) {
      chat.model.cancelRequested = false;
      setComposerEnabled(!chat.model.confirmPending);
      setStatus(`停止发送失败：${err instanceof Error ? err.message : String(err)}`);
      syncWorkingVisual();
    }
  });

  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      composerWire.sendCurrentMessage();
    }
  });

  chatEl.addEventListener("click", async (ev) => {
    const target = ev.target as HTMLElement;
    const btn = target.closest<HTMLButtonElement>("[data-confirm]");
    if (!btn || btn.disabled) return;
    const requestId = btn.dataset.id;
    const choice = btn.dataset.confirm as "y" | "n" | "a" | undefined;
    if (!requestId || !choice) return;
    if (!chat.submitConfirm(requestId, choice)) {
      setStatus("请点最新一张工具确认卡");
      return;
    }
    try {
      client.sendConfirm(requestId, choice);
      setStatus(choice === "n" ? "已提交拒绝…" : "确认中…");
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      setStatus(`确认失败：${message}`);
      chat.model.confirmSubmitting = false;
      const block = chat.model.blocks.find((b) => b.kind === "confirm" && b.requestId === requestId);
      if (block?.kind === "confirm") {
        block.resolved = undefined;
      }
      if (chat.model.confirmOverlay?.requestId === requestId) {
        chat.model.confirmOverlay = { ...chat.model.confirmOverlay, resolved: undefined };
      }
      renderChat();
      syncWorkingVisual();
    }
  });

  const off = client.onEvent((event: ServerEvent) => {
    if (!client.isActiveShell(shellId)) return;
    switch (event.type) {
      case "session.banner":
        chat.handleEvent(event);
        if (event.project_id) {
          ui.projectId = event.project_id;
          ui.planStatus = event.project_plan_status ?? ui.planStatus;
          ui.tasksDone = event.project_tasks_done ?? ui.tasksDone;
          ui.tasksTotal = event.project_tasks_total ?? ui.tasksTotal;
        }
        setStatus(`会话 ${event.session_id} · ${event.turn_mode_label}`);
        renderSidebar();
        renderTopbar();
        break;
      case "project.state":
        applyProjectState(event);
        renderProjectPicker();
        break;
      case "project.list":
        ui.projects = event.projects.map((item) => ({
          id: item.id,
          tasksDone: item.tasks_done,
          tasksTotal: item.tasks_total,
          sessionId: item.session_id,
          isCurrent: item.is_current,
        }));
        renderProjectPicker();
        break;
      case "project.switch.request":
        ui.switchInProgress = false;
        ui.switchOverlay = {
          requestId: event.request_id,
          projectId: event.project_id,
          message: event.message,
          action: event.action,
        };
        renderProjectPicker();
        renderSwitchCard();
        break;
      case "project.switch.done":
        finishProjectSwitch(event);
        break;
      case "plan.request":
        ui.planOverlay = {
          requestId: event.request_id,
          title: event.title,
          summary: event.summary,
          tasksPreview: event.tasks_preview,
          planStatus: event.plan_status,
        };
        ui.planStatus = event.plan_status;
        renderSidebar();
        break;
      case "plan.done":
        if (event.choice === "confirm") {
          ui.planStatus = "confirmed";
          ui.planOverlay = null;
          setStatus("计划已确认，可以开始写代码。");
        } else {
          ui.planOverlay = null;
        }
        renderSidebar();
        renderTopbar();
        client.refreshProject();
        break;
      case "project.verify.done": {
        ui.verifyRunning = false;
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
        ui.verifyResult = { passed, text: lines.join("\n\n") };
        renderVerifyCard();
        setStatus(passed ? "验收通过" : "验收未通过");
        client.refreshProject();
        break;
      }
      case "session.memory":
        ui.memoryLabel = `${event.message_count} 条 · ${event.memory_mode_label}`;
        renderTopbar();
        break;
      case "tool.start":
      case "tool.end":
      case "reasoning.delta":
      case "assistant.delta":
      case "assistant.done":
      case "turn.start":
      case "turn.end":
      case "turn.notice":
      case "checker.verdict":
      case "confirm.request":
      case "confirm.done":
      case "session.history":
        chat.handleEvent(event);
        if (event.type === "session.history") {
          renderChat();
        }
        if (event.type === "assistant.done") {
          client.refreshProject();
        }
        if (event.type === "tool.start") setStatus(`· ${event.tool}`);
        break;
      case "notice":
        chat.model.blocks.push({ kind: "notice", text: event.text });
        renderChat();
        if (
          ui.planStatus !== "confirmed" &&
          /计划已确认|可以开始写代码/.test(event.text)
        ) {
          ui.planStatus = "confirmed";
          ui.planOverlay = null;
          renderSidebar();
          renderTopbar();
          client.refreshProject();
        }
        break;
      case "error":
        if (ui.verifyRunning) {
          ui.verifyRunning = false;
          ui.verifyResult = { passed: false, text: event.message };
          renderVerifyCard();
        }
        if (ui.switchInProgress || ui.switchOverlay) {
          ui.switchInProgress = false;
          ui.pendingPickerId = "";
          renderProjectPicker();
          renderSwitchCard();
        }
        setStatus(event.message);
        break;
      default:
        break;
    }
  });

  client.listProjects();

  return () => {
    if (cancelledStatusTimer !== null) window.clearTimeout(cancelledStatusTimer);
    fileDrop.destroy();
    off();
    root.innerHTML = "";
  };
}
