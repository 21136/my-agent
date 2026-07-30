import type { AgentWsClient, PlanChangeItem, ProjectDocItem, ServerEvent } from "../../api/ws";
import { renderMarkdown } from "../../markdown";
import { escapeHtml } from "../chat-state";

// ---- new task-flow types ----

export interface TaskItem {
  line: number;
  text: string;
  done: boolean;
  status: "done" | "current" | "pending" | "new" | "skipped" | "removed";
  subtasks?: { text: string; done: boolean }[];
}

export interface TaskPhase {
  title: string;
  tasks: TaskItem[];
}

/** Snapshot of task list for diffing across updates. */
export interface TaskSnapshot {
  lines: Set<number>;
  lineTexts: Map<number, string>;
}

export type OverlayPanel = "docs" | "verify" | "projects" | "tasks" | null;

// ---- existing types (compat) ----

export interface PlanOverlayData {
  requestId: string;
  title: string;
  summary: string;
  tasksPreview: string;
  planStatus: string;
}

export interface SwitchOverlayData {
  requestId: string;
  projectId: string;
  message: string;
  action: string;
}

export interface ProjectListItem {
  id: string;
  tasksDone: number;
  tasksTotal: number;
  sessionId: string | null;
  isCurrent: boolean;
}

export interface VerifyResult {
  passed: boolean;
  text: string;
}

export interface ProjectPanelState {
  projectId: string;
  planStatus: string;
  tasksMarkdown: string;
  mapMarkdown: string;
  tasksDone: number;
  tasksTotal: number;
  tasksAllDone: boolean;
  acceptanceCommand: string;
  canVerify: boolean;
  planOverlay: PlanOverlayData | null;
  projects: ProjectListItem[];
  switchOverlay: SwitchOverlayData | null;
  switchInProgress: boolean;
  pendingPickerId: string;
  verifyResult: VerifyResult | null;
  verifyRunning: boolean;
  // new fields
  overlayPanel: OverlayPanel;
  taskPhases: TaskPhase[];
  taskSnapshot: TaskSnapshot;
  planBannerCollapsed: boolean;
  switchConfirmTarget: ProjectListItem | null;
  projectSearchQuery: string;
  planChangeLog: PlanChangeItem[];
  highlightChanges: boolean;
  highlightedLines: Set<number>;
  // doc panel
  projectDocs: ProjectDocItem[];
  currentDocPath: string;
  currentDocContent: string;
  newDocName: string;
  // task add
  quickAddText: string;
  // auto-detect
  detectedProject: { id: string; reason: string } | null;
  // Plan Agent warnings
  planWarnings: string[];
}

export interface ProjectPanelCallbacks {
  onProjectSwitch: (projectId: string) => void;
  onProjectSwitchConfirm: () => void;
  onProjectSwitchCancel: () => void;
  onPlanConfirm: () => void;
  onPlanEdit: () => void;
  onRefreshProjects: () => void;
  onRunVerify: () => void;
}

// ---- helpers ----

function planStatusLabel(state: ProjectPanelState): string {
  if (state.planStatus === "confirmed") {
    if (state.tasksAllDone && state.tasksTotal > 0) return "全部完成";
    const open = Math.max(0, state.tasksTotal - state.tasksDone);
    return `${open}/${state.tasksTotal} 未完成`;
  }
  if (state.planStatus === "plan_dirty") return "计划已变更 · 待确认";
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

// ---- TASKS.md parser ----

const TASK_RE = /^\s*-\s*\[([ xX])\]\s+(.*)$/;
const PHASE_RE = /^##\s+(.+)$/;

export function parseTasksMarkdown(text: string): TaskPhase[] {
  const lines = text.split("\n");
  const phases: TaskPhase[] = [];
  let currentPhase: TaskPhase = { title: "", tasks: [] };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const phaseMatch = line.match(PHASE_RE);
    if (phaseMatch) {
      if (currentPhase.title || currentPhase.tasks.length) {
        phases.push(currentPhase);
      }
      currentPhase = { title: phaseMatch[1].trim(), tasks: [] };
      continue;
    }

    const taskMatch = line.match(TASK_RE);
    if (taskMatch) {
      const done = taskMatch[1].toLowerCase() === "x";
      currentPhase.tasks.push({
        line: i,
        text: taskMatch[2].trim(),
        done,
        status: done ? "done" : "pending",
      });
    }
  }

  if (currentPhase.title || currentPhase.tasks.length) {
    phases.push(currentPhase);
  }

  // assign "current" to first undone
  let found = false;
  for (const phase of phases) {
    for (const task of phase.tasks) {
      if (!task.done && !found) {
        task.status = "current";
        found = true;
      }
    }
  }

  return phases;
}

// ---- change detection ----

export function captureTaskSnapshot(phases: TaskPhase[]): TaskSnapshot {
  const lines = new Set<number>();
  const lineTexts = new Map<number, string>();
  for (const phase of phases) {
    for (const task of phase.tasks) {
      lines.add(task.line);
      lineTexts.set(task.line, task.text);
    }
  }
  return { lines, lineTexts };
}

export function diffTaskPhases(old: TaskSnapshot, phases: TaskPhase[]): {
  addedLines: Set<number>;
  removedLines: Set<number>;
  changedLines: Set<number>;
} {
  const currentLines = new Set<number>();
  for (const phase of phases) {
    for (const task of phase.tasks) {
      currentLines.add(task.line);
    }
  }

  const addedLines = new Set<number>();
  const removedLines = new Set<number>();
  const changedLines = new Set<number>();

  // New lines not in old snapshot
  for (const line of currentLines) {
    if (!old.lines.has(line)) {
      addedLines.add(line);
    } else if (old.lines.has(line)) {
      // Text changed for same line?
      const oldText = old.lineTexts.get(line);
      const task = phases.flatMap((p) => p.tasks).find((t) => t.line === line);
      if (oldText && task && oldText !== task.text) {
        changedLines.add(line);
      }
    }
  }

  // Lines in old but not current
  for (const line of old.lines) {
    if (!currentLines.has(line)) {
      removedLines.add(line);
    }
  }

  return { addedLines, removedLines, changedLines };
}

// ---- task flow rendering ----

function renderTaskFlow(state: ProjectPanelState, highlightLines: Set<number> | null = null): string {
  if (!state.taskPhases.length) {
    return `<p class="overlay-empty">TASKS.md 将显示在这里</p>`;
  }

  let html = "";
  for (const phase of state.taskPhases) {
    html += `<div class="task-phase-header">${escapeHtml(phase.title)}</div>`;
    for (const task of phase.tasks) {
      const cls = ["task-item"];
      if (task.status === "done") cls.push("is-done");
      else if (task.status === "current") cls.push("is-current");
      else if (task.status === "new") cls.push("is-new");
      else if (task.status === "skipped") cls.push("is-skipped");
      else if (task.status === "removed") cls.push("is-removed");

      // Highlight mode: mark changed lines for "查看变更"
      if (highlightLines && highlightLines.has(task.line)) {
        cls.push("is-highlighted");
      }

      html += `<label class="${cls.join(" ")}" data-line="${task.line}">
        <input type="checkbox" class="task-checkbox" data-line="${task.line}"${task.done ? " checked" : ""}>
        <span class="task-text">${escapeHtml(task.text)}</span>
      </label>`;

      if (task.subtasks && task.subtasks.length > 0) {
        for (const sub of task.subtasks) {
          const subCls = sub.done ? "task-subtask is-done" : "task-subtask";
          html += `<div class="${subCls}">
            <span class="task-subtask-dot"></span>
            <span class="task-text">${escapeHtml(sub.text)}</span>
          </div>`;
        }
      }
    }
  }
  // Quick-add input at bottom of task flow
  html += `<div style="display:flex;gap:0.3rem;padding:0.5rem 0.75rem;border-top:1px solid var(--ma-border);margin-top:0.25rem;">
    <input type="text" class="overlay-search-input" id="sidebar-quick-add-input" placeholder="添加任务…" value="${escapeHtml(state.quickAddText)}" style="margin-bottom:0;font-size:0.8rem;">
  </div>`;

  return html;
}

// ---- overlay panel rendering ----

function renderDocsOverlay(state: ProjectPanelState): string {
  // If viewing a specific document
  if (state.currentDocPath) {
    return `<div style="padding:0.25rem 0;font-size:0.78rem;color:var(--ma-text-muted);margin-bottom:0.5rem;">${escapeHtml(state.currentDocPath)}</div>
      <div class="unified-markdown-panel">${state.currentDocContent
        ? renderMarkdown(state.currentDocContent)
        : `<p class="overlay-empty">加载中…</p>`}</div>`;
  }

  // List all docs
  let html = "";

  // New doc input
  html += `<div style="display:flex;gap:0.3rem;margin-bottom:0.5rem;">
    <input type="text" class="overlay-search-input" id="overlay-new-doc-input" placeholder="新建文档（如 需求分析.md）…" value="${escapeHtml(state.newDocName)}" style="margin-bottom:0;">
    <button type="button" class="unified-btn unified-btn-accent" id="overlay-new-doc-btn" style="flex-shrink:0;font-size:0.78rem;">新建</button>
  </div>`;

  if (!state.projectDocs.length) {
    html += `<p class="overlay-empty">暂无文档</p>`;
    return html;
  }

  // Sort: standard docs first
  const sorted = [...state.projectDocs].sort((a, b) => {
    if (a.is_standard && !b.is_standard) return -1;
    if (!a.is_standard && b.is_standard) return 1;
    return a.name.localeCompare(b.name);
  });

  html += `<div style="font-size:0.72rem;font-weight:600;color:var(--ma-text-muted);margin-bottom:0.25rem;">项目文档</div>`;
  for (const doc of sorted) {
    const icon = doc.is_standard ? "📋" : "📄";
    const sub = doc.name !== doc.path ? ` <span style="font-size:0.7rem;color:var(--ma-text-muted);">${escapeHtml(doc.path)}</span>` : "";
    html += `<button type="button" class="overlay-doc-item" data-doc-path="${escapeHtml(doc.path)}">
      <span>${icon} ${escapeHtml(doc.name)}</span>${sub}
    </button>`;
  }

  return html;
}

function renderVerifyOverlay(state: ProjectPanelState): string {
  if (!state.acceptanceCommand && !state.verifyResult) {
    return `<p class="overlay-empty">计划确认后，验收命令将显示在这里</p>`;
  }

  let html = "";
  if (state.acceptanceCommand) {
    html += `<div class="overlay-verify-command">${escapeHtml(state.acceptanceCommand)}</div>`;
    html += `<button type="button" class="unified-btn unified-btn-accent" id="overlay-verify-run"${!state.canVerify || state.verifyRunning ? " disabled" : ""}>${state.verifyRunning ? "运行中…" : "运行验收"}</button>`;
  }

  if (state.verifyResult) {
    const cls = state.verifyResult.passed ? "is-pass" : "is-fail";
    html += `<pre class="overlay-verify-result ${cls}">${escapeHtml(state.verifyResult.text)}</pre>`;
  }

  return html;
}

function renderProjectsOverlay(state: ProjectPanelState): string {
  // If switch confirm target is set, show confirm instead of list
  if (state.switchConfirmTarget) {
    const item = state.switchConfirmTarget;
    return `<div class="overlay-switch-confirm" id="overlay-switch-confirm">
      <div class="overlay-switch-confirm-title">切换到 · ${escapeHtml(item.id)}</div>
      <div class="overlay-switch-confirm-text">
        将恢复已有会话。当前会话已自动保存。
      </div>
      <div class="overlay-switch-confirm-actions">
        <button type="button" class="unified-btn unified-btn-accent" id="overlay-switch-confirm-btn">确认切换</button>
        <button type="button" class="unified-btn" id="overlay-switch-cancel-btn">取消</button>
      </div>
    </div>`;
  }

  let html = `<input type="text" class="overlay-search-input" id="overlay-project-search" placeholder="搜索项目…" value="${escapeHtml(state.projectSearchQuery)}">`;

  const query = state.projectSearchQuery.toLowerCase().trim();

  if (!state.projects.length) {
    html += `<p class="overlay-empty">暂无项目 · 对话中说「项目 新建 &lt;id&gt;」</p>`;
    return html;
  }

  const filtered = query
    ? state.projects.filter((p) => p.id.toLowerCase().includes(query))
    : state.projects;

  if (filtered.length === 0) {
    html += `<p class="overlay-empty">无匹配 · 「项目 新建 ${escapeHtml(query)}」</p>`;
    return html;
  }

  for (const item of filtered) {
    const current = item.id === state.projectId || item.isCurrent;
    const disabled = state.switchInProgress || current;
    html += `<button type="button" class="overlay-project-item${current ? " is-current" : ""}" data-project-id="${escapeHtml(item.id)}"${disabled ? " disabled" : ""}>
      <span class="overlay-project-item-name">${escapeHtml(item.id)}</span>
      <span class="overlay-project-item-meta">${escapeHtml(projectProgressLabel(item))} · ${escapeHtml(projectSessionHint(item))}</span>
    </button>`;
  }

  return html;
}

function renderOverlayBody(state: ProjectPanelState): string {
  switch (state.overlayPanel) {
    case "docs":
      return renderDocsOverlay(state);
    case "verify":
      return renderVerifyOverlay(state);
    case "projects":
      return renderProjectsOverlay(state);
    default:
      return "";
  }
}

function overlayTitle(panel: OverlayPanel): string {
  switch (panel) {
    case "docs": return "文档";
    case "verify": return "验收";
    case "projects": return "我的项目";
    default: return "";
  }
}

// ---- change banner / plan confirmation inline ----

function renderChangeBanner(state: ProjectPanelState): string {
  // Plan confirmation (draft / plan_dirty with overlay)
  const needsPlanConfirm =
    state.planOverlay && state.planStatus !== "confirmed";
  const needsPlanDirtyBanner =
    state.planStatus === "plan_dirty" && !needsPlanConfirm;

  // Plan confirmation takes priority over change banner
  if (needsPlanConfirm) {
    const planLabel = state.planStatus === "plan_dirty"
      ? "计划已变更 · 请确认"
      : "计划待确认";
    return `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);background:color-mix(in srgb, var(--ma-accent) 6%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">${escapeHtml(planLabel)} (${escapeHtml(state.projectId)})</div>
      <button type="button" class="unified-btn unified-btn-accent" data-action="confirm-plan" style="margin-right:0.4rem;">确认开工</button>
      <button type="button" class="unified-btn" data-action="edit-plan">修改计划</button>
    </div>`;
  }

  if (needsPlanDirtyBanner) {
    if (state.planBannerCollapsed) {
      const pendingCount = state.planChangeLog.length;
      return `<div class="sidebar-change-banner">
        <span style="color:#d4a000">⚠ 计划已变更${pendingCount > 0 ? ` (${pendingCount} 项)` : ""}</span>
        <button type="button" class="unified-btn" data-action="expand-banner" style="margin-left:0.5rem;font-size:0.72rem;">查看</button>
      </div>`;
    }

    // Build change summary from change_log
    let changesHtml = "";
    if (state.planChangeLog.length > 0) {
      const recent = state.planChangeLog.slice(-8);
      changesHtml = recent.map((c) => {
        const icon = c.kind === "toggle" ? "✓" : c.kind === "add" ? "+" : c.kind === "drop" ? "−" : c.kind === "skip" ? "~" : "⇅";
        return `${icon} ${escapeHtml(c.task_text)}`;
      }).join("<br>");
    } else {
      changesHtml = "Phase 结构已变更，请确认后继续。";
    }

    const highlightLabel = state.highlightChanges ? "取消高亮" : "查看变更";
    return `<div class="sidebar-change-banner">
      <div class="sidebar-change-banner-title">⚠ 计划已变更 · 待确认</div>
      <div class="sidebar-change-banner-changes">${changesHtml}</div>
      <div class="sidebar-change-banner-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-action="confirm-changes">确认变更</button>
        <button type="button" class="unified-btn" data-action="toggle-highlight">${highlightLabel}</button>
        <button type="button" class="unified-btn" data-action="collapse-banner">收起</button>
      </div>
    </div>`;
  }

  return "";
}

// ---- project event application (keep compat) ----

export function applyProjectStateEvent(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.state" }>,
): void {
  state.projectId = event.project_id ?? "";
  state.planStatus = event.plan_status ?? "draft";
  state.tasksMarkdown = event.tasks_markdown ?? "";
  state.mapMarkdown = event.map_markdown ?? "";
  state.tasksDone = event.tasks_done ?? 0;
  state.tasksTotal = event.tasks_total ?? 0;
  state.tasksAllDone = Boolean(event.tasks_all_done);
  state.acceptanceCommand = event.acceptance_command ?? "";
  state.canVerify = Boolean(event.can_verify);

  // Diff old vs new to detect changes
  const oldPhases = state.taskPhases.length > 0 ? state.taskPhases : null;
  const oldSnapshot = state.taskPhases.length > 0
    ? captureTaskSnapshot(state.taskPhases)
    : { lines: new Set<number>(), lineTexts: new Map<number, string>() } as TaskSnapshot;

  state.taskPhases = parseTasksMarkdown(state.tasksMarkdown);

  if (oldPhases) {
    const diff = diffTaskPhases(oldSnapshot, state.taskPhases);
    // Mark newly added lines as "new" status
    for (const phase of state.taskPhases) {
      for (const task of phase.tasks) {
        if (diff.addedLines.has(task.line)) {
          task.status = "new";
        }
      }
    }
    // Collect highlighted lines from diff
    state.highlightedLines = new Set([
      ...diff.addedLines,
      ...diff.removedLines,
      ...diff.changedLines,
    ]);
    // If there are changes and highlight mode isn't active, store but don't show yet
    if (state.highlightedLines.size > 0 && !state.highlightChanges) {
      // Changes detected; planBanner will show them
    }
  }

  state.taskSnapshot = captureTaskSnapshot(state.taskPhases);

  if (!event.needs_plan_confirm) {
    state.planOverlay = null;
    state.highlightChanges = false;
  }
}

export function applyProjectPlanState(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.plan.state" }>,
): void {
  state.projectId = event.project_id ?? "";
  state.planStatus = event.plan_status ?? "draft";
  state.tasksMarkdown = event.tasks_markdown ?? "";
  state.mapMarkdown = event.map_markdown ?? "";
  state.tasksDone = event.tasks_done ?? 0;
  state.tasksTotal = event.tasks_total ?? 0;
  state.tasksAllDone = Boolean(event.tasks_all_done);
  state.planChangeLog = event.change_log ?? [];
  state.planWarnings = event.warnings ?? [];
  // Show auto_fix actions as part of warnings display
  const autoFixes = event.auto_fix_actions ?? [];
  if (autoFixes.length > 0) {
    state.planWarnings = [...autoFixes, ...state.planWarnings];
  }

  // Diff old vs new task phases
  const oldSnapshot = state.taskPhases.length > 0
    ? captureTaskSnapshot(state.taskPhases)
    : { lines: new Set<number>(), lineTexts: new Map<number, string>() } as TaskSnapshot;

  state.taskPhases = parseTasksMarkdown(state.tasksMarkdown);

  if (oldSnapshot.lines.size > 0) {
    const diff = diffTaskPhases(oldSnapshot, state.taskPhases);
    for (const phase of state.taskPhases) {
      for (const task of phase.tasks) {
        if (diff.addedLines.has(task.line)) {
          task.status = "new";
        }
      }
    }
    state.highlightedLines = new Set([
      ...diff.addedLines,
      ...diff.removedLines,
      ...diff.changedLines,
    ]);
  }

  state.taskSnapshot = captureTaskSnapshot(state.taskPhases);
}

export function applyProjectListEvent(
  state: ProjectPanelState,
  event: Extract<ServerEvent, { type: "project.list" }>,
): void {
  state.projects = event.projects.map((item) => ({
    id: item.id,
    tasksDone: item.tasks_done,
    tasksTotal: item.tasks_total,
    sessionId: item.session_id,
    isCurrent: item.is_current,
  }));
}

// ---- element refs (matches new DOM) ----

export function setupProjectPanel(container: HTMLElement): {
  sidebarTitle: HTMLElement;
  sidebarMeta: HTMLElement;
  sidebarProgressWrap: HTMLElement;
  sidebarProgressFill: HTMLElement;
  taskFlow: HTMLElement;
  changeBanner: HTMLElement;
  iconBar: HTMLElement;
  overlayPanel: HTMLElement;
  overlayTitle: HTMLElement;
  overlayBody: HTMLElement;
  overlayBackBtn: HTMLButtonElement;
  // compat refs kept for index.ts event wiring
  pickerRefreshBtn: HTMLButtonElement;
  switchConfirmBtn: HTMLButtonElement;
  switchCancelBtn: HTMLButtonElement;
  switchCard: HTMLElement;
  switchTitle: HTMLElement;
  switchMessage: HTMLElement;
  planCard: HTMLElement;
  planTitle: HTMLElement;
  planPreview: HTMLElement;
  planConfirmBtn: HTMLButtonElement;
  planEditBtn: HTMLButtonElement;
  sidebarTabs: HTMLElement;
  tasksPanel: HTMLElement;
  mapPanel: HTMLElement;
  verifyCard: HTMLElement;
  verifyCommand: HTMLElement;
  verifyRunBtn: HTMLButtonElement;
  verifyResultEl: HTMLElement;
  pickerList: HTMLElement;
} {
  const el = (id: string) => container.querySelector<HTMLElement>(`#${id}`)!;

  return {
    // new elements
    sidebarTitle: el("project-sidebar-title"),
    sidebarMeta: el("project-sidebar-meta"),
    sidebarProgressWrap: el("project-sidebar-progress"),
    sidebarProgressFill: el("sidebar-progress-fill"),
    taskFlow: el("sidebar-task-flow"),
    changeBanner: el("sidebar-change-banner"),
    iconBar: el("sidebar-icon-bar"),
    overlayPanel: el("sidebar-overlay"),
    overlayTitle: el("overlay-title"),
    overlayBody: el("overlay-body"),
    overlayBackBtn: el("overlay-back-btn") as HTMLButtonElement,
    // compat refs (dummy elements kept for old index.ts event wiring)
    pickerRefreshBtn: el("project-picker-refresh") as HTMLButtonElement,
    switchConfirmBtn: el("project-switch-confirm") as HTMLButtonElement,
    switchCancelBtn: el("project-switch-cancel") as HTMLButtonElement,
    switchCard: el("project-switch-card"),
    switchTitle: el("project-switch-title"),
    switchMessage: el("project-switch-message"),
    planCard: el("project-plan-card"),
    planTitle: el("project-plan-title"),
    planPreview: el("project-plan-preview"),
    planConfirmBtn: el("project-plan-confirm") as HTMLButtonElement,
    planEditBtn: el("project-plan-edit") as HTMLButtonElement,
    sidebarTabs: el("project-sidebar-tabs"),
    tasksPanel: el("project-panel-tasks"),
    mapPanel: el("project-panel-map"),
    verifyCard: el("project-verify-card"),
    verifyCommand: el("project-verify-command"),
    verifyRunBtn: el("project-verify-run") as HTMLButtonElement,
    verifyResultEl: el("project-verify-result"),
    pickerList: el("project-picker-list"),
  };
}

// ---- main render ----

export function renderProjectSidebar(
  els: ReturnType<typeof setupProjectPanel>,
  state: ProjectPanelState,
  callbacks: ProjectPanelCallbacks,
): void {
  // header
  if (state.projectId) {
    els.sidebarTitle.textContent = `项目 · ${state.projectId}`;
    els.sidebarMeta.textContent = planStatusLabel(state);
    els.sidebarProgressWrap.classList.remove("hidden");
    const pct = state.tasksTotal > 0
      ? Math.round((state.tasksDone / state.tasksTotal) * 100)
      : 0;
    els.sidebarProgressFill.style.width = `${pct}%`;
  } else {
    els.sidebarTitle.textContent = "项目";
    els.sidebarMeta.textContent = "未绑定项目 · 使用「项目 新建 <id>」";
    els.sidebarProgressWrap.classList.add("hidden");
  }

  // Plan Agent warnings
  if (state.planWarnings.length > 0) {
    const warnHtml = state.planWarnings
      .map((w) => `<div style="padding:0.2rem 0.75rem;font-size:0.78rem;color:#d4a000;">⚠ ${escapeHtml(w)}</div>`)
      .join("");
    els.changeBanner.classList.remove("hidden");
    els.changeBanner.innerHTML = `<div class="sidebar-change-banner" style="border-color:#d4a000;background:color-mix(in srgb, #d4a000 6%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">项目管理器反馈</div>
      ${warnHtml}
      <button type="button" class="unified-btn" data-action="dismiss-warnings" style="margin-top:0.3rem;font-size:0.72rem;">关闭</button>
    </div>`;
  } else if (!state.detectedProject) {
    // change banner (only if no detection banner and no warnings)
    els.changeBanner.innerHTML = renderChangeBanner(state);
  }

  // task flow (main view)
  els.taskFlow.innerHTML = renderTaskFlow(
    state,
    state.highlightChanges && state.highlightedLines.size > 0 ? state.highlightedLines : null,
  );

  // detection banner (project.detect)
  if (state.detectedProject && !state.projectId) {
    els.changeBanner.classList.remove("hidden");
    els.changeBanner.innerHTML = `<div class="sidebar-change-banner" style="border-color:var(--ma-accent);background:color-mix(in srgb, var(--ma-accent) 8%, var(--ma-surface));">
      <div class="sidebar-change-banner-title">检测到项目目录</div>
      <div class="sidebar-change-banner-changes">${escapeHtml(state.detectedProject.reason)}</div>
      <div class="sidebar-change-banner-actions">
        <button type="button" class="unified-btn unified-btn-accent" data-action="detect-switch" data-project-id="${escapeHtml(state.detectedProject.id)}">切换为项目</button>
        <button type="button" class="unified-btn" data-action="detect-dismiss">忽略</button>
      </div>
    </div>`;
    // Don't render the normal change banner when detection is active
  } else {
    // change banner
    els.changeBanner.innerHTML = renderChangeBanner(state);
  }

  // icon bar: verify only visible in confirmed
  const verifyBtn = els.iconBar.querySelector<HTMLElement>("#icon-btn-verify");
  if (verifyBtn) {
    verifyBtn.style.display = state.planStatus === "confirmed" ? "" : "none";
  }
  // project count badge
  const projectBadge = els.iconBar.querySelector<HTMLElement>("#project-count-badge");
  if (projectBadge) {
    projectBadge.textContent = String(state.projects.length);
  }
  // active icon
  for (const btn of els.iconBar.querySelectorAll<HTMLButtonElement>(".sidebar-icon-btn")) {
    const panel = btn.dataset.panel as OverlayPanel;
    btn.classList.toggle("is-active", panel === "tasks" && !state.overlayPanel);
  }

  // overlay panel
  if (state.overlayPanel) {
    els.overlayPanel.classList.remove("hidden");
    els.overlayTitle.textContent = overlayTitle(state.overlayPanel);
    els.overlayBody.innerHTML = renderOverlayBody(state);
  } else {
    els.overlayPanel.classList.add("hidden");
  }

  // -- compat update: handle plan overlay / switch overlay in new style --
  updateCompatElements(els, state, callbacks);
}

// ---- compat bridge: wire up old element refs hidden in DOM ----

function updateCompatElements(
  els: ReturnType<typeof setupProjectPanel>,
  state: ProjectPanelState,
  callbacks: ProjectPanelCallbacks,
): void {
  // Plan card (hidden, used by planOverlay logic in index.ts)
  if (state.planOverlay && state.planStatus !== "confirmed") {
    els.planCard.classList.remove("hidden");
    els.planTitle.textContent = state.planOverlay.title;
    els.planPreview.textContent = state.planOverlay.tasksPreview || state.planOverlay.summary;
  } else if (state.planStatus === "draft" || state.planStatus === "plan_dirty") {
    els.planCard.classList.remove("hidden");
    els.planTitle.textContent =
      state.planStatus === "plan_dirty" ? "计划已变更 · 请确认" : "计划待确认";
    els.planPreview.textContent = state.tasksMarkdown.slice(0, 1200) || "（等待助手生成 TASKS.md）";
  } else {
    els.planCard.classList.add("hidden");
  }

  // Switch card (hidden, used by switchOverlay logic in index.ts)
  if (state.switchOverlay) {
    els.switchCard.classList.remove("hidden");
    els.switchTitle.textContent = `切换到 · ${state.switchOverlay.projectId}`;
    els.switchMessage.textContent = state.switchOverlay.message;
  } else {
    els.switchCard.classList.add("hidden");
    els.switchMessage.textContent = "";
  }

  // Verify card (hidden, overlay handles this now)
  els.verifyCard.classList.add("hidden");

  // Markdown panels (hidden, task flow + overlay handle these)
  els.tasksPanel.classList.add("hidden");
  els.mapPanel.classList.add("hidden");

  // Sidebar tabs (hidden, icon bar replaces them)
  els.sidebarTabs.classList.add("hidden");
}
