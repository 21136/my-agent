export type LlmModelListItem = {
  id: string;
  name: string;
  vendor: string;
  tier: string;
  max_input_tokens: number;
  supports_tool_call: boolean;
  supports_image_input?: boolean;
  configured: boolean;
};

export type LlmKeySlot = {
  env: string;
  label: string;
  models: string[];
  configured: boolean;
  masked: string | null;
  source: "env" | "file" | null;
};

export type TurnEvidenceTestStatus = "passed" | "failed" | "blocked" | "timeout";

export interface TurnEvidenceTestFailure {
  file?: string;
  line?: number;
  test?: string;
}

export interface TurnEvidenceTestSummary {
  status: TurnEvidenceTestStatus;
  suite?: string;
  duration_ms?: number;
  reason?: string;
  failed: TurnEvidenceTestFailure[];
}

export interface TurnEvidenceItem {
  tool: string;
  ok: boolean;
  call_id?: string;
  paths?: string[];
  test?: TurnEvidenceTestSummary;
  terminal_session_id?: string;
  terminal_state?: string;
  diff_summary?: string;
}

export type ExecutionStateName =
  | "idle"
  | "queued"
  | "running"
  | "stopping"
  | "settled"
  | "paused"
  | "failed";

export interface ProjectArtifactSummary {
  path: string;
  role: string;
  revision: string;
  status: string;
  completeness?: "skeleton" | "draft" | "complete" | string;
  ids?: string[];
}

export interface ReleaseAcceptanceSummary {
  accepted: boolean;
  accepted_at: string | null;
  release_revision: string | null;
  checklist: Record<string, boolean>;
}

export interface CompanionWorldContextPayload {
  version: 1;
  sceneId: string;
  activityId: string;
  personalArc: { id: string; stage: number };
  events: Array<{ lane: string; origin: string; title: string; body: string; sourceId?: string }>;
}

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
      llm_model?: string;
      llm_model_label?: string;
      llm_model_override?: boolean;
      write_scope_root?: string;
      write_scope_label?: string;
      execution_lane?: SidecarLane;
    }
  | {
      type: "session.models";
      models: LlmModelListItem[];
      default_flash_id: string;
      default_pro_id: string;
    }
  | {
      type: "llm_keys.state";
      keys: LlmKeySlot[];
    }
  | { type: "llm_keys.updated" }
  | {
      type: "session.history";
      session_id?: string;
      project_id?: string | null;
      items: Array<{ role: "user" | "assistant"; text: string; inline_images?: string[] }>;
      truncated?: boolean;
      total_items?: number;
      omitted_count?: number;
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
  | { type: "turn.start"; intent: string; intent_label: string; run_id?: string }
  | {
      type: "execution.state";
      run_id: string;
      mode?: string;
      state: Exclude<ExecutionStateName, "idle">;
      sequence: number;
      cancel_requested?: boolean;
      cancel_reason?: string | null;
      finish_reason?: string | null;
      ok?: boolean | null;
      started_at?: number | null;
      ended_at?: number | null;
    }
  | { type: "llm.pending"; run_id?: string }
  | {
      type: "llm.usage";
      prompt_tokens: number;
      cached_tokens: number;
      cache_ratio: number;
      completion_tokens?: number;
    }
  | {
      type: "turn.notice";
      level?: "info" | "warn";
      text: string;
      run_id?: string;
    }
  | { type: "checker.verdict"; tool_name: string; verdict: "pass" | "fail" | "warn" }
  | { type: "turn.end"; ok: boolean; finish_reason: string; run_id?: string }
  | { type: "evolve.proposals"; items: ProposalItem[] }
  | { type: "assistant.delta"; text: string; run_id?: string }
  | { type: "assistant.done"; text: string; run_id?: string }
  | { type: "reasoning.delta"; text: string; run_id?: string }
  | { type: "notice"; text: string }
  | { type: "error"; message: string }
  | { type: "confirm.request"; request_id: string; preview: string; allow_approve_all: boolean; run_id?: string }
  | { type: "confirm.done"; request_id: string; choice: string; run_id?: string }
  | { type: "activity.update"; text: string; run_id?: string }
  | { type: "tool.start"; tool: string; call_id: string; summary: string; run_id?: string }
  | {
      type: "tool.end";
      tool: string;
      call_id: string;
      ok: boolean;
      summary: string;
      /** "absent" — a read found the path does not exist yet. Not a failure. */
      note?: string;
      path?: string;
      output_path?: string;
      /** Agent-relative image paths for Desktop inline preview (CHAT-IMAGE-DISPLAY). */
      display_paths?: string[];
      logs_tail?: string;
      run_id?: string;
    }
  | {
      type: "tool.progress";
      call_id: string;
      text?: string;
      pct?: number;
      phase?: string;
      elapsed_sec?: number;
      run_id?: string;
    }
  | {
      type: "turn.evidence";
      run_id?: string;
      total_count?: number;
      omitted_count?: number;
      items: TurnEvidenceItem[];
      gate_notice?: string | null;
      reliability?: {
        postcondition?: "ok" | "fail" | "none" | "blocked" | string;
        circuit_open?: string[];
        playbook_id?: string | null;
        failure_class?: string | null;
      };
    }
  | {
      type: "services.state";
      ok?: boolean;
      services: ServiceListItem[];
    }
  | { type: "prompt.request"; prompt: string }
  | {
      type: "session.list";
      sessions: Array<{
        session_id: string;
        title: string;
        preview?: string;
        updated_at: string;
        message_count?: number;
        project_id?: string;
        execution_lane?: SidecarLane;
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
      project_display_name?: string | null;
      project_directory?: string | null;
      git_branch?: string;
      plan_status: string;
      project_entry?: string;
      project_template?: string;
      map_markdown: string;
      project_summary: string;
      acceptance_command: string | null;
      acceptance_expected_exit: number | null;
      can_verify: boolean;
      scope_confirmed_at?: string | null;
      delivery_profile?: string;
      review_verdict?: string | null;
      review_blockers_count?: number;
      review_progress_blocked?: boolean;
      workflow_stage?: "requirements" | "documentation" | "design" | "implementation" | "verification" | "release";
      needs_documentation?: boolean;
      needs_design_confirm?: boolean;
      design_confirmed_at?: string | null;
      execution_stage?: "requirements" | "documentation" | "design" | "implementation" | "verification" | "release";
      execution_stage_status?: "in_progress" | "blocked" | "ready" | string;
      execution_stage_reason?: string;
      execution_stage_blockers?: string[];
      execution_stage_missing?: string[];
      execution_stage_warnings?: string[];
      execution_stage_affected?: string[];
      execution_stage_deferred?: string[];
      content_lint?: Record<string, unknown> | null;
      execution_stage_artifacts?: Array<{
        path: string;
        role: string;
        revision: string;
        status: string;
        completeness?: "skeleton" | "draft" | "complete" | string;
        ids?: string[];
      }>;
      release_acceptance?: ReleaseAcceptanceSummary;
    }
  | {
      type: "project.import.scan";
      scan: {
        external_root: string;
        project_id: string;
        logical_root: string;
        markers: string[];
        has_git: boolean;
        git_dirty: boolean | null;
        has_verify: boolean;
        has_readme: boolean;
        has_env: boolean;
        has_build_manifest: boolean;
        requires_confirmation: boolean;
      };
    }
  | { type: "project.import.applied"; project_id: string; message: string }
  | { type: "project.import.cancelled"; project_id: string | null }
  | {
      type: "project.list";
      projects: Array<{
        id: string;
        root: string;
        session_id: string | null;
        is_current: boolean;
        external_adopted?: boolean;
        external_healthy?: boolean;
      }>;
    }
  | { type: "project.release.accepted"; release_acceptance: ReleaseAcceptanceSummary }
  | { type: "review.subagent.start"; task_preview: string; call_id?: string }
  | {
      type: "review.subagent.done";
      summary?: string;
      summary_preview?: string;
      verdict?: string | null;
      blockers_count?: number;
      ok?: boolean;
      call_id?: string;
      paths_cited?: string[];
    }
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
  | { type: "project.doc.write.done"; path: string; name: string; size: number }
  | { type: "project.doc.rename.done"; path: string; old_path: string; name: string }
  | { type: "project.doc.delete.done"; path: string; name: string; is_standard?: boolean }
  | { type: "project.undo.available"; description: string }
  | { type: "project.detect"; project_id: string; reason: string; file_count: number }
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
      type: "project.threads";
      project_id: string | null;
      active_session_id: string | null;
      archived_session_ids: string[];
      threads: Array<{
        session_id: string;
        title: string;
        preview?: string;
        updated_at?: string;
        archived: boolean;
      }>;
      is_active: boolean;
    }
  | {
      type: "project.thread.new.done";
      request_id: string;
      project_id: string | null;
      session_id: string;
      previous_session_id: string;
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
  | { type: "file.staged"; items: StagedFileItem[] }
  | { type: "file.unstaged"; attachment_id: string }
  | { type: "file.error"; message: string; path?: string }
  | {
      type: "services.list.done";
      ok: boolean;
      services: ServiceListItem[];
    }
  | {
      type: "services.logs.done";
      ok: boolean;
      name: string;
      log_path?: string;
      text: string;
    }
  | {
      type: "terminal.list.done";
      ok: boolean;
      sessions: TerminalSessionItem[];
      request_id?: string;
      error?: string;
    }
  | {
      type: "terminal.output.done";
      ok: boolean;
      session_id: string;
      request_id?: string;
      output: string;
      cursor: number;
      next_cursor: number;
      cursor_reset?: boolean;
      truncated?: boolean;
      session?: TerminalSessionItem;
      error?: string;
    }
  | {
      type: "terminal.close.done";
      ok: boolean;
      session_id: string;
      request_id?: string;
      state?: string;
      session?: TerminalSessionItem;
      error?: string;
    };

export type ServiceListItem = {
  name: string;
  alive: boolean;
  status?: string | null;
  pid?: number | null;
  cwd?: string | null;
  command?: string | null;
  ready_port?: number | null;
};

export type TerminalSessionItem = {
  session_id: string;
  command: string;
  cwd: string;
  state: string;
  alive: boolean;
  exit_code: number | null;
  signal: string | null;
  reason: string | null;
  created_at: string;
  last_activity_at: string;
  output_bytes: number;
  output_start: number;
};

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
  image_input?: boolean;
  vision_input?: boolean;
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
  mtime?: number;
  recently_changed?: boolean;
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

export type SidecarLane = "stable" | "sandbox";

export type SessionListItem = {
  session_id: string;
  title: string;
  preview?: string;
  updated_at: string;
  message_count?: number;
  project_id?: string;
  execution_lane?: SidecarLane;
};

/** Stable + sandbox trees often share copied session ids; show one row per id. */
export function mergeSessionLists(
  stable: SessionListItem[],
  sandbox: SessionListItem[],
): SessionListItem[] {
  const byId = new Map<string, SessionListItem>();
  for (const item of stable) {
    byId.set(item.session_id, { ...item, execution_lane: item.execution_lane ?? "stable" });
  }
  for (const item of sandbox) {
    const laneItem = { ...item, execution_lane: item.execution_lane ?? "sandbox" };
    const prev = byId.get(item.session_id);
    if (!prev) {
      byId.set(item.session_id, laneItem);
      continue;
    }
    const nextAt = laneItem.updated_at || "";
    const prevAt = prev.updated_at || "";
    if (nextAt > prevAt) {
      byId.set(item.session_id, laneItem);
    }
  }
  const merged = Array.from(byId.values());
  merged.sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""));
  return merged.slice(0, 50);
}

export class AgentWsClient {
  private ws: WebSocket | null = null;
  private handlers = new Set<EventHandler>();
  private reconnectTimer: number | null = null;
  private lane: SidecarLane = "stable";
  private sessionListMergedInflight: Promise<SessionListItem[]> | null = null;
  private stableSessionsCache: SessionListItem[] = [];
  private sandboxSessionsCache: SessionListItem[] = [];
  constructor(
    private host: string,
    private port: number,
  ) {}

  getLane(): SidecarLane {
    return this.lane;
  }

  private async resolveSidecarInfo(lane: SidecarLane): Promise<{ host: string; port: number }> {
    const api = window.myAgentDesktop;
    if (!api) {
      throw new Error("myAgentDesktop preload API missing");
    }
    if (lane === "sandbox") {
      const ensured = await api.ensureSandboxSidecar?.();
      if (!ensured?.ok) {
        throw new Error(ensured?.message || "sandbox sidecar unavailable");
      }
      const info = await api.getSandboxSidecar?.();
      if (!info) {
        throw new Error("sandbox sidecar not ready");
      }
      return info;
    }
    const info = await api.getSidecar();
    if (!info) {
      throw new Error("stable sidecar not ready");
    }
    return info;
  }

  async ensureLane(lane: SidecarLane): Promise<void> {
    if (this.lane === lane && this.ws?.readyState === WebSocket.OPEN) {
      return;
    }
    await this.switchLane(lane);
  }

  async switchLane(lane: SidecarLane): Promise<void> {
    const info = await this.resolveSidecarInfo(lane);
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
    }
    this.lane = lane;
    this.host = info.host;
    this.port = info.port;
    await this.connect();
  }

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
        // Rehydrate persistent terminal visibility after the initial connect
        // and every reconnect; the unified shell also requests an explicit
        // snapshot after its event handler is mounted.
        this.send({ type: "terminal.list" });
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
      const info = await this.resolveSidecarInfo(this.lane);
      this.host = info.host;
      this.port = info.port;
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

  sendMessage(
    text: string,
    attachmentIds?: string[],
    opts?: {
      forceAgent?: boolean;
      activeDoc?: string;
      activeDocSelection?: string;
      turnMode?: "agent" | "ask";
      companionWorldContext?: CompanionWorldContextPayload;
    },
  ): void {
    const payload: Record<string, unknown> = {
      type: "user.message",
      text,
    };
    if (attachmentIds?.length) {
      payload.attachments = attachmentIds;
    }
    if (opts?.forceAgent) payload.force_agent = true;
    if (opts?.activeDoc) payload.active_doc = opts.activeDoc;
    if (opts?.activeDocSelection) payload.active_doc_selection = opts.activeDocSelection;
    if (opts?.turnMode === "agent" || opts?.turnMode === "ask") {
      payload.turn_mode = opts.turnMode;
    }
    if (opts?.companionWorldContext) {
      payload.companion_world_context = opts.companionWorldContext;
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

  async openSession(sessionId: string, lane: SidecarLane = "stable"): Promise<void> {
    await this.ensureLane(lane);
    this.send({ type: "session.open", session_id: sessionId });
  }

  /** One-shot WS so listing never tears down the active chat connection. */
  private fetchSessionListEphemeral(lane: SidecarLane, timeoutMs = 12_000): Promise<SessionListItem[]> {
    return new Promise((resolve, reject) => {
      void this.resolveSidecarInfo(lane)
        .then((info) => {
          const url = `ws://${info.host}:${info.port}`;
          const ws = new WebSocket(url);
          let settled = false;
          const finish = (fn: () => void) => {
            if (settled) return;
            settled = true;
            window.clearTimeout(timeout);
            try {
              ws.close();
            } catch {
              /* ignore */
            }
            fn();
          };
          const timeout = window.setTimeout(() => {
            finish(() => reject(new Error(`session.list timeout (${lane})`)));
          }, timeoutMs);
          ws.addEventListener("open", () => {
            ws.send(JSON.stringify({ type: "session.list" }));
          });
          ws.addEventListener("message", (ev) => {
            try {
              const data = JSON.parse(String(ev.data)) as {
                type?: string;
                sessions?: SessionListItem[];
              };
              if (data?.type !== "session.list" || !Array.isArray(data.sessions)) {
                return;
              }
              finish(() => {
                resolve(
                  data.sessions.map((session) => ({
                    ...session,
                    execution_lane: session.execution_lane ?? lane,
                  })),
                );
              });
            } catch {
              /* wait for a valid frame */
            }
          });
          ws.addEventListener("error", () => {
            finish(() => reject(new Error(`WebSocket error: ${url}`)));
          });
          ws.addEventListener("close", () => {
            finish(() => reject(new Error(`WebSocket closed before session.list (${lane})`)));
          });
        })
        .catch(reject);
    });
  }

  applySessionListPush(
    sessions: Array<{
      session_id: string;
      title: string;
      preview?: string;
      updated_at: string;
      message_count?: number;
      project_id?: string;
      execution_lane?: SidecarLane;
    }>,
  ): SessionListItem[] {
    const mapped: SessionListItem[] = sessions.map((session) => ({
      session_id: session.session_id,
      title: session.title,
      preview: session.preview,
      updated_at: session.updated_at,
      message_count: session.message_count,
      project_id: session.project_id,
      execution_lane: session.execution_lane ?? this.lane,
    }));
    if (this.lane === "sandbox") {
      this.sandboxSessionsCache = mapped;
    } else {
      this.stableSessionsCache = mapped;
    }
    return mergeSessionLists(this.stableSessionsCache, this.sandboxSessionsCache);
  }

  async listSessionsMerged(): Promise<SessionListItem[]> {
    if (this.sessionListMergedInflight) {
      return this.sessionListMergedInflight;
    }
    const run = async (): Promise<SessionListItem[]> => {
      const [stableFetched, sandboxFetched] = await Promise.all([
        this.fetchSessionListEphemeral("stable").catch(() => null),
        this.fetchSessionListEphemeral("sandbox", 8_000).catch(() => null),
      ]);
      if (stableFetched) {
        this.stableSessionsCache = stableFetched;
      }
      if (sandboxFetched) {
        this.sandboxSessionsCache = sandboxFetched;
      }
      return mergeSessionLists(this.stableSessionsCache, this.sandboxSessionsCache);
    };
    this.sessionListMergedInflight = run().finally(() => {
      this.sessionListMergedInflight = null;
    });
    return this.sessionListMergedInflight;
  }

  async createSandboxSession(): Promise<void> {
    await this.ensureLane("sandbox");
    this.send({ type: "session.create_sandbox" });
  }

  refreshSession(): void {
    this.send({ type: "session.refresh" });
  }

  setSessionModel(model: string): void {
    this.send({ type: "session.set_model", model });
  }

  listModels(): void {
    this.send({ type: "session.models" });
  }

  listLlmKeys(): void {
    this.send({ type: "llm_keys.list" });
  }

  setLlmKey(env: string, value: string): void {
    this.send({ type: "llm_keys.set", env, value });
  }

  clearLlmKey(env: string): void {
    this.send({ type: "llm_keys.clear", env });
  }

  refreshProject(): void {
    this.send({ type: "project.state" });
  }

  listProjects(): void {
    this.send({ type: "project.list" });
  }

  scanExternalProject(path: string, projectId?: string): void {
    this.send({ type: "project.import.scan", path, project_id: projectId });
  }

  applyExternalProject(path: string, projectId: string, confirm = true): void {
    this.send({ type: "project.import.apply", path, project_id: projectId, confirm });
  }

  cancelExternalProject(projectId?: string): void {
    this.send({ type: "project.import.cancel", project_id: projectId });
  }

  openProject(projectId: string): void {
    this.send({ type: "project.open", project_id: projectId });
  }

  sendContextSwitchResponse(requestId: string, choice: "y" | "n"): void {
    this.send({ type: "context.switch.response", request_id: requestId, choice });
  }

  startDirectImplement(): void {
    this.send({ type: "project.direct_implement" });
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

  writeDoc(
    path: string,
    content: string,
    opts?: { baseMtime?: number; baseHash?: string; force?: boolean },
  ): void {
    const payload: Record<string, unknown> = { type: "project.doc.write", path, content };
    if (opts?.baseMtime != null) payload.base_mtime = opts.baseMtime;
    if (opts?.baseHash) payload.base_hash = opts.baseHash;
    if (opts?.force) payload.force = true;
    this.send(payload);
  }

  undoDoc(payload: Record<string, unknown>): void {
    this.send({ type: "project.doc.undo", undo_payload: payload });
  }

  renameDoc(path: string, opts: { title?: string; newPath?: string }): void {
    this.send({
      type: "project.doc.rename",
      path,
      title: opts.title ?? "",
      new_path: opts.newPath ?? "",
    });
  }

  deleteDoc(path: string): void {
    this.send({ type: "project.doc.delete", path });
  }

  confirmProjectScope(): void {
    this.send({ type: "project.scope.confirm" });
  }

  confirmProjectDesign(): void {
    this.send({ type: "project.design.confirm" });
  }

  acceptProjectRelease(): void {
    this.send({ type: "project.release.accept" });
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

  listProjectThreads(projectId?: string): void {
    this.send({
      type: "project.threads",
      ...(projectId ? { project_id: projectId } : {}),
    });
  }

  newProjectThread(projectId?: string): void {
    this.send({
      type: "project.thread.new",
      ...(projectId ? { project_id: projectId } : {}),
    });
  }

  listHostScope(): void {
    this.send({ type: "host_scope.list" });
  }

  setTurnMode(mode: "agent" | "ask"): void {
    this.send({ type: "session.set_turn_mode", mode });
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

  /** Phase 27 — project sidebar Services panel (read-only). */
  listServices(): void {
    this.send({ type: "services.list" });
  }

  fetchServiceLogs(name: string, tailLines = 40): void {
    this.send({ type: "services.logs", name, tail_lines: tailLines });
  }

  stopService(name: string): void {
    this.send({ type: "services.stop", name });
  }

  pruneServices(name?: string): void {
    this.send({
      type: "services.prune",
      ...(name ? { name } : {}),
    });
  }

  /** Persistent interactive terminal session visibility and controls. */
  listTerminals(requestId?: string): void {
    this.send({
      type: "terminal.list",
      ...(requestId ? { request_id: requestId } : {}),
    });
  }

  pruneTerminals(includeEnded = true, requestId?: string): void {
    this.send({
      type: "terminal.prune",
      include_ended: includeEnded,
      ...(requestId ? { request_id: requestId } : {}),
    });
  }

  readTerminalOutput(
    sessionId: string,
    cursor = 0,
    maxChars = 16_384,
    requestId?: string,
  ): void {
    this.send({
      type: "terminal.output",
      session_id: sessionId,
      cursor,
      max_chars: maxChars,
      ...(requestId ? { request_id: requestId } : {}),
    });
  }

  closeTerminal(
    sessionId: string,
    requestId?: string,
    force = false,
    options?: { userInitiated?: boolean; dismissIfInactive?: boolean },
  ): void {
    this.send({
      type: "terminal.close",
      session_id: sessionId,
      force,
      ...(options?.userInitiated ? { user_initiated: true } : {}),
      ...(options?.dismissIfInactive ? { dismiss_if_inactive: true } : {}),
      ...(requestId ? { request_id: requestId } : {}),
    });
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
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    try {
      await client.connect();
      return client;
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  }
  await client.connect();
  return client;
}

declare global {
  interface Window {
    myAgentDesktop?: {
      getSidecar: () => Promise<{ host: string; port: number } | null>;
      getSandboxSidecar?: () => Promise<{ host: string; port: number } | null>;
      ensureSandboxSidecar?: () => Promise<{ ok: boolean; host?: string; port?: number; message?: string }>;
      switchSidecarCandidate?: (root: string) => Promise<Record<string, unknown>>;
      switchToCli: () => Promise<void>;
      openWorkbench?: () => Promise<void>;
      openExternal?: (url: string) => Promise<boolean>;
      openBilibiliSearch?: (query: string, bounds: { x: number; y: number; width: number; height: number }) => Promise<boolean>;
      setBilibiliViewState?: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => Promise<boolean>;
      getShellDevPreviewUrl?: () => Promise<string>;
      getShellDevMainUrl?: () => Promise<string>;
      promoteShellDevPreview?: () => Promise<{ ok: boolean; mainUrl?: string; previewUrl?: string; message?: string }>;
      getSandboxAdoptStatus?: () => Promise<Record<string, unknown>>;
      syncSandboxWorktree?: () => Promise<{ ok: boolean; message: string; detail?: string }>;
      resetShellDevMainLane?: () => Promise<{ ok: boolean; message: string; mainUrl?: string }>;
      mergeSandboxIntoStable?: (options?: {
        autoCommitSandbox?: boolean;
        sandboxCommitMessage?: string;
        autoStashStable?: boolean;
      }) => Promise<{ ok: boolean; message: string; detail?: string; steps?: string[] }>;
      openShellDevPreview?: (bounds: { x: number; y: number; width: number; height: number }) => Promise<{
        ok: boolean;
        url?: string;
        configuredUrl?: string;
        fallbackToMain?: boolean;
        message?: string;
      }>;
      openShellDevPopout?: () => Promise<{
        ok: boolean;
        url?: string;
        configuredUrl?: string;
        fallbackToMain?: boolean;
        message?: string;
      }>;
      reloadShellDevPreview?: () => Promise<boolean>;
      setShellDevViewState?: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => Promise<boolean>;
      onSessionControl?: (handler: (action: "suspend" | "resume") => void) => () => void;
      pickDirectory: () => Promise<string | null>;
      getDownloadsPath: () => Promise<string>;
      getDesktopPath: () => Promise<string>;
      getPathForFile?: (file: File) => string;
      writeTempStagingFile?: (name: string, data: Uint8Array) => Promise<string>;
      readClipboardImageToTemp?: () => Promise<string | null>;
      readAgentImageDataUrl?: (agentRelativePath: string) => Promise<string | null>;
      readConstellation: () => Promise<{ version: 1; stars: unknown[]; links: unknown[] }>;
      writeConstellation: (payload: { version: 1; stars: unknown[]; links: unknown[] }) => Promise<boolean>;
      clearConstellation: () => Promise<boolean>;
    };
  }
}
