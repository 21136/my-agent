export type ServerEvent =
  | {
      type: "session.banner";
      session_id: string;
      goal?: string | null;
      topics: string[];
      turn_mode: string;
      turn_mode_label: string;
      phase: string;
      active_shell?: string;
      project_id?: string;
      project_root?: string | null;
      project_plan_status?: string;
      project_plan_label?: string;
      project_tasks_done?: number;
      project_tasks_total?: number;
    }
  | {
      type: "session.history";
      items: Array<{ role: "user" | "assistant"; text: string }>;
    }
  | {
      type: "session.memory";
      message_count: number;
      memory_mode: "full" | "compact";
      memory_mode_label: string;
      digest_sections?: number;
      keep_turns?: number;
      token_usage?: number;
      token_limit?: number;
    }
  | { type: "turn.start"; intent: string; intent_label: string }
  | { type: "turn.notice"; level?: "info" | "warn"; text: string }
  | { type: "checker.verdict"; tool_name: string; verdict: "pass" | "fail" | "warn" }
  | { type: "turn.end"; ok: boolean; finish_reason: string }
  | { type: "evolve.proposals"; items: ProposalItem[] }
  | { type: "assistant.delta"; text: string }
  | { type: "assistant.done"; text: string }
  | { type: "reasoning.delta"; text: string }
  | { type: "notice"; text: string }
  | { type: "error"; message: string }
  | { type: "confirm.request"; request_id: string; preview: string; allow_approve_all: boolean }
  | { type: "confirm.done"; request_id: string; choice: string }
  | { type: "tool.start"; tool: string; call_id: string; summary: string }
  | { type: "tool.end"; tool: string; call_id: string; ok: boolean; summary: string; output_path?: string }
  | { type: "prompt.request"; prompt: string }
  | {
      type: "session.list";
      sessions: Array<{
        session_id: string;
        title: string;
        updated_at: string;
      }>;
    }
  | {
      type: "host_scope.state" | "host_scope.updated";
      roots: HostScopeRoot[];
      wizard_suggested: boolean;
    }
  | {
      type: "project.state";
      project_id: string | null;
      project_root: string | null;
      plan_status: string;
      tasks_markdown: string;
      map_markdown: string;
      tasks_done: number;
      tasks_total: number;
      tasks_open: number;
      tasks_all_done: boolean;
      project_summary: string;
      needs_plan_confirm: boolean;
      acceptance_command: string | null;
      acceptance_expected_exit: number | null;
      can_verify: boolean;
    }
  | {
      type: "project.list";
      projects: Array<{
        id: string;
        root: string;
        tasks_done: number;
        tasks_total: number;
        session_id: string | null;
        is_current: boolean;
      }>;
    }
  | {
      type: "plan.request";
      request_id: string;
      project_id: string;
      project_root: string | null;
      plan_status: string;
      title: string;
      summary: string;
      tasks_preview: string;
      tasks_done: number;
      tasks_total: number;
    }
  | { type: "plan.done"; request_id: string; choice: string | null }
  | {
      type: "project.task.toggle.done";
      line: number;
      done: boolean;
      tasks_done: number;
      tasks_total: number;
    }
  | { type: "project.task.toggle.error"; line: number; message: string }
  | { type: "project.task.reorder.done"; line: number; direction: string; tasks_done: number; tasks_total: number }
  | { type: "project.task.reorder.error"; line: number; message: string }
  | { type: "project.task.drop.done"; line: number; removed: string; tasks_done: number; tasks_total: number }
  | { type: "project.task.drop.error"; line: number; message: string }
  | { type: "project.task.skip.done"; line: number; new_position: number; tasks_done: number; tasks_total: number }
  | { type: "project.task.skip.error"; line: number; message: string }
  // project.plan.* events
  | {
      type: "project.plan.state";
      project_id: string;
      plan_status: string;
      tasks_markdown: string;
      map_markdown: string;
      tasks_done: number;
      tasks_total: number;
      tasks_open: number;
      tasks_all_done: boolean;
      needs_confirm: boolean;
      warnings: string[];
      auto_fix_actions: string[];
      change_log: PlanChangeItem[];
    }
  | { type: "project.plan.confirm_changes.done" }
  | { type: "project.plan.classify.done"; decision: "handle" | "forward" | "split" }
  // project.doc.* events
  | {
      type: "project.doc.list.done";
      docs: ProjectDocItem[];
    }
  | {
      type: "project.doc.read.done";
      path: string;
      content: string;
      size: number;
    }
  | { type: "project.doc.create.done"; path: string; name: string }
  | { type: "project.task.add.done"; line: number; description: string; tasks_done: number; tasks_total: number }
  | { type: "project.undo.available"; description: string }
  | { type: "project.detect"; project_id: string; reason: string; file_count: number; has_tasks: boolean }
  | {
      type: "project.verify.done";
      ok: boolean;
      passed: boolean;
      exit_code?: number;
      expected_exit_code?: number;
      command?: string;
      path?: string;
      stdout?: string;
      stderr?: string;
      error?: string;
    }
  | {
      type: "project.switch.request";
      request_id: string;
      project_id: string;
      current_project_id: string | null;
      action: string;
      target_session_id: string | null;
      message: string;
      needs_confirm: boolean;
    }
  | {
      type: "project.switch.done";
      request_id: string;
      project_id: string;
      session_id: string;
      action: string;
      message: string;
      session_replaced: boolean;
    }
  | {
      type: "context.switch.request";
      request_id: string;
      action: string;
      target: string;
      project_id?: string | null;
      reason?: string;
      title?: string;
      side_effects?: string[];
      current?: {
        shell?: string;
        project_id?: string | null;
        session_id?: string;
      };
    }
  | {
      type: "context.switch.done";
      request_id: string;
      choice: string;
      applied?: boolean;
      action?: string;
      target?: string;
      project_id?: string | null;
      session_id?: string;
      session_replaced?: boolean;
      message?: string;
      shell?: string;
    }
  | {
      type: "file.staged";
      items: StagedFileItem[];
    }
  | { type: "file.unstaged"; attachment_id: string }
  | { type: "file.error"; message: string; path?: string };

/** @deprecated — kept for old shell compatibility; will be removed in Phase 4 */
export type ShellId = string;

export type StagedFileItem = {
  id: string;
  name: string;
  ref: string;
  size: number;
  mime: string;
  readable_text: boolean;
  copied: boolean;
};

export type ProposalItem = {
  proposal_id: string;
  type: string;
  mode: string;
  summary: string;
  target_path?: string | null;
  topics: string[];
};

export type ProjectDocItem = {
  path: string;
  name: string;
  size: number;
  is_standard: boolean;
};

export type PlanChangeItem = {
  id: string;
  kind: string;
  task_text: string;
  reason: string;
  time: string;
  line?: number | null;
};

export type HostScopeRoot = {
  id: string;
  label: string;
  path: string;
  read: boolean;
  write: boolean;
  permissions: string;
  added_at: string;
};

type EventHandler = (event: ServerEvent) => void;

export class AgentWsClient {
  private ws: WebSocket | null = null;
  private handlers = new Set<EventHandler>();
  private reconnectTimer: number | null = null;
  constructor(
    private host: string,
    private port: number,
  ) {}

  connect(): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = `ws://${this.host}:${this.port}`;
      const ws = new WebSocket(url);
      this.ws = ws;

      ws.addEventListener("open", () => {
        if (this.reconnectTimer !== null) {
          window.clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
        resolve();
      });

      ws.addEventListener("message", (ev) => {
        try {
          const data = JSON.parse(String(ev.data)) as ServerEvent;
          if (data && typeof data === "object" && "type" in data) {
            for (const handler of this.handlers) handler(data);
          }
        } catch {
          // ignore malformed frames
        }
      });

      ws.addEventListener("error", () => {
        reject(new Error(`WebSocket error: ${url}`));
      });

      ws.addEventListener("close", () => {
        this.ws = null;
        this.reconnectTimer = window.setTimeout(() => {
          void this.reconnect();
        }, 2000);
      });
    });
  }

  private async reconnect(): Promise<void> {
    try {
      const sidecar = await window.myAgentDesktop?.getSidecar();
      if (sidecar) {
        this.host = sidecar.host;
        this.port = sidecar.port;
      }
      await this.connect();
    } catch {
      this.reconnectTimer = window.setTimeout(() => {
        void this.reconnect();
      }, 2000);
    }
  }

  onEvent(handler: EventHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  /** Tear down WebSocket; used when switching pet ↔ workbench. */
  destroy(): void {
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.handlers.clear();
  }

  /** @deprecated compat for old shells; no-op in unified shell */
  isActiveShell(_shell: string): boolean {
    return true;
  }

  /** @deprecated compat for old shells; no-op in unified shell */
  shellSwitch(_shell: string, _projectId?: string): void {
    // no-op
  }

  send(payload: Record<string, unknown>): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket not connected");
    }
    this.ws.send(JSON.stringify(payload));
  }

  sendMessage(text: string, attachmentIds?: string[]): void {
    const payload: Record<string, unknown> = {
      type: "user.message",
      text,
    };
    if (attachmentIds?.length) {
      payload.attachments = attachmentIds;
    }
    this.send(payload);
  }

  stageFiles(paths: string[], shell?: string): void {
    this.send({
      type: "file.stage",
      paths,
      shell: shell ?? "grow",
    });
  }

  unstageFile(attachmentId: string): void {
    this.send({ type: "file.unstage", attachment_id: attachmentId });
  }

  onFileStaged(handler: (items: StagedFileItem[]) => void): () => void {
    return this.onEvent((event) => {
      if (event.type === "file.staged") handler(event.items);
    });
  }

  onFileUnstaged(handler: (attachmentId: string) => void): () => void {
    return this.onEvent((event) => {
      if (event.type === "file.unstaged") handler(event.attachment_id);
    });
  }

  onFileError(handler: (event: { message: string; path?: string }) => void): () => void {
    return this.onEvent((event) => {
      if (event.type === "file.error") handler({ message: event.message, path: event.path });
    });
  }

  sendCommand(name: string): void {
    this.send({ type: "command", name });
  }

  sendConfirm(requestId: string, choice: "y" | "n" | "a"): void {
    this.send({ type: "confirm.response", request_id: requestId, choice });
  }

  sendTurnCancel(): void {
    this.send({ type: "turn.cancel" });
  }

  acceptProposal(proposalId: string): void {
    this.send({ type: "proposal.accept", proposal_id: proposalId });
  }

  rejectProposal(proposalId: string): void {
    this.send({ type: "proposal.reject", proposal_id: proposalId });
  }

  listSessions(): void {
    this.send({ type: "session.list" });
  }

  openSession(sessionId: string): void {
    this.send({ type: "session.open", session_id: sessionId });
  }

  refreshSession(): void {
    this.send({ type: "session.refresh" });
  }

  refreshProject(): void {
    this.send({ type: "project.state" });
  }

  listProjects(): void {
    this.send({ type: "project.list" });
  }

  openProject(projectId: string): void {
    this.send({ type: "project.open", project_id: projectId });
  }

  sendPlanResponse(requestId: string, choice: "confirm" | "edit"): void {
    this.send({ type: "plan.response", request_id: requestId, choice });
  }

  sendContextSwitchResponse(requestId: string, choice: "y" | "n"): void {
    this.send({ type: "context.switch.response", request_id: requestId, choice });
  }

  toggleTask(line: number, done: boolean): void {
    this.send({ type: "project.task.toggle", line, done });
  }

  // project.plan.* convenience methods
  sendPlanMessage(payload: Record<string, unknown>): void {
    this.send(payload);
  }

  planToggleTask(line: number, done: boolean): void {
    this.sendPlanMessage({ type: "project.plan.toggle_task", line, done });
  }

  planReorderTask(line: number, direction: "up" | "down"): void {
    this.sendPlanMessage({ type: "project.plan.reorder_task", line, direction });
  }

  planDropTask(line: number): void {
    this.sendPlanMessage({ type: "project.plan.drop_task", line });
  }

  planSkipTask(line: number): void {
    this.sendPlanMessage({ type: "project.plan.skip_task", line });
  }

  planConfirmChanges(): void {
    this.sendPlanMessage({ type: "project.plan.confirm_changes" });
  }

  planGetState(): void {
    this.sendPlanMessage({ type: "project.plan.state" });
  }

  listDocs(): void {
    this.send({ type: "project.doc.list" });
  }

  readDoc(path: string): void {
    this.send({ type: "project.doc.read", path });
  }

  createDoc(path: string, content?: string): void {
    this.send({ type: "project.doc.create", path, content: content ?? "" });
  }

  addTask(description: string, phase?: string): void {
    this.send({ type: "project.task.add", description, phase: phase ?? "" });
  }

  undoLastPlanOp(): void {
    this.send({ type: "project.plan.undo" });
  }

  runProjectVerify(): void {
    this.send({ type: "project.verify" });
  }

  switchProject(
    projectId: string,
    options?: { confirm?: boolean; requestId?: string },
  ): void {
    this.send({
      type: "project.switch",
      project_id: projectId,
      confirm: Boolean(options?.confirm),
      request_id: options?.requestId,
    });
  }

  listHostScope(): void {
    this.send({ type: "host_scope.list" });
  }

  addHostScope(payload: {
    host_id: string;
    path: string;
    label?: string;
    write?: boolean;
  }): void {
    this.send({
      type: "host_scope.add",
      host_id: payload.host_id,
      path: payload.path,
      label: payload.label,
      write: Boolean(payload.write),
      read: true,
    });
  }

  removeHostScope(hostId: string): void {
    this.send({ type: "host_scope.remove", host_id: hostId });
  }

  setHostScopeWrite(hostId: string, write: boolean): void {
    this.send({ type: "host_scope.write", host_id: hostId, write });
  }

  repathHostScope(hostId: string, path: string): void {
    this.send({ type: "host_scope.repath", host_id: hostId, path });
  }

  runHostScopeWizard(
    entries: Array<{ host_id: string; path: string; label?: string; write?: boolean }>,
  ): void {
    this.send({ type: "host_scope.wizard", entries });
  }

  skipHostScopeWizard(): void {
    this.send({ type: "host_scope.wizard", skip: true });
  }
}

export async function createWsClient(): Promise<AgentWsClient> {
  const api = window.myAgentDesktop;
  if (!api) {
    throw new Error("myAgentDesktop preload API missing");
  }
  const sidecar = await api.getSidecar();
  if (!sidecar) {
    throw new Error("Python sidecar not ready");
  }
  const client = new AgentWsClient(sidecar.host, sidecar.port);
  await client.connect();
  return client;
}

declare global {
  interface Window {
    myAgentDesktop?: {
      getSidecar: () => Promise<{ host: string; port: number } | null>;
      switchToCli: () => Promise<void>;
      openWorkbench?: () => Promise<void>;
      openPet?: () => Promise<void>;
      petSetIgnoreMouseEvents?: (ignore: boolean) => void;
      petSetBounds?: (mode: "collapsed" | "expanded") => Promise<void>;
      onSessionControl?: (handler: (action: "suspend" | "resume") => void) => () => void;
      pickDirectory: () => Promise<string | null>;
      getDownloadsPath: () => Promise<string>;
      getDesktopPath: () => Promise<string>;
      getPathForFile?: (file: File) => string;
      readConstellation: () => Promise<{ version: 1; stars: unknown[]; links: unknown[] }>;
      writeConstellation: (payload: { version: 1; stars: unknown[]; links: unknown[] }) => Promise<boolean>;
      clearConstellation: () => Promise<boolean>;
    };
  }
}
