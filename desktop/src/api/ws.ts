import type { EvolutionProjection } from "../evolution-state";
import { normalizeEvolutionHostRequest, type EvolutionHostRequest } from "./evolution-bridge";

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

export type LlmRoleModelPreferences = { main: string; subagent: string };
export type ApprovalPreferenceMode = "manual" | "auto" | "full";
type ApprovalPreferencesPayload = {
  status: "ok" | "rejected";
  state_known?: boolean;
  approval_mode: ApprovalPreferenceMode;
  saved_approval_mode: ApprovalPreferenceMode;
  available_modes: ApprovalPreferenceMode[];
  message?: string;
};
export type ApprovalPreferencesEvent =
  | ({ type: "approval_preferences.state" } & ApprovalPreferencesPayload)
  | ({ type: "approval_preferences.done" } & ApprovalPreferencesPayload);
export type LlmRoleModelsEvent = {
  type: "llm_role_models.state" | "llm_role_models.done";
  status: "ok" | "rejected";
  preferences?: LlmRoleModelPreferences;
  explicit_roles?: Array<"main" | "subagent">;
  models?: LlmModelListItem[];
  message?: string;
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

export type WorkCardSourceRef = { kind: string; id: string };
export type ProjectTestSelfReport = { status: "passed"; source: "user_authorized_project_self_report"; pytest_target: string; exit_code: 0 };
export type WorkCardResult = { session_id: string; project_id?: string; item_id: string; requirement_id: string; result_id: string; run_id: string; status: string; summary: string; verification_status: string | null; project_test?: ProjectTestSelfReport | null; sources: WorkCardSourceRef[] };
export type WorkCardRequirement = { session_id: string; project_id?: string; item_id: string; requirement_id: string; text: string; status: string; source_ref: WorkCardSourceRef | null; latest_result: WorkCardResult | null; result_count: number; omitted_result_count: number; current_result_missing: boolean; current_run_id: string | null; latest_result_is_current: boolean | null };
export type WorkCardItem = { session_id: string; project_id: string | null; kind?: "independent"; item_id: string; title: string; status: string; source_refs: WorkCardSourceRef[]; requirements: WorkCardRequirement[] };
export type LinkedItemCopySnapshot = {
  kind: "linked_item_copy"; session_id: string; captured_at: string; copy_id: string; completeness_scope: "item_requirement";
  project_id: string; source_session_id: string; source_item_digest: string; binding_id: string; item_id: string;
  item_title: string; item_status_at_capture: string; requirement_id: string; requirement_text: string;
  requirement_status_at_capture: string; worker_id: string; worker_run_id: string;
  host_start: { reservation_id: string; parent_run_id: string; turn_epoch: number };
  host_lifecycle: { sequence: number; status: string; finish_reason: string };
  operation_result: "unknown"; verification: "not_verified";
};
export type LinkedProjectSessionCopyItem = { item_id: string; title: string; status: string; requirements: Array<{ requirement_id: string; text: string; status: string }> };
export type LinkedProjectSessionWorkerFact = {
  binding_id: string; item_id: string; requirement_id: string; worker_id: string; worker_run_id: string;
  host_start: { reservation_id: string; parent_run_id: string; turn_epoch: number };
  host_lifecycle: { sequence: number; status: string; finish_reason: string };
  operation_result: "unknown"; verification: "not_verified";
};
export type LinkedProjectSessionCopySnapshot = {
  kind: "linked_project_session_copy"; session_id: string; captured_at: string; copy_id: string; binding_id: string;
  source: { scope: "authorized_source_session_only"; mapping_claim: "not_asserted"; project_id: string;
    source_session_id: string; source_revision: number; items: LinkedProjectSessionCopyItem[]; worker_facts: LinkedProjectSessionWorkerFact[] };
};
export type WorkCardSnapshot = { session_id: string; project_id: string | null; kind?: "independent" | "project" | "project_scoped"; item_id?: string; captured_at: string; completeness: "complete"; item_count: number; requirement_count: number; result_count: number; omitted_item_count: number; omitted_requirement_count: number; omitted_result_count: number; incomplete_reason: string | null; persisted: boolean; history_complete: boolean; missing_current_result_count: number | null; card?: WorkCardItem; cards?: WorkCardItem[] } | LinkedItemCopySnapshot | LinkedProjectSessionCopySnapshot;
export type WorkCard = { card_id: string; snapshot: WorkCardSnapshot; saved_at: string; viewed_at: string | null };
export type WorkCardScope = { session_id: string; kind: "independent" } | { session_id: string; project_id: string }
  | { session_id: string; kind: "linked_item_copy"; project_id: string; item_id: string }
  | { session_id: string; kind: "linked_project_session_copy"; project_id: string; mapping_claim: "not_asserted" };
export type WorkCardIndexItem = { card_id: string; scope:
  | {kind: "independent"; item_id: string}
  | {kind: "project"; project_id: string}
  | {kind: "linked_item_copy"; project_id: string; item_id: string}
  | {kind: "linked_project_session_copy"; project_id: string; source_session_id: string; mapping_claim: "not_asserted"};
  saved_at: string; viewed_at: string | null; title: string };
export type WorkCardErrorStatus = "invalid_request" | "session_unavailable" | "project_unbound" | "session_changed" | "request_conflict" | "ledger_unavailable" | "incomplete" | "rejected" | "save_failed" | "save_uncertain" | "not_found" | "invalid_cursor" | "unavailable";
export type WorkCardPresentEvent = { type: "work-card.present"; session_id: string; presentation_id: string; card_id: string; card: WorkCard };
/** Client-to-host idempotent receipt for a merged exact session presentation; distinct from viewed/acknowledged_at. Host binds run_id from the durable Session reference. */
export type WorkCardReceivedMessage = { type: "work-card.received"; session_id: string; presentation_id: string; card_id: string };
export type WorkCardReceivedResultEvent = { type: "work-card.received.done"; status: "received" | "already_received" | "invalid_request" | "session_unavailable" | "project_unbound" | "session_changed" | "history_unavailable" | "rejected" | "unavailable"; message: string; session_id: string; presentation_id: string; card_id: string; received_at?: string };
export type WorkCardReceiptBacklogEvent = { type: "work-card.receipt.backlog"; session_id: string; pending_count: number; deferred_count: number; legacy_untracked_count: number; retry_batch_size: number; batch_limit: number; max_send_attempts: number };

export type WorkCardResultViewStatus = "acknowledged" | "already_acknowledged" | "invalid_request" | "session_unavailable" | "session_changed" | "history_unavailable" | "rejected" | "unavailable";
export type WorkCardSnapshotViewTimeoutEvent = { type: "work-card.snapshot.view.timeout"; session_id: string; presentation_id: string; card_id: string };
export type WorkCardSnapshotViewEvent = { type: "work-card.snapshot.view.done"; status: WorkCardResultViewStatus; message: string; session_id: string; presentation_id: string; card_id: string };
export type LocalAssistantReplyExcerptDetail = { session_id: string; item_id: string; requirement_id: string; result_id: string; label: string; escaped_excerpt_text: string; source_availability: "available" | "unavailable"; result_status: "unknown"; verification_status: "not_verified" };
export type WorkCardResultViewEvent = { type: "work-card.result.view.done"; status: WorkCardResultViewStatus; message: string; session_id: string; presentation_id: string; card_id: string; result_id: string; requirement_id: string; request_id?: string; local_assistant_reply_excerpt_detail?: LocalAssistantReplyExcerptDetail };

export type WorkRecordControlAction = "set_dependencies" | "queue" | "wait_confirmation" | "release_confirmation" | "stop" | "continue" | "cancel_execution" | "revise_requirement" | "set_priority" | "copy_requirement_to_new_item" | "inspect_unknown_claim";
export type WorkRecordControlDoneEvent = { type: "work-record.control.done"; session_id: string; request_id: string; item_id: string; project_id?: string; requirement_id?: string; action: WorkRecordControlAction; status: "applied" | "rejected"; message: string };
export type WorkRecordScheduleDispatchDoneEvent = { type: "work-records.schedule.dispatch.done"; session_id: string; request_id: string; status: "applied" | "rejected"; message: string; tool_ok?: boolean; workers?: unknown[] };
export type OverviewResultState = "no_records" | "visible" | "collapsed" | "cancelled" | "error" | "expired";
export type OverviewReadStatus = "viewed" | "unread" | "unknown";
export type WorkRecordsOverviewPageFacts = { kind: "items" | "requirements"; group_kind: "project" | "independent" | "linked_source_snapshot"; group_id: string; item_id: string | null; offset: number; total: number; has_more: boolean; next_cursor: string | null };
export type WorkRecordsOverviewRunHistory = { run_id: string; result_id: string; status: string; summary: string; verification_status: string; recorded_at: number | null };
export type AuthorizedRequirementHistoryEvent = WorkRecordsOverviewRunHistory & { type: "work-records.overview.result"; session_id: string; request_id: string; state: OverviewResultState; aggregation_scope: "selected_project_sessions"; selected_project_ids: string[]; history_project_id: string; history_item_id: string; history_requirement_id: string; history_page: { page_size: number; offset: number; has_more: boolean; next_cursor: string | null; scope: "this_requirement_only" }; source: { project_id: string; session_id: string; revision: number }; history: WorkRecordsOverviewRunHistory[]; };
export type WorkRecordsOverviewRequirement = { requirement_id: string; text: string; status: string; depends_on?: string[]; depends_on_refs?: Array<{ item_id: string; requirement_id: string }>; history?: WorkRecordsOverviewRunHistory[]; history_page?: { page_size: number; offset: number; has_more: boolean; next_cursor: string | null; scope: "this_requirement_only" }; status_at_capture?: string; worker_facts?: unknown[]; claim_recovery_needed?: boolean };
export type WorkRecordsOverviewCurrentResult = { requirement_id: string; result_id: string; status: string; read_status: OverviewReadStatus };
export type WorkRecordsOverviewItem = { item_id: string; title: string; priority?: number; status: string; status_at_capture?: string; status_counts: Record<string, number>; requirements: WorkRecordsOverviewRequirement[]; current_results: WorkRecordsOverviewCurrentResult[]; omitted_requirement_count?: number; requirements_page?: WorkRecordsOverviewPageFacts };
export type WorkRecordsOverviewSourceSnapshot = { scope: "authorized_source_session_only"; mapping_claim: "not_asserted"; project_id: string; source_session_id: string; source_revision: number; captured_at: string; copy_id: string; intent_state: "pending" | "reference_queued"; card_id: string | null; viewed_at: string | null; superseded_snapshot_count: number; superseded_unviewed_count: number };
export type WorkRecordsOverviewGroup = { id: string; visibility: "visible" | "collapsed"; status: string; status_counts: Record<string, number>; item_count: number; requirement_count: number; items: WorkRecordsOverviewItem[]; items_page?: WorkRecordsOverviewPageFacts | null; source_session_id?: string; source_revision?: number; omitted_item_count?: number } & ({ kind: "project" | "independent" } | { kind: "linked_source_snapshot"; status: "unknown"; source_snapshot: WorkRecordsOverviewSourceSnapshot } & ({ visibility: "visible"; attention_status: "unknown"; attention_reason: "publication_pending" | "captured_requirement_unresolved" | "superseded_snapshot_attention" | "view_evidence_unavailable" | "view_not_confirmed" | "superseded_snapshot_unviewed" | "worker_result_unverified"; visibility_timer_status: "unknown" | "waiting" | "not_viewed" | "unavailable" | "elapsed" } | { visibility: "collapsed"; attention_status: "none"; attention_reason: "elapsed_terminal_capture"; visibility_timer_status: "elapsed" }));
export type WorkRecordsOverviewUnreadResult = { group: [string, string]; item_id: string; requirement_id: string; result_id: string };
export type WorkRecordsSchedulePreview = { state: "visible" | "unavailable"; candidates: Array<{ project_id: string; item_id: string; requirement_id: string; priority: number }>; blocked: Array<{ project_id: string; item_id: string; requirement_id: string; priority: number; reasons: string[] }>; limitations: string[]; omissions?: string[] };
export type WorkRecordsOverviewResultEvent = {
  type: "work-records.overview.result"; session_id: string; request_id: string; state: OverviewResultState; work_record_revision?: number;
  aggregation_scope?: "selected_project_sessions"; selected_project_ids?: string[]; schedule_preview?: WorkRecordsSchedulePreview;
  reason?: string; groups: WorkRecordsOverviewGroup[]; detail_page?: WorkRecordsOverviewPageFacts | null;
  unread_important_results: WorkRecordsOverviewUnreadResult[];
  unread_important_results_total?: number; unread_important_results_truncated?: boolean;
  unread_status: "known" | "unknown"; visibility_limitations: string[];
  visibility_counts: { visible_groups: number; visible_items: number; collapsed_groups: number; collapsed_items: number };
  snapshot_id: string | null;
  history_project_id?: string; history_item_id?: string; history_requirement_id?: string;
  history_scope?: "selected_projects_history"; history_sources?: Array<{ project_id: string; session_id: string; revision: number }>;
  history?: WorkRecordsOverviewRunHistory[];
  history_page?: { page_size: number; offset: number; has_more: boolean; next_cursor: string | null; scope: "this_requirement_only" | "selected_projects_history" };
  source?: { project_id: string; session_id: string; revision: number };
  visibility_recheck_after_ms?: number;
  page: { page_size: number; offset: number; total_groups: number; total_visible_groups: number; total_visible_items: number;
    total_collapsed_groups: number; total_collapsed_items: number; has_more: boolean; next_cursor: string | null };
};
export type WorkCardEvent =
  | WorkCardReceiptBacklogEvent
  | WorkCardReceivedResultEvent
  | WorkCardResultViewEvent
  | WorkCardSnapshotViewEvent
  | WorkCardSnapshotViewTimeoutEvent
  | { type: "work-card.create.done"; status: "saved" | "already_saved"; message: string; request_id: string; card_id: string; card: WorkCard; source_scope: WorkCardScope }
  | { type: "work-card.get.done"; status: "found"; message: string; card_id: string; card: WorkCard; source_scope: WorkCardScope }
  | { type: "work-card.seen.done"; status: "acknowledged" | "already_acknowledged"; message: string; card_id: string; viewed_at: string; source_scope: WorkCardScope }
  | { type: "work-card.list.done"; status: "ok"; message: string; request_id: string; items: WorkCardIndexItem[]; has_more: boolean; next_cursor: string | null; source_scope: { session_id: string } }
  | { type: "work-card.list.done"; status: WorkCardErrorStatus; message: string; request_id: string }
  | {
      type: "work-card.trash.done" | "work-card.purge.done" | "work-card.restore.done";
      status: string;
      message: string;
      request_id: string;
      card_id?: string;
      affected_card_ids?: string[];
      affected_binding_ids?: string[];
      affected_refresh_ids?: string[];
    }
  | { type: "work-card.create.done" | "work-card.get.done" | "work-card.seen.done"; status: WorkCardErrorStatus; message: string; request_id?: string; card_id?: string };

export type AutoRecognitionDoneEvent = { type: "work-record.auto-recognition.done"; session_id: string; request_id: string; action: "enable" | "revoke"; status: "ok" | "rejected"; enabled: boolean };
export type AutoRecognitionStateEvent = { type: "work-record.auto-recognition.state"; session_id: string; enabled: boolean };

export type GoalBudgetState = {
  tool_turns_used: number;
  tool_turns_limit: number;
  wall_seconds_used: number;
  wall_seconds_limit: number;
  repair_count: number;
  repair_limit: number;
};
export type GoalStateEvent = {
  type: "goal.state";
  goal_id: string;
  goal_text: string;
  status: "idle" | "active" | "paused" | "done" | "failed" | "stopped";
  pause_reason: string | null;
  next_step: string;
  budgets: GoalBudgetState;
  can_auto_continue: boolean;
  session_id?: string;
};
export type ProjectWorkerVerificationPayload = { session_id: string; project_id: string; item_id: string; requirement_id: string; project_name: string; requirement_text: string; pytest_target: string; worker_run_id: string; reservation_id: string };

export type ServerEvent =
  | ApprovalPreferencesEvent
  | AutoRecognitionDoneEvent
  | AutoRecognitionStateEvent
  | WorkRecordControlDoneEvent
  | GoalStateEvent
  | LlmRoleModelsEvent
  | { type: "client.connection.closed" }
  | { type: "user.message.persisted"; session_id: string; request_id: string }
  | { type: "chat.start"; request_id: string; work_run_id: string; session_id: string }
  | { type: "chat.delta"; request_id: string; work_run_id: string; session_id: string; text: string }
  | { type: "chat.done"; request_id: string; work_run_id: string; session_id: string; text: string; finish_reason: string }
  | { type: "chat.error"; request_id: string; work_run_id: string; session_id: string; code: string; message: string }
  | { type: "chat.cancel.result"; request_id: string; accepted: boolean }
  | { type: "execution.stop.result"; run_id?: string; accepted: boolean }
  | { type: "work.instruction.state"; request_id: string; run_id: string; session_id: string; state: "queued" | "delivered" | "undelivered"; reason?: string; delivery_stage?: "next_model_request_context" }
  | { type: "companion.orchestration.state"; request_id: string; run_id: string; session_id: string; state: "dispatching" | "active" | "undelivered"; reason?: string }
  | WorkCardPresentEvent
  | WorkCardEvent
  | WorkRecordsOverviewResultEvent
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
      items: Array<
        | { role: "user" | "assistant"; text: string; inline_images?: string[]; inline_videos?: string[]; inline_files?: string[] }
        | { role: "work_card"; type: "work_card_reference"; presentation_id: string; card_id: string; text: string }
      >;
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
  | {
      type: "memory.proposal.accept.request";
      request_id: string;
      proposal_id: string;
      summary?: string;
      target_path?: string | null;
      topics?: string[];
      mode?: string;
      message?: string;
    }
  | {
      type: "memory.proposal.accept.done";
      request_id?: string;
      proposal_id?: string;
      applied?: boolean;
    }
  | { type: "evolve.memories"; items: MemoryItem[] }
  | { type: "evolution.bound"; evolution_id: string; conversation_id: string; host_run_id: string }
  | { type: "evolution.denied"; conversation_id: string }
  | { type: "evolution.state"; evolution_id: string; conversation_id: string; host_run_id: string; projection: EvolutionProjection }
  | { type: "evolution.stop.result"; conversation_id: string; host_run_id: string; result: Record<string, unknown> }
  | { type: "assistant.delta"; text: string; run_id?: string }
  | { type: "assistant.done"; text: string; run_id?: string }
  | { type: "reasoning.delta"; text: string; run_id?: string }
  | { type: "notice"; text: string }
  | { type: "work-record.post-turn.result"; session_id: string; request_id: string; outcome: "recorded" | "already_recorded" | "not_recorded" | "append_not_allowed" | "uncertain"; message?: string }
  | { type: "error"; message: string }
  | { type: "confirm.request"; request_id: string; preview: string; allow_approve_all: boolean; run_id?: string; confirm_kind?: "work_record_post_turn"; confirm_subtype?: "work_record_reply_excerpt" | "work_record_auto_candidate" | "project_worker_requirement_verification"; work_record_candidate?: unknown; project_worker_verification?: unknown; session_id?: string }
  | { type: "confirm.done"; request_id: string; choice: string; run_id?: string; confirm_kind?: "work_record_post_turn"; confirm_subtype?: "work_record_reply_excerpt" | "work_record_auto_candidate" | "project_worker_requirement_verification"; work_record_candidate?: unknown; project_worker_verification?: unknown; session_id?: string }
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
      diff?: string;
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
  | { type: "prompt.request"; request_id: string; run_id: string; session_id: string; prompt: string }
  | { type: "prompt.response.result"; request_id: string; run_id: string; session_id: string; accepted: boolean }
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
      placeholder_count?: number;
    }
  | {
      type: "project.doc.read.done";
      path: string;
      content: string;
      size: number;
      mtime?: number;
      content_hash?: string;
    }
  | { type: "project.doc.create.done"; path: string; name: string }
  | { type: "project.doc.write.done"; path: string; name: string; size: number; mtime?: number; content_hash?: string }
  | { type: "project.doc.write.conflict"; path: string; message: string; mtime: number; content_hash: string; size: number }
  | { type: "project.doc.restore.done"; path: string; name: string }
  | { type: "project.doc.rename.done"; path: string; old_path: string; name: string }
  | { type: "project.doc.delete.done"; path: string; name: string; is_standard?: boolean }
  | { type: "project.undo.available"; description: string; undo_kind?: "restore_doc"; undo_payload?: Record<string, unknown> }
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
      type: "session.restart_today.done";
      request_id: string;
      session_id: string;
      previous_session_id: string;
      segment_id: string;
      message: string;
      session_replaced: boolean;
      journal_notice?: string | null;
    }
  | {
      type: "day_journal.directory";
      days: Array<{
        date: string;
        summary?: string | null;
        projects: string[];
        segment_count: number;
        segments: Array<{
          scope?: string;
          project_id?: string | null;
          date: string;
          segment_id: string;
          summary?: string | null;
          source: { kind: string; session_id: string };
        }>;
      }>;
    }
  | {
      type: "day_journal.read.done";
      status: string;
      records: unknown[];
      issues: unknown[];
      message?: string;
    }
  | {
      type: "day_journal.summary.status";
      ready?: boolean;
      status?: string;
      day?: string;
      project_id?: string;
      source_count?: number;
      message?: string;
      request_id?: string;
    }
  | {
      type: "day_journal.refresh_mechanical.done";
      ok?: boolean;
      updated?: boolean;
      day?: string;
      message?: string;
      request_id?: string;
    }
  | {
      type: "day_journal.suggest_summary.done";
      ok?: boolean;
      saved?: boolean;
      message?: string;
      request_id?: string;
    }
  | {
      type: "day_journal.project_store";
      project_id: string;
      days: Array<{
        date: string;
        segment_count: number;
        segments: Array<{
          segment_id: string;
          session_id: string;
          occurred_at: string;
        }>;
      }>;
    }
  | {
      type: "session.turn_mode.request";
      request_id: string;
      from_mode?: string;
      to_mode: "agent" | "ask";
      message?: string;
    }
  | {
      type: "session.turn_mode.done";
      request_id?: string;
      applied?: boolean;
      to_mode?: string;
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

export type MemoryItem = {
  memory_id: string;
  topics: string[];
  summary: string;
  path: string;
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

export type SessionLaneCapabilities = { legacySessionLanes: boolean };

/** Missing or denied capability never authorizes legacy sidecar enumeration. */
export function isLegacyLaneQueryAllowed(capabilities: SessionLaneCapabilities | null | undefined): boolean {
  return capabilities?.legacySessionLanes === true;
}

/** Stable + sandbox trees can contain copied session ids; keep the newer row. */
export function mergeSessionLists(
  stable: SessionListItem[],
  sandbox: SessionListItem[],
): SessionListItem[] {
  const byId = new Map<string, SessionListItem>();
  for (const item of stable) byId.set(item.session_id, { ...item, execution_lane: item.execution_lane ?? "stable" });
  for (const item of sandbox) {
    const laneItem = { ...item, execution_lane: item.execution_lane ?? "sandbox" };
    const previous = byId.get(item.session_id);
    if (!previous || (laneItem.updated_at || "") > (previous.updated_at || "")) byId.set(item.session_id, laneItem);
  }
  return Array.from(byId.values())
    .sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || ""))
    .slice(0, 50);
}

const WORK_CARD_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const WORK_CARD_ID = /^wc-[0-9a-f]{64}$/;
const WORK_ITEM_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/;
type PendingCard = { session: string; itemId?: string; key: string; resultIdentity?: { presentationId: string; resultId: string; requirementId: string } };
const cardObject = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const nonempty = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const timestamp = (value: unknown): value is string => typeof value === "string" && /^\d{4}-\d\d-\d\dT/.test(value) && Number.isFinite(Date.parse(value)) && /(?:Z|[+-]\d\d:\d\d)$/.test(value);
const cardId = (value: unknown): value is string => typeof value === "string" && WORK_CARD_ID.test(value);
const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
function exactCardKeys(value: Record<string, unknown>, keys: string[]): boolean {
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}
function validCardScope(value: unknown, session: string): boolean {
  if (!cardObject(value) || value.session_id !== session) return false;
  if (value.kind === "independent" || value.kind === "linked_item_copy") {
    return exactCardKeys(value, ["session_id", "kind"]);
  }
  return nonempty(value.project_id) && value.kind === undefined;
}
function validLinkedParentCardScope(
  value: unknown, session: string, snapshot?: LinkedItemCopySnapshot,
): boolean {
  if (!cardObject(value) || value.session_id !== session) return false;
  if (value.kind === "linked_item_copy") {
    return exactCardKeys(value, ["session_id", "kind", "project_id", "item_id"])
      && typeof value.project_id === "string" && WORK_ITEM_ID.test(value.project_id)
      && typeof value.item_id === "string" && WORK_ITEM_ID.test(value.item_id)
      && (!snapshot || (snapshot.session_id === session && value.project_id === snapshot.project_id
        && value.item_id === snapshot.item_id));
  }
  // Legacy server responses use the parent Session's project scope, not the linked source project.
  if (exactCardKeys(value, ["session_id", "project_id"])) {
    return typeof value.project_id === "string"
      && (value.project_id === "" || WORK_ITEM_ID.test(value.project_id));
  }
  return value.kind === "independent" && exactCardKeys(value, ["session_id", "kind"]);
}
function validLinkedProjectSessionCopyScope(
  value: unknown, session: string, snapshot?: LinkedProjectSessionCopySnapshot,
): boolean {
  if (!cardObject(value) || !exactCardKeys(value, ["session_id", "kind", "project_id", "mapping_claim"])
    || value.session_id !== session || value.kind !== "linked_project_session_copy"
    || value.mapping_claim !== "not_asserted" || typeof value.project_id !== "string"
    || !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value.project_id)) return false;
  return !snapshot || (snapshot.session_id === session && value.project_id === snapshot.source.project_id
    && value.mapping_claim === snapshot.source.mapping_claim);
}

function validLinkedItemCopySnapshot(value: unknown, session: string): value is LinkedItemCopySnapshot {
  const keys = ["kind", "session_id", "captured_at", "copy_id", "completeness_scope", "project_id",
    "source_session_id", "source_item_digest", "binding_id", "item_id", "item_title", "item_status_at_capture",
    "requirement_id", "requirement_text", "requirement_status_at_capture", "worker_id", "worker_run_id",
    "host_start", "host_lifecycle", "operation_result", "verification"];
  const sourceId = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
  const digest = /^[0-9a-f]{64}$/;
  const itemStatuses = new Set(["active", "completed", "in_progress", "unknown", "blocked", "stopped"]);
  const requirementStatuses = new Set(["pending", "completed", "in_progress", "unknown", "blocked", "stopped"]);
  const hostStatuses = new Set(["model_turn_finished", "blocked", "failed", "cancelled", "limit_reached", "unknown"]);
  const finishReasons = new Set(["completed", "cancelled", "timeout", "error", "paused", "unknown"]);
  if (!cardObject(value) || !exactCardKeys(value, keys) || value.kind !== "linked_item_copy"
    || value.session_id !== session || value.completeness_scope !== "item_requirement"
    || value.operation_result !== "unknown" || value.verification !== "not_verified"
    || !timestamp(value.captured_at) || value.captured_at.length > 64
    || !["project_id", "source_session_id", "binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id"]
      .every(key => typeof value[key] === "string" && sourceId.test(value[key] as string))
    || typeof value.copy_id !== "string" || !digest.test(value.copy_id)
    || typeof value.source_item_digest !== "string" || !digest.test(value.source_item_digest)
    || typeof value.item_title !== "string" || !value.item_title || new TextEncoder().encode(value.item_title).length > 4096
    || typeof value.requirement_text !== "string" || !value.requirement_text || new TextEncoder().encode(value.requirement_text).length > 4096
    || typeof value.item_status_at_capture !== "string" || !itemStatuses.has(value.item_status_at_capture)
    || typeof value.requirement_status_at_capture !== "string" || !requirementStatuses.has(value.requirement_status_at_capture)
    || !cardObject(value.host_start) || !exactCardKeys(value.host_start, ["reservation_id", "parent_run_id", "turn_epoch"])
    || typeof value.host_start.reservation_id !== "string" || !sourceId.test(value.host_start.reservation_id)
    || typeof value.host_start.parent_run_id !== "string" || !sourceId.test(value.host_start.parent_run_id)
    || !nonnegative(value.host_start.turn_epoch) || value.host_start.turn_epoch < 1
    || !cardObject(value.host_lifecycle) || !exactCardKeys(value.host_lifecycle, ["sequence", "status", "finish_reason"])
    || value.host_lifecycle.sequence !== value.host_start.turn_epoch
    || typeof value.host_lifecycle.status !== "string" || !hostStatuses.has(value.host_lifecycle.status)
    || typeof value.host_lifecycle.finish_reason !== "string" || !finishReasons.has(value.host_lifecycle.finish_reason)
    || (value.host_lifecycle.status === "unknown" && value.host_lifecycle.finish_reason !== "unknown")) return false;
  return true;
}
const validSourceRef = (value: unknown): value is WorkCardSourceRef => cardObject(value) && Object.keys(value).length === 2 && typeof value.kind === "string" && typeof value.id === "string";
function validLinkedProjectSessionCopySnapshot(value: unknown, session: string): value is LinkedProjectSessionCopySnapshot {
  const keys = ["kind", "session_id", "captured_at", "copy_id", "binding_id", "source"];
  const sourceKeys = ["scope", "mapping_claim", "project_id", "source_session_id", "source_revision", "items", "worker_facts"];
  const idPattern = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
  const digestPattern = /^[0-9a-f]{64}$/;
  const itemStatuses = new Set(["active", "completed", "in_progress", "unknown", "blocked", "stopped"]);
  const requirementStatuses = new Set(["pending", "completed", "in_progress", "unknown", "blocked", "stopped"]);
  const hostStatuses = new Set(["model_turn_finished", "blocked", "failed", "cancelled", "limit_reached", "unknown"]);
  const finishReasons = new Set(["completed", "cancelled", "timeout", "error", "paused", "unknown"]);
  if (!cardObject(value) || !exactCardKeys(value, keys) || value.kind !== "linked_project_session_copy"
    || value.session_id !== session || !timestamp(value.captured_at) || typeof value.copy_id !== "string"
    || !digestPattern.test(value.copy_id) || typeof value.binding_id !== "string" || !idPattern.test(value.binding_id)
    || !cardObject(value.source) || !exactCardKeys(value.source, sourceKeys)) return false;
  const source = value.source;
  if (source.scope !== "authorized_source_session_only" || source.mapping_claim !== "not_asserted"
    || typeof source.project_id !== "string" || !idPattern.test(source.project_id)
    || typeof source.source_session_id !== "string" || !idPattern.test(source.source_session_id)
    || !nonnegative(source.source_revision) || !Array.isArray(source.items) || source.items.length > 500
    || !Array.isArray(source.worker_facts) || source.worker_facts.length < 1 || source.worker_facts.length > 20) return false;
  const items = new Map<string, Set<string>>();
  let requirementCount = 0;
  for (const item of source.items) {
    if (!cardObject(item) || !exactCardKeys(item, ["item_id", "title", "status", "requirements"])
      || typeof item.item_id !== "string" || !idPattern.test(item.item_id) || items.has(item.item_id)
      || typeof item.title !== "string" || item.title.length === 0 || new TextEncoder().encode(item.title).length > 4096
      || typeof item.status !== "string" || !itemStatuses.has(item.status)
      || !Array.isArray(item.requirements)) return false;
    const requirementIds = new Set<string>();
    for (const requirement of item.requirements) {
      if (!cardObject(requirement) || !exactCardKeys(requirement, ["requirement_id", "text", "status"])
        || typeof requirement.requirement_id !== "string" || !idPattern.test(requirement.requirement_id)
        || requirementIds.has(requirement.requirement_id) || typeof requirement.text !== "string"
        || requirement.text.length === 0 || new TextEncoder().encode(requirement.text).length > 4096
        || typeof requirement.status !== "string" || !requirementStatuses.has(requirement.status)) return false;
      requirementIds.add(requirement.requirement_id);
      requirementCount += 1;
      if (requirementCount > 5000) return false;
    }
    items.set(item.item_id, requirementIds);
  }
  const bindings = new Set<string>();
  let anchorFound = false;
  for (const fact of source.worker_facts) {
    if (!cardObject(fact) || !exactCardKeys(fact, ["binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id",
      "host_start", "host_lifecycle", "operation_result", "verification"])
      || !["binding_id", "item_id", "requirement_id", "worker_id", "worker_run_id"]
        .every(key => typeof fact[key] === "string" && idPattern.test(fact[key] as string))
      || typeof fact.binding_id !== "string" || typeof fact.item_id !== "string"
      || typeof fact.requirement_id !== "string" || bindings.has(fact.binding_id)
      || !items.get(fact.item_id)?.has(fact.requirement_id)
      || fact.operation_result !== "unknown" || fact.verification !== "not_verified") return false;
    bindings.add(fact.binding_id);
    if (fact.binding_id === value.binding_id) anchorFound = true;
    const start = fact.host_start;
    const lifecycle = fact.host_lifecycle;
    if (!cardObject(start) || !exactCardKeys(start, ["reservation_id", "parent_run_id", "turn_epoch"])
      || typeof start.reservation_id !== "string" || !idPattern.test(start.reservation_id)
      || typeof start.parent_run_id !== "string" || !idPattern.test(start.parent_run_id)
      || !nonnegative(start.turn_epoch) || start.turn_epoch < 1
      || !cardObject(lifecycle) || !exactCardKeys(lifecycle, ["sequence", "status", "finish_reason"])
      || lifecycle.sequence !== start.turn_epoch || typeof lifecycle.status !== "string" || !hostStatuses.has(lifecycle.status)
      || typeof lifecycle.finish_reason !== "string" || !finishReasons.has(lifecycle.finish_reason)
      || (lifecycle.status === "unknown" && lifecycle.finish_reason !== "unknown")) return false;
  }
  if (!anchorFound) return false;
  try { return new TextEncoder().encode(JSON.stringify(source)).length <= 65536; }
  catch { return false; }
}
function validProjectScopedRequirement(
  item: Record<string, any>, requirement: Record<string, unknown>, session: string, projectId: string,
): boolean {
  const result = requirement.latest_result;
  if (item.session_id !== session || item.project_id !== projectId
    || !Array.isArray(item.source_refs) || !item.source_refs.every(validSourceRef)
    || item.source_refs.some((source: WorkCardSourceRef) => source.kind !== "project" || source.id !== projectId)
    || requirement.session_id !== session || requirement.project_id !== projectId
    || requirement.item_id !== item.item_id || requirement.current_result_missing !== false
    || !cardObject(requirement.source_ref) || requirement.source_ref.kind !== "project"
    || requirement.source_ref.id !== projectId || !nonnegative(requirement.result_count)
    || !nonnegative(requirement.omitted_result_count)) return false;
  // Queued or running work may hide stale history; it is not a current result.
  if (result === null && (requirement.status === "pending" || requirement.status === "in_progress")) {
    return item.status === "unknown" && requirement.omitted_result_count === requirement.result_count
      && (requirement.current_run_id === null
        ? requirement.status === "pending" && requirement.latest_result_is_current === null
        : nonempty(requirement.current_run_id) && requirement.latest_result_is_current === false);
  }
  if (!cardObject(result) || result.session_id !== session || result.project_id !== projectId
    || result.item_id !== item.item_id || result.requirement_id !== requirement.requirement_id
    || !nonempty(result.result_id) || !nonempty(result.run_id) || typeof result.summary !== "string"
    || result.verification_status !== "not_verified" || !Array.isArray(result.sources)
    || !result.sources.every(validSourceRef)) return false;

  const startedUnknown = item.status === "unknown" && requirement.status === "unknown"
    && nonempty(requirement.current_run_id) && requirement.latest_result_is_current === true
    && result.status === "unknown" && result.run_id === requirement.current_run_id;
  const blockedBeforeStart = item.status === "unknown" && requirement.status === "blocked"
    && requirement.current_run_id === null && requirement.latest_result_is_current === null
    && result.status === "blocked";
  return startedUnknown || blockedBeforeStart;
}

function validWorkCard(value: unknown, session: string, expectedId: string, itemId?: string): value is WorkCard {
  if (!cardObject(value) || value.card_id !== expectedId || !timestamp(value.saved_at) || !(value.viewed_at === null || timestamp(value.viewed_at)) || !cardObject(value.snapshot)) return false;
  const s = value.snapshot;
  if (s.kind === "linked_item_copy") return validLinkedItemCopySnapshot(s, session);
  if (s.kind === "linked_project_session_copy") return validLinkedProjectSessionCopySnapshot(s, session);
  if (s.session_id !== session || !timestamp(s.captured_at) || s.completeness !== "complete" || typeof s.persisted !== "boolean" || typeof s.history_complete !== "boolean" || !(s.incomplete_reason === null || typeof s.incomplete_reason === "string") || !(s.missing_current_result_count === null || nonnegative(s.missing_current_result_count))) return false;
  if (![s.item_count,s.requirement_count,s.result_count,s.omitted_item_count,s.omitted_requirement_count,s.omitted_result_count].every(nonnegative)) return false;
  const independent = s.kind === "independent";
  if (itemId !== undefined && (!independent || s.item_id !== itemId)) return false;
  const cards = independent ? [s.card] : s.cards;
  if (!Array.isArray(cards) || (independent ? s.project_id !== null || !nonempty(s.item_id) || cards.length !== 1 : !nonempty(s.project_id))) return false;
  const scopedProjectId = s.kind === "project_scoped" && typeof s.project_id === "string" ? s.project_id : "";
  if (s.kind === "project_scoped") {
    const requirements = cards.flatMap(item => cardObject(item) && Array.isArray(item.requirements) ? item.requirements : []);
    const resultCount = requirements.reduce((sum, row) => sum + (cardObject(row) && nonnegative(row.result_count) ? row.result_count : 0), 0);
    if (s.omitted_item_count !== 0 || s.omitted_requirement_count !== 0 || s.incomplete_reason !== null
      || s.missing_current_result_count !== 0 || s.item_count !== cards.length
      || s.requirement_count !== requirements.length || s.result_count !== resultCount) return false;
  }
  return cards.every(item => {
    if (!cardObject(item) || item.session_id !== session || !nonempty(item.item_id)
      || typeof item.title !== "string" || !nonempty(item.status) || !Array.isArray(item.source_refs)
      || !item.source_refs.every(validSourceRef) || !Array.isArray(item.requirements)
      || (independent && (item.item_id !== s.item_id || item.kind !== "independent" || item.project_id !== null))
      || (!independent && item.project_id !== s.project_id)) return false;
    return item.requirements.every((value: unknown) => {
      if (!cardObject(value) || value.session_id !== session || value.item_id !== item.item_id
        || !nonempty(value.requirement_id) || typeof value.text !== "string" || !nonempty(value.status)
        || !nonnegative(value.result_count) || !nonnegative(value.omitted_result_count)
        || typeof value.current_result_missing !== "boolean"
        || !(value.current_run_id === null || nonempty(value.current_run_id))
        || !(value.latest_result_is_current === null || typeof value.latest_result_is_current === "boolean")
        || !(value.source_ref === null || validSourceRef(value.source_ref))) return false;
      if (s.kind === "project_scoped" && !validProjectScopedRequirement(item, value, session, scopedProjectId)) return false;
      const result = value.latest_result;
      return result === null || (cardObject(result) && result.session_id === session
        && result.item_id === item.item_id && result.requirement_id === value.requirement_id
        && (s.kind !== "project_scoped" || result.project_id === s.project_id)
        && nonempty(result.result_id) && nonempty(result.run_id) && nonempty(result.status)
        && typeof result.summary === "string"
        && (result.verification_status === null || typeof result.verification_status === "string")
        && Array.isArray(result.sources) && result.sources.every(validSourceRef));
    });
  });
}
function validIndexItem(value: unknown): value is WorkCardIndexItem {
  if (!cardObject(value) || !cardId(value.card_id) || !timestamp(value.saved_at) || !(value.viewed_at === null || timestamp(value.viewed_at)) || typeof value.title !== "string" || !cardObject(value.scope)) return false;
  const scope = value.scope;
  const sourceId = (id: unknown): id is string => typeof id === "string"
    && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id) && !/[\r\n]/.test(id);
  if (scope.kind === "independent") return exactCardKeys(scope, ["kind", "item_id"]) && nonempty(scope.item_id);
  if (scope.kind === "project") return exactCardKeys(scope, ["kind", "project_id"]) && nonempty(scope.project_id);
  if (scope.kind === "linked_item_copy") return exactCardKeys(scope, ["kind", "project_id", "item_id"])
    && sourceId(scope.project_id) && sourceId(scope.item_id);
  if (scope.kind === "linked_project_session_copy") return exactCardKeys(scope, ["kind", "project_id", "source_session_id", "mapping_claim"])
    && sourceId(scope.project_id) && sourceId(scope.source_session_id) && scope.mapping_claim === "not_asserted";
  return false;
}

export class AgentWsClient {
  private ws: WebSocket | null = null;
  private connectionAttempt: { socket: WebSocket; promise: Promise<void>; reject: (error: Error) => void } | null = null;
  private handlers = new Set<EventHandler>();
  private reconnectTimer: number | null = null;
  private currentConversationId = "";
  private usedAutoRecognitionRequestIds = new Set<string>();
  private autoRecognitionPending: { sessionId: string; requestId: string; action: "enable" | "revoke" } | null = null;
  private cardPending = new Map<string, PendingCard>();
  private cardRetired = new Set<string>();
  private usedResultViewRequestIds = new Set<string>();
  private snapshotViewTimers = new Map<string, { timer: number; token: object }>();
  private snapshotViewAmbiguous = new Set<string>();
  // Never reuse a list correlation ID, including across reconnects.
  private usedCardListIds = new Set<string>();
  private cardProjectId: string | null = null;
  private workCardPresentationIds = new Map<string, string>();
  private linkedItemCopyIdentities = new Map<string, { projectId: string; itemId: string }>();
  private linkedProjectSessionCopyIdentities = new Map<string, { projectId: string; mappingClaim: "not_asserted" }>();
  private receiptBacklogSignature = "";
  private overviewPending = new Map<string, string>();
  private workRecordControlPending = new Map<string, { sessionId: string; itemId: string; projectId: string; requirementId: string; action: WorkRecordControlAction }>();
  private scheduleDispatchPending = new Map<string, { sessionId: string }>();
  private destroyed = false;
  private lane: SidecarLane = "stable";
  private legacyLaneQueriesAllowed = false;
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

  private async laneCapabilities(): Promise<SessionLaneCapabilities> {
    // Missing identity is not permission to inspect legacy sidecar lanes.
    try {
      return await window.myAgentDesktop?.getSessionLaneCapabilities?.() ?? { legacySessionLanes: false };
    } catch {
      return { legacySessionLanes: false };
    }
  }

  private async resolveSidecarInfo(lane: SidecarLane): Promise<{ host: string; port: number }> {
    const capabilities = await this.laneCapabilities();
    if (!isLegacyLaneQueryAllowed(capabilities)) {
      throw new Error("实验运行时没连上。请用 start-desktop.bat 重启桌面后再开实验会话。");
    }
    const api = window.myAgentDesktop;
    if (!api) throw new Error("myAgentDesktop preload API missing");
    if (lane === "sandbox") {
      const ensured = await api.ensureSandboxSidecar?.();
      if (!ensured?.ok) throw new Error(ensured?.message || "sandbox sidecar unavailable");
      const info = await api.getSandboxSidecar?.();
      if (!info) throw new Error("sandbox sidecar not ready");
      return info;
    }
    const info = await api.getSidecar();
    if (!info) throw new Error("stable sidecar not ready");
    return info;
  }

  async ensureLane(lane: SidecarLane): Promise<void> {
    if (this.destroyed) throw new Error("WebSocket client destroyed");
    if (this.lane !== lane) return this.switchLane(lane);
    if (this.ws?.readyState === WebSocket.OPEN) return;
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    await this.connect();
  }

  async reconnectActiveLane(): Promise<void> {
    await this.switchLane(this.lane);
  }

  async switchLane(lane: SidecarLane): Promise<void> {
    const info = await this.resolveSidecarInfo(lane);
    if (this.destroyed) throw new Error("WebSocket client destroyed");
    this.updateEvolutionConversationId(null);
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      const previous = this.ws;
      this.ws = null;
      this.retireSocket(previous, new Error("WebSocket connection superseded by lane switch"));
    }
    this.lane = lane;
    this.host = info.host;
    this.port = info.port;
    await this.connect();
  }

  connect(): Promise<void> {
    if (this.destroyed) return Promise.reject(new Error("WebSocket client destroyed"));
    if (this.ws?.readyState === WebSocket.OPEN) return Promise.resolve();
    if (this.ws?.readyState === WebSocket.CONNECTING && this.connectionAttempt?.socket === this.ws) {
      return this.connectionAttempt.promise;
    }
    if (this.ws) {
      const previous = this.ws;
      this.ws = null;
      this.retireSocket(previous, new Error("WebSocket connection superseded"));
    }
    this.updateEvolutionConversationId(null);
    this.cardPending.clear();
    this.cardRetired.clear();
    this.cardProjectId = null;
    this.overviewPending.clear();
    this.workRecordControlPending.clear();
    this.scheduleDispatchPending.clear();
    const url = `ws://${this.host}:${this.port}`;
    const ws = new WebSocket(url);
    this.ws = ws;

    let resolveAttempt!: () => void;
    let rejectAttempt!: (error: Error) => void;
    const promise = new Promise<void>((resolve, reject) => {
      resolveAttempt = resolve;
      rejectAttempt = (error) => reject(error);
    });
    let settled = false;
    const settle = (error?: Error) => {
      if (settled) return;
      settled = true;
      if (this.connectionAttempt?.socket === ws) this.connectionAttempt = null;
      if (error) rejectAttempt(error);
      else resolveAttempt();
    };
    this.connectionAttempt = { socket: ws, promise, reject: settle };

    ws.addEventListener("open", () => {
      if (this.destroyed || this.ws !== ws) {
        settle(new Error(this.destroyed ? "WebSocket client destroyed" : "WebSocket connection superseded"));
        return;
      }
      if (this.reconnectTimer !== null) {
        window.clearTimeout(this.reconnectTimer);
        this.reconnectTimer = null;
      }
      // Rehydrate persistent terminal visibility after the initial connect
      // and every reconnect; the unified shell also requests an explicit
      // snapshot after its event handler is mounted.
      try {
        this.send({ type: "terminal.list" });
        settle();
      } catch (error) {
        settle(error instanceof Error ? error : new Error(String(error)));
      }
    });

    ws.addEventListener("message", (ev) => {
      if (this.destroyed || this.ws !== ws) return;
      try {
        const data = JSON.parse(String(ev.data)) as ServerEvent;
        if (data && typeof data === "object" && "type" in data) {
          if (data.type === "client.connection.closed") return; // internal notification cannot arrive from server
          if (data.type === "user.message.persisted" && (
            Object.keys(data).length !== 3
            || Object.keys(data).some((key) => key !== "type" && key !== "session_id" && key !== "request_id")
            || typeof data.session_id !== "string" || !data.session_id
            || data.session_id !== this.currentConversationId
            || typeof data.request_id !== "string" || !WORK_CARD_UUID.test(data.request_id)
          )) return;
          if ((data.type === "approval_preferences.state" || data.type === "approval_preferences.done") && (
            (data.status !== "ok" && data.status !== "rejected")
            || !["manual", "auto", "full"].includes(data.approval_mode)
            || !["manual", "auto", "full"].includes(data.saved_approval_mode)
            || !Array.isArray(data.available_modes)
            || data.available_modes.length !== 3
            || new Set(data.available_modes).size !== 3
            || !["manual", "auto", "full"].every(mode => data.available_modes.includes(mode as ApprovalPreferenceMode))
            || Object.keys(data).some(key => !["type", "status", "approval_mode", "saved_approval_mode", "available_modes", "message"].includes(key))
            || (data.message !== undefined && typeof data.message !== "string")
          )) return;
          if (data.type === "approval_preferences.state" || data.type === "approval_preferences.done") return; // Only authenticated Electron IPC may update this view.
          if (data.type === "session.banner") {
            if (data.session_id !== this.currentConversationId) this.autoRecognitionPending = null;
            const nextProject = typeof data.project_id === "string" ? data.project_id : "";
            if (this.currentConversationId && (data.session_id !== this.currentConversationId || this.cardProjectId !== nextProject)) this.retireCardRequests();
            this.updateEvolutionConversationId(data.session_id, this.cardProjectId !== nextProject);
            this.cardProjectId = nextProject;
            data.execution_lane ??= this.lane;
          }
          if ((data.type === "llm_role_models.state" || data.type === "llm_role_models.done") && (
            !["ok", "rejected"].includes(data.status)
            || Object.keys(data).some(key => !["type", "status", "preferences", "explicit_roles", "models", "message"].includes(key))
            || (data.status === "ok" && (!data.preferences || !Array.isArray(data.models)))
            || (data.preferences !== undefined && (typeof data.preferences !== "object" || data.preferences === null
              || Object.keys(data.preferences).length !== 2
              || typeof data.preferences.main !== "string" || typeof data.preferences.subagent !== "string"))
            || (data.models !== undefined && (!Array.isArray(data.models) || data.models.some(model => !model || typeof model.id !== "string" || typeof model.name !== "string" || typeof model.configured !== "boolean")))
            || (data.message !== undefined && typeof data.message !== "string")
          )) return;
          if (data.type === "work-record.auto-recognition.state" && (
            Object.keys(data).length !== 3 || typeof data.session_id !== "string" || !data.session_id
            || data.session_id !== this.currentConversationId || typeof data.enabled !== "boolean"
            || this.autoRecognitionPending !== null
          )) return;
          if (data.type === "work-record.auto-recognition.done") {
            const pending = this.autoRecognitionPending;
            if (!pending || data.session_id !== this.currentConversationId || data.session_id !== pending.sessionId
              || data.request_id !== pending.requestId || data.action !== pending.action
              || (data.status !== "ok" && data.status !== "rejected") || typeof data.enabled !== "boolean"
              || (data.status === "ok" && data.enabled !== (data.action === "enable"))
              || Object.keys(data).some(k => !["type", "session_id", "request_id", "action", "status", "enabled"].includes(k))) return;
            this.autoRecognitionPending = null;
          }
          if (data.type === "work-record.post-turn.result" &&
              (!this.currentConversationId || data.session_id !== this.currentConversationId)) return;
          if (data.type === "work-record.control.done") {
            if (!this.acceptWorkRecordControlDone(data)) return;
          } else if (data.type === "work-records.schedule.dispatch.done") {
            if (!this.acceptScheduleDispatchDone(data)) return;
          } else if (data.type === "work-records.overview.result") {
            if (!this.acceptWorkRecordsOverviewResult(data)) return;
          } else if (data.type === "work-card.present") {
            if (!this.acceptWorkCardPresent(data)) return;
          } else if (typeof data.type === "string" && data.type.startsWith("work-card.") && !this.acceptCardEvent(data)) return;
          if (data.type === "session.list") {
            data.sessions = data.sessions.map((session) => ({ ...session, execution_lane: session.execution_lane ?? this.lane }));
          }
          for (const handler of this.handlers) handler(data);
        }
      } catch {
        // ignore malformed frames
      }
    });

    ws.addEventListener("error", () => {
      if (this.destroyed || this.ws !== ws) {
        settle(new Error(this.destroyed ? "WebSocket client destroyed" : "WebSocket connection superseded"));
        return;
      }
      settle(new Error(`WebSocket error: ${url}`));
    });

    ws.addEventListener("close", () => {
      if (this.ws !== ws) {
        settle(new Error(this.destroyed ? "WebSocket client destroyed" : "WebSocket connection superseded"));
        return;
      }
      this.ws = null;
      this.cardPending.clear();
      this.cardRetired.clear();
      this.cardProjectId = null;
      this.overviewPending.clear();
    this.workRecordControlPending.clear();
    this.scheduleDispatchPending.clear();
      settle(new Error("WebSocket closed before connection opened"));
      if (this.destroyed) return;
      this.updateEvolutionConversationId(null);
      this.scheduleReconnect();
    });
    return promise;
  }

  private retireSocket(socket: WebSocket, reason: Error): void {
    if (this.connectionAttempt?.socket === socket) {
      const attempt = this.connectionAttempt;
      this.connectionAttempt = null;
      attempt.reject(reason);
    }
    try { socket.close(); } catch { /* ignore a retired socket close error */ }
  }

  private scheduleReconnect(): void {
    if (this.destroyed || this.reconnectTimer !== null) return;
    this.reconnectTimer = window.setTimeout(() => {
      this.reconnectTimer = null;
      void this.reconnect();
    }, 2000);
  }

  private async reconnect(): Promise<void> {
    if (this.destroyed) return;
    try {
      const capabilities = await this.laneCapabilities();
      // Fixed Hosts reconnect only their bound sidecar. A selected sandbox
      // lane must never silently fall back to the stable endpoint.
      const sidecar = this.lane === "sandbox" || isLegacyLaneQueryAllowed(capabilities)
        ? await this.resolveSidecarInfo(this.lane)
        : await window.myAgentDesktop?.getSidecar();
      if (this.destroyed) return;
      if (sidecar) {
        this.host = sidecar.host;
        this.port = sidecar.port;
      }
      await this.connect();
    } catch {
      if (this.destroyed) return;
      this.scheduleReconnect();
    }
  }

  onEvent(handler: EventHandler): () => void {
    this.handlers.add(handler);
    return () => this.handlers.delete(handler);
  }

  /** Tear down WebSocket; used when switching pet ↔ workbench. */
  destroy(): void {
    this.destroyed = true;
    this.updateEvolutionConversationId(null);
    if (this.reconnectTimer !== null) {
      window.clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      const previous = this.ws;
      this.ws = null;
      this.retireSocket(previous, new Error("WebSocket client destroyed"));
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

  /** Proposed explicit session-scoped consent protocol; no optimistic authorization. */
  setWorkRecordAutoRecognition(sessionId: string, action: "enable" | "revoke"): string {
    if (!sessionId || sessionId !== this.currentConversationId || this.autoRecognitionPending
        || (action !== "enable" && action !== "revoke")) throw new Error("automatic recognition unavailable or request pending");
    const requestId = crypto.randomUUID();
    if (!WORK_CARD_UUID.test(requestId) || this.usedAutoRecognitionRequestIds.has(requestId)) throw new Error("invalid or reused request UUID");
    this.usedAutoRecognitionRequestIds.add(requestId);
    this.autoRecognitionPending = { sessionId, requestId, action };
    try { this.send({ type: "work-record.auto-recognition.set", session_id: sessionId, request_id: requestId, action }); }
    catch (error) { this.autoRecognitionPending = null; throw error; }
    return requestId;
  }

  controlWorkRecord(options: {
    itemId: string; requirementId?: string; projectId?: string; action: WorkRecordControlAction; expectedStatus?: string;
    expectedRevision?: number; expectedSourceRevision?: number; dependsOn?: string[]; dependsOnRefs?: Array<{ item_id: string; requirement_id: string }>; priority?: number; newText?: string; inspectionEvidence?: string;
  }): string {
    if (!cardObject(options) || !nonempty(options.itemId) || !this.currentConversationId
        || !["set_dependencies", "queue", "wait_confirmation", "release_confirmation", "stop", "continue", "cancel_execution", "revise_requirement", "set_priority", "copy_requirement_to_new_item", "inspect_unknown_claim"].includes(options.action)) {
      throw new Error("invalid work-record control request");
    }
    const action = options.action;
    const requirementId = options.requirementId ?? "";
    if (action === "set_priority") {
      if (!Number.isInteger(options.priority) || (options.priority as number) < 0 || (options.priority as number) > 5) throw new Error("invalid work-record priority");
    } else if (!nonempty(requirementId) || (action !== "copy_requirement_to_new_item" && !nonempty(options.expectedStatus))) {
      throw new Error("requirement controls require identity and expected status");
    }
    if (action === "revise_requirement" && !nonempty(options.newText)) throw new Error("revision text is required");
    if (action !== "revise_requirement" && options.newText !== undefined) throw new Error("unexpected revision text");
    if (action === "inspect_unknown_claim" && (!nonempty(options.inspectionEvidence) || options.inspectionEvidence.length > 4000)) throw new Error("inspection evidence is required");
    if (action !== "inspect_unknown_claim" && options.inspectionEvidence !== undefined) throw new Error("unexpected inspection evidence");
    if (options.dependsOn !== undefined && (!Array.isArray(options.dependsOn)
        || options.dependsOn.length > 128 || options.dependsOn.some(value => !nonempty(value)))) throw new Error("invalid dependency list");
    if (options.dependsOnRefs !== undefined && (!Array.isArray(options.dependsOnRefs)
        || options.dependsOnRefs.length > 128 || options.dependsOnRefs.some(ref => !cardObject(ref)
          || !nonempty(ref.item_id) || !nonempty(ref.requirement_id)))) throw new Error("invalid cross-item dependency list");
    const sessionId = this.currentConversationId;
    const requestId = crypto.randomUUID();
    const eventId = crypto.randomUUID();
    if (!WORK_CARD_UUID.test(requestId) || !WORK_CARD_UUID.test(eventId) || this.workRecordControlPending.has(requestId)) throw new Error("invalid or reused work-record control identity");
    const projectId = options.projectId ?? "";
    if (projectId) {
      if (!nonempty(projectId) || !Number.isSafeInteger(options.expectedSourceRevision) || (options.expectedSourceRevision as number) < 0) {
        throw new Error("remote project control requires a source revision");
      }
    } else if (!Number.isSafeInteger(options.expectedRevision) || (options.expectedRevision as number) < 0) {
      throw new Error("local work-record control requires a revision");
    }
    this.workRecordControlPending.set(requestId, { sessionId, itemId: options.itemId, projectId, requirementId, action });
    try {
      this.send({ type: "work-record.control", session_id: sessionId, request_id: requestId,
        item_id: options.itemId, expected_revision: options.expectedRevision ?? 0,
        ...(projectId ? { project_id: projectId, expected_source_revision: options.expectedSourceRevision } : {}),
        ...(requirementId ? { requirement_id: requirementId } : {}), action,
        ...(options.expectedStatus ? { expected_status: options.expectedStatus } : {}),
        ...(options.dependsOn ? { depends_on: options.dependsOn } : {}),
        ...(options.dependsOnRefs ? { depends_on_refs: options.dependsOnRefs } : {}),
        ...(options.newText === undefined ? {} : { new_text: options.newText }),
        ...(options.inspectionEvidence === undefined ? {} : { inspection_evidence: options.inspectionEvidence }),
        ...(options.priority === undefined ? {} : { priority: options.priority }),
        event_id: eventId, idempotency_key: requestId });
    } catch (error) {
      this.workRecordControlPending.delete(requestId);
      throw error;
    }
    return requestId;
  }

  dispatchScheduleWorkers(options: {
    projectIds: string[];
    workers: Array<{
      project_id: string; item_id: string; requirement_id: string; priority: number;
      task: string; read_targets: string[]; write_targets: string[];
    }>;
  }): string {
    if (!cardObject(options) || !Array.isArray(options.projectIds) || !options.projectIds.length
        || options.projectIds.length > 6 || !Array.isArray(options.workers)
        || !options.workers.length || options.workers.length > 2 || !this.currentConversationId) {
      throw new Error("invalid schedule dispatch request");
    }
    const sessionId = this.currentConversationId;
    const requestId = crypto.randomUUID();
    if (this.scheduleDispatchPending.has(requestId)) throw new Error("invalid schedule dispatch identity");
    this.scheduleDispatchPending.set(requestId, { sessionId });
    try {
      this.send({
        type: "work-records.schedule.dispatch",
        session_id: sessionId,
        request_id: requestId,
        project_ids: [...new Set(options.projectIds)],
        workers: options.workers,
      });
    } catch (error) {
      this.scheduleDispatchPending.delete(requestId);
      throw error;
    }
    return requestId;
  }

  private acceptScheduleDispatchDone(event: unknown): event is WorkRecordScheduleDispatchDoneEvent {
    if (!cardObject(event) || event.type !== "work-records.schedule.dispatch.done"
        || typeof event.request_id !== "string" || typeof event.session_id !== "string"
        || event.session_id !== this.currentConversationId || typeof event.message !== "string"
        || !["applied", "rejected"].includes(String(event.status))) return false;
    const pending = this.scheduleDispatchPending.get(event.request_id);
    if (!pending || pending.sessionId !== event.session_id) return false;
    this.scheduleDispatchPending.delete(event.request_id);
    return true;
  }

  private acceptWorkRecordControlDone(event: unknown): event is WorkRecordControlDoneEvent {
    if (!cardObject(event) || event.type !== "work-record.control.done"
        || typeof event.request_id !== "string" || typeof event.session_id !== "string"
        || event.session_id !== this.currentConversationId || typeof event.item_id !== "string"
        || typeof event.action !== "string" || !["applied", "rejected"].includes(String(event.status))
        || typeof event.message !== "string") return false;
    const pending = this.workRecordControlPending.get(event.request_id);
    if (!pending || pending.sessionId !== event.session_id || pending.itemId !== event.item_id
        || pending.projectId !== (typeof event.project_id === "string" ? event.project_id : "")
        || pending.requirementId !== (typeof event.requirement_id === "string" ? event.requirement_id : "")
        || pending.action !== event.action) return false;
    if (Object.keys(event).some(key => !["type", "request_id", "session_id", "item_id", "project_id", "requirement_id", "action", "status", "message"].includes(key))) return false;
    this.workRecordControlPending.delete(event.request_id);
    return true;
  }

  queryWorkRecordsOverview(options: { cursor?: string; pageSize?: number; detailPaging?: boolean; projectIds?: string[]; history?: { projectId: string; itemId: string; requirementId: string; cursor?: string }; mergedHistory?: { cursor?: string } } = {}): string {
    if (!cardObject(options) || Object.keys(options).some(key => !["cursor", "pageSize", "detailPaging", "projectIds", "history", "mergedHistory"].includes(key))) throw new Error("invalid work-records overview options");
    if (options.history && (!options.projectIds?.includes(options.history.projectId) || !nonempty(options.history.itemId)
      || !nonempty(options.history.requirementId) || options.mergedHistory !== undefined
      || (options.history.cursor !== undefined && (!nonempty(options.history.cursor) || options.history.cursor.length > 2048)))) throw new Error("invalid authorized history scope");
    if (options.mergedHistory && (!options.projectIds?.length || (options.mergedHistory.cursor !== undefined
      && (!nonempty(options.mergedHistory.cursor) || options.mergedHistory.cursor.length > 2048)))) throw new Error("invalid merged history scope");
    const pageSize = options.pageSize ?? 6;
    const detailPaging = options.detailPaging ?? true;
    if (typeof detailPaging !== "boolean") throw new Error("invalid work-records overview detail paging option");
    if (!Number.isInteger(pageSize) || pageSize < 1 || pageSize > 50) throw new Error("invalid work-records overview page size");
    if (options.cursor !== undefined && (typeof options.cursor !== "string" || !options.cursor || options.cursor.length > 4096)) throw new Error("invalid work-records overview cursor");
    if (options.projectIds !== undefined && (!Array.isArray(options.projectIds) || !options.projectIds.length || options.projectIds.length > 6
        || options.projectIds.some(value => !nonempty(value)) || new Set(options.projectIds).size !== options.projectIds.length
        || options.cursor !== undefined)) throw new Error("invalid selected project overview scope");
    if (!this.currentConversationId) throw new Error("work-records overview requires a current session banner");
    const requestId = crypto.randomUUID();
    if (!WORK_CARD_UUID.test(requestId) || this.overviewPending.has(requestId)) throw new Error("invalid or reused work-records overview request UUID");
    const sessionId = this.currentConversationId;
    this.overviewPending.set(requestId, sessionId);
    try {
      this.send({ type: "work-records.overview.query", session_id: sessionId, request_id: requestId,
        ...(options.cursor === undefined ? {} : { cursor: options.cursor }), page_size: pageSize,
        ...(detailPaging ? { detail_paging: true } : {}),
        ...(options.projectIds ? { project_ids: options.projectIds } : {}),
        ...(options.history ? { history_project_id: options.history.projectId, history_item_id: options.history.itemId,
          history_requirement_id: options.history.requirementId, history_page_size: 10,
          ...(options.history.cursor ? { history_cursor: options.history.cursor } : {}) } : {}),
        ...(options.mergedHistory ? { history_scope: "selected_projects_history", history_page_size: 10,
          ...(options.mergedHistory.cursor ? { history_cursor: options.mergedHistory.cursor } : {}) } : {}) });
    } catch (error) {
      this.overviewPending.delete(requestId);
      throw error;
    }
    return requestId;
  }

  private validOverviewPageFacts(value: unknown, expectedKind: "items" | "requirements", groupKind: string,
    groupId: string, itemId: string | null): value is WorkRecordsOverviewPageFacts {
    if (!cardObject(value)) return false;
    const fields = ["kind", "group_kind", "group_id", "item_id", "offset", "total", "has_more", "next_cursor"];
    return Object.keys(value).length === fields.length && fields.every(field => field in value)
      && value.kind === expectedKind && value.group_kind === groupKind && value.group_id === groupId
      && value.item_id === itemId && nonnegative(value.offset) && nonnegative(value.total)
      && value.offset <= value.total && typeof value.has_more === "boolean"
      && (value.next_cursor === null || nonempty(value.next_cursor))
      && (value.has_more ? nonempty(value.next_cursor) : value.next_cursor === null);
  }

  private sameOverviewPageFacts(left: unknown, right: WorkRecordsOverviewPageFacts): boolean {
    if (!cardObject(left) || Object.keys(left).length !== 8) return false;
    const actual = left as Record<string, unknown>;
    const expected = right as unknown as Record<string, unknown>;
    return ["kind", "group_kind", "group_id", "item_id", "offset", "total", "has_more", "next_cursor"]
      .every(key => actual[key] === expected[key]);
  }

  private acceptWorkRecordsOverviewResult(event: unknown): event is WorkRecordsOverviewResultEvent {
    if (cardObject(event) && event.type === "work-records.overview.result" && event.history_page !== undefined) {
      const page = event.history_page;
      const source = event.source;
      const mergedSources = event.history_sources;
      const rows = event.history;
      if (event.history_scope === "selected_projects_history") {
        if (typeof event.request_id !== "string" || !WORK_CARD_UUID.test(event.request_id)
          || event.session_id !== this.currentConversationId || this.overviewPending.get(event.request_id) !== event.session_id
          || event.aggregation_scope !== "selected_project_sessions" || !Array.isArray(event.selected_project_ids)
          || !["visible", "no_records"].includes(String(event.state)) || !Array.isArray(mergedSources) || !Array.isArray(rows)
          || (event.state === "no_records" && rows.length !== 0)
          || !cardObject(page) || page.scope !== "selected_projects_history" || !nonnegative(page.page_size)
          || page.page_size < 1 || page.page_size > 20 || !nonnegative(page.offset) || typeof page.has_more !== "boolean"
          || !(page.total === null || nonnegative(page.total))
          || (page.has_more ? !nonempty(page.next_cursor) : page.next_cursor !== null)
          || mergedSources.length !== event.selected_project_ids.length
          || new Set(event.selected_project_ids as string[]).size !== event.selected_project_ids.length
          || [...(event.selected_project_ids as string[])].sort().some((id, index) => id !== [...mergedSources.map(source => cardObject(source) ? source.project_id : "")].sort()[index])
          || mergedSources.some(source => !cardObject(source) || !nonempty(source.project_id)
            || !event.selected_project_ids!.includes(source.project_id as string) || !nonempty(source.session_id) || !nonnegative(source.revision))
          || new Set(mergedSources.filter(cardObject).map(source => source.project_id)).size !== mergedSources.length
          || rows.length > page.page_size || (page.has_more && rows.length !== page.page_size)
          || (event.state === "no_records" && rows.length !== 0)
          || rows.some(row => !cardObject(row) || !nonempty(row.project_id)
            || !nonempty(row.session_id)
            || !mergedSources.some(source => cardObject(source) && source.project_id === row.project_id && source.session_id === row.session_id)
            || !nonempty(row.item_id) || !nonempty(row.requirement_id)
            || !nonempty(row.run_id) || !nonempty(row.result_id) || !nonempty(row.status)
            || typeof row.summary !== "string" || !nonempty(row.verification_status)
            || !(row.recorded_at === null || typeof row.recorded_at === "number" && Number.isFinite(row.recorded_at)))) return false;
        this.overviewPending.delete(event.request_id);
        return true;
      }
      if (typeof event.request_id !== "string" || !WORK_CARD_UUID.test(event.request_id)
        || event.session_id !== this.currentConversationId || this.overviewPending.get(event.request_id) !== event.session_id
        || event.aggregation_scope !== "selected_project_sessions" || !Array.isArray(event.selected_project_ids)
        || !event.selected_project_ids.includes(event.history_project_id as string)
        || !nonempty(event.history_item_id) || !nonempty(event.history_requirement_id)
        || event.state !== "visible" || !cardObject(source) || source.project_id !== event.history_project_id
        || !nonempty(source.session_id) || !nonnegative(source.revision) || !Array.isArray(rows)
        || !cardObject(page) || page.scope !== "this_requirement_only" || !nonnegative(page.page_size)
        || page.page_size < 1 || page.page_size > 20 || !nonnegative(page.offset) || typeof page.has_more !== "boolean"
        || (page.has_more ? !nonempty(page.next_cursor) : page.next_cursor !== null)
        || rows.length > page.page_size || rows.some(row => !cardObject(row) || !nonempty(row.run_id)
          || !nonempty(row.result_id) || !nonempty(row.status) || typeof row.summary !== "string"
          || !nonempty(row.verification_status) || !(row.recorded_at === null || typeof row.recorded_at === "number" && Number.isFinite(row.recorded_at)))) return false;
      this.overviewPending.delete(event.request_id);
      return true;
    }
    if (!cardObject(event) || event.type !== "work-records.overview.result"
      || typeof event.request_id !== "string" || !WORK_CARD_UUID.test(event.request_id)
      || typeof event.session_id !== "string" || event.session_id !== this.currentConversationId
      || this.overviewPending.get(event.request_id) !== event.session_id
      || !["no_records", "visible", "collapsed", "cancelled", "error", "expired"].includes(String(event.state))
      || (event.work_record_revision !== undefined && (!Number.isSafeInteger(event.work_record_revision) || Number(event.work_record_revision) < 0))
      || !Array.isArray(event.groups) || !Array.isArray(event.unread_important_results)
      || (event.aggregation_scope !== undefined && event.aggregation_scope !== "selected_project_sessions")
      || (event.selected_project_ids !== undefined && (!Array.isArray(event.selected_project_ids)
        || event.selected_project_ids.length < 1 || event.selected_project_ids.length > 6
        || event.selected_project_ids.some(value => !nonempty(value))
        || new Set(event.selected_project_ids).size !== event.selected_project_ids.length))
      || !["known", "unknown"].includes(String(event.unread_status))
      || !Array.isArray(event.visibility_limitations) || !event.visibility_limitations.every(value => typeof value === "string")
      || !cardObject(event.visibility_counts) || !cardObject(event.page)
      || !(event.detail_page === undefined || event.detail_page === null || cardObject(event.detail_page))) return false;
    const counts = event.visibility_counts;
    const page = event.page;
    const countFields = ["visible_groups", "visible_items", "collapsed_groups", "collapsed_items"];
    if (!countFields.every(key => nonnegative(counts[key]))) return false;
    const pageFields = ["page_size", "offset", "total_groups", "total_visible_groups", "total_visible_items", "total_collapsed_groups", "total_collapsed_items"];
    if (!pageFields.every(key => nonnegative(page[key]))) return false;
    const pageSize = page.page_size as number;
    const offset = page.offset as number;
    const totalVisibleGroups = page.total_visible_groups as number;
    if (pageSize < 1 || pageSize > 50 || typeof page.has_more !== "boolean"
      || !(page.next_cursor === null || typeof page.next_cursor === "string" && page.next_cursor.length > 0)
      || (page.has_more ? typeof page.next_cursor !== "string" : page.next_cursor !== null)
      || page.total_groups !== page.total_visible_groups || totalVisibleGroups !== counts.visible_groups
      || page.total_visible_items !== counts.visible_items || page.total_collapsed_groups !== counts.collapsed_groups
      || page.total_collapsed_items !== counts.collapsed_items || offset > totalVisibleGroups
      || event.groups.length > pageSize || offset + event.groups.length > totalVisibleGroups
      || event.groups.some((group: unknown) => !this.validOverviewGroup(group))) return false;
    if (event.detail_page !== undefined && event.detail_page !== null) {
      const detail = event.detail_page as Record<string, unknown>;
      if (!this.validOverviewPageFacts(detail, detail.kind as "items" | "requirements", String(detail.group_kind),
        String(detail.group_id), detail.item_id === null ? null : String(detail.item_id))
        || event.state !== "visible" || event.groups.length !== 1) return false;
      const group = event.groups[0];
      if (group.kind !== detail.group_kind || group.id !== detail.group_id || group.visibility !== "visible") return false;
      if (detail.kind === "items") {
        if (!this.sameOverviewPageFacts(group.items_page, detail as WorkRecordsOverviewPageFacts)) return false;
      } else if (group.items_page !== null || group.items.length !== 1
        || group.items[0].item_id !== detail.item_id
        || !this.sameOverviewPageFacts(group.items[0].requirements_page, detail as WorkRecordsOverviewPageFacts)) return false;
    }
    if (event.snapshot_id !== null && (typeof event.snapshot_id !== "string" || !event.snapshot_id)) return false;
    if (!event.unread_important_results.every((item: unknown) => cardObject(item) && Array.isArray(item.group)
      && item.group.length === 2 && item.group.every((part: unknown) => typeof part === "string")
      && [item.item_id, item.requirement_id, item.result_id].every(nonempty))) return false;
    const hasUnreadTotal = Object.prototype.hasOwnProperty.call(event, "unread_important_results_total");
    const hasUnreadTruncated = Object.prototype.hasOwnProperty.call(event, "unread_important_results_truncated");
    const unreadTotal: unknown = event.unread_important_results_total;
    const unreadTruncated: unknown = event.unread_important_results_truncated;
    if (hasUnreadTotal && (!nonnegative(unreadTotal)
      || !hasUnreadTruncated || typeof unreadTruncated !== "boolean")) return false;
    if (hasUnreadTruncated && typeof unreadTruncated !== "boolean") return false;
    if (unreadTruncated === true) {
      if (event.unread_important_results.length !== 100
        || (hasUnreadTotal && (typeof unreadTotal !== "number" || unreadTotal <= event.unread_important_results.length))) return false;
    } else if (hasUnreadTotal && unreadTotal !== event.unread_important_results.length) return false;
    this.overviewPending.delete(event.request_id);
    return true;
  }
  private validSourceOverview(value: Record<string, unknown>): boolean {
    const source = value.source_snapshot;
    const id = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
    const digest = /^[0-9a-f]{64}$/;
    const bounded = (v: unknown): v is string => typeof v === "string" && v.trim().length > 0 && v.length <= 4096;
    const unknownCounts = (v: unknown): boolean => cardObject(v) && exactCardKeys(v, ["unknown"]) && v.unknown === 1;
    const validFact = (v: unknown): boolean => {
      if (!cardObject(v) || !exactCardKeys(v, ["binding_id", "worker_id", "worker_run_id", "host_start", "host_lifecycle", "operation_result", "verification"])
        || ![v.binding_id, v.worker_id, v.worker_run_id].every(x => typeof x === "string" && id.test(x))
        || v.operation_result !== "unknown" || v.verification !== "not_verified"
        || !cardObject(v.host_start) || !exactCardKeys(v.host_start, ["reservation_id", "parent_run_id", "turn_epoch"])
        || ![v.host_start.reservation_id, v.host_start.parent_run_id].every(x => typeof x === "string" && id.test(x))
        || !nonnegative(v.host_start.turn_epoch) || v.host_start.turn_epoch < 1
        || !cardObject(v.host_lifecycle) || !exactCardKeys(v.host_lifecycle, ["sequence", "status", "finish_reason"])
        || v.host_lifecycle.sequence !== v.host_start.turn_epoch
        || typeof v.host_lifecycle.status !== "string" || !["model_turn_finished", "blocked", "failed", "cancelled", "limit_reached", "unknown"].includes(v.host_lifecycle.status)
        || typeof v.host_lifecycle.finish_reason !== "string" || !["completed", "cancelled", "timeout", "error", "paused", "unknown"].includes(v.host_lifecycle.finish_reason)
        || (v.host_lifecycle.status === "unknown" && v.host_lifecycle.finish_reason !== "unknown")) return false;
      return true;
    };
    if (typeof value.id !== "string" || !/^linked-source-[0-9a-f]{64}$/.test(value.id)
      || value.status !== "unknown" || !unknownCounts(value.status_counts) || value.attention_status !== "unknown"
      || typeof value.visibility_timer_status !== "string" || !["unknown", "waiting", "not_viewed", "unavailable", "elapsed"].includes(value.visibility_timer_status)
      || typeof value.attention_reason !== "string" || !["publication_pending", "captured_requirement_unresolved", "superseded_snapshot_attention", "view_evidence_unavailable", "superseded_snapshot_unviewed", "view_not_confirmed", "worker_result_unverified"].includes(value.attention_reason)
      || !cardObject(source) || !exactCardKeys(source, ["scope", "mapping_claim", "project_id", "source_session_id", "source_revision", "captured_at", "copy_id", "intent_state", "card_id", "viewed_at", "superseded_snapshot_count", "superseded_unviewed_count"])
      || source.scope !== "authorized_source_session_only" || source.mapping_claim !== "not_asserted"
      || ![source.project_id, source.source_session_id].every(x => typeof x === "string" && id.test(x))
      || !nonnegative(source.source_revision) || !timestamp(source.captured_at) || source.captured_at.length > 64
      || typeof source.copy_id !== "string" || !digest.test(source.copy_id)
      || typeof source.intent_state !== "string" || !["pending", "reference_queued"].includes(source.intent_state)
      || !(source.card_id === null || cardId(source.card_id)) || !(source.viewed_at === null || timestamp(source.viewed_at))
      || !nonnegative(source.superseded_snapshot_count) || !nonnegative(source.superseded_unviewed_count)
      || source.superseded_unviewed_count > source.superseded_snapshot_count || !Array.isArray(value.items)) return false;
    return value.items.every((item: unknown) => cardObject(item) && bounded(item.status_at_capture)
      && item.status === "unknown" && unknownCounts(item.status_counts) && Array.isArray(item.current_results) && item.current_results.length === 0
      && Array.isArray(item.requirements) && item.requirements.every((req: unknown) => cardObject(req) && req.status === "unknown"
        && bounded(req.status_at_capture) && Array.isArray(req.worker_facts) && req.worker_facts.length <= 20 && req.worker_facts.every(validFact)));
  }
  private validOverviewGroup(value: unknown): value is WorkRecordsOverviewGroup {
    if (!cardObject(value) || (value.kind !== "project" && value.kind !== "independent" && value.kind !== "linked_source_snapshot")
      || !nonempty(value.id) || value.visibility !== "visible" || !nonempty(value.status)
      || !cardObject(value.status_counts) || !Array.isArray(value.items)
      || !nonnegative(value.item_count) || !nonnegative(value.requirement_count)
      || value.items.length > value.item_count
      || (value.source_session_id !== undefined && !nonempty(value.source_session_id))
      || (value.source_revision !== undefined && !nonnegative(value.source_revision))
      || (value.omitted_item_count !== undefined && !nonnegative(value.omitted_item_count))) return false;
    if (value.kind === "linked_source_snapshot" && !this.validSourceOverview(value)) return false;
    const hasItemsPage = Object.prototype.hasOwnProperty.call(value, "items_page");
    const itemsPage = value.items_page as WorkRecordsOverviewPageFacts | null | undefined;
    if (hasItemsPage && itemsPage === undefined) return false;
    if (hasItemsPage && itemsPage !== null
      && (!this.validOverviewPageFacts(itemsPage, "items", value.kind, value.id, null)
        || itemsPage.total !== value.item_count
        || itemsPage.offset + value.items.length > value.item_count
        || itemsPage.has_more !== (itemsPage.offset + value.items.length < value.item_count))) return false;
    if (hasItemsPage && itemsPage !== null && itemsPage !== undefined && itemsPage.offset === 0
      && itemsPage.total === value.items.length && itemsPage.has_more) return false;
    if (hasItemsPage && itemsPage === null && value.items.length !== 1) return false;
    if (!Object.values(value.status_counts).every(nonnegative)) return false;
    let requirementCount = 0;
    for (const item of value.items) {
      if (!cardObject(item) || !nonempty(item.item_id) || typeof item.title !== "string" || !nonempty(item.status)
        || (item.priority !== undefined && (!Number.isInteger(item.priority) || Number(item.priority) < 0 || Number(item.priority) > 5))
        || (item.omitted_requirement_count !== undefined && !nonnegative(item.omitted_requirement_count))
        || !cardObject(item.status_counts) || !Object.values(item.status_counts).every(nonnegative)
        || !Array.isArray(item.requirements) || !Array.isArray(item.current_results)) return false;
      requirementCount += item.requirements.length;
      if (!item.requirements.every((req: unknown) => {
        if (!cardObject(req) || !nonempty(req.requirement_id) || typeof req.text !== "string" || !nonempty(req.status)) return false;
        if (req.depends_on !== undefined
            && (!Array.isArray(req.depends_on) || !req.depends_on.every(nonempty))) return false;
        if (req.depends_on_refs !== undefined
            && (!Array.isArray(req.depends_on_refs) || req.depends_on_refs.length > 128
              || !req.depends_on_refs.every((ref: unknown) => cardObject(ref)
                && Object.keys(ref).length === 2 && nonempty(ref.item_id) && nonempty(ref.requirement_id)))) return false;
        if (req.history !== undefined) {
          if (!Array.isArray(req.history) || req.history.length > 10) return false;
          if (!req.history.every((row: unknown) => cardObject(row) && nonempty(row.run_id)
              && nonempty(row.result_id) && nonempty(row.status) && typeof row.summary === "string"
              && nonempty(row.verification_status)
              && (row.recorded_at === null || typeof row.recorded_at === "number" && Number.isFinite(row.recorded_at)))) return false;
        }
        return true;
      })) return false;
      if (Object.prototype.hasOwnProperty.call(item, "requirements_page")
        && (!this.validOverviewPageFacts(item.requirements_page, "requirements", value.kind, value.id, item.item_id)
          || item.requirements_page.offset + item.requirements.length > item.requirements_page.total
          || item.requirements_page.has_more !== (item.requirements_page.offset + item.requirements.length < item.requirements_page.total))) return false;
      if (!item.current_results.every((result: unknown) => cardObject(result) && nonempty(result.requirement_id)
        && nonempty(result.result_id) && nonempty(result.status)
        && ["viewed", "unread", "unknown"].includes(String(result.read_status)))) return false;
    }
    const completeItemCount = value.items.every(item => !Object.prototype.hasOwnProperty.call(item, "requirements_page")
      || item.requirements_page?.offset === 0 && item.requirements_page.total === item.requirements.length
        && !item.requirements_page.has_more);
    const completeGroup = !hasItemsPage || (itemsPage !== null && itemsPage !== undefined
      && itemsPage.offset === 0 && value.items.length === value.item_count);
    return (!completeGroup || !completeItemCount || requirementCount <= value.requirement_count)
      && (!completeGroup || !completeItemCount || requirementCount === value.requirement_count);
  }
  private acceptWorkCardPresent(event: unknown): event is WorkCardPresentEvent {
    if (!cardObject(event) || event.type !== "work-card.present" || event.session_id !== this.currentConversationId
      || typeof event.presentation_id !== "string" || !WORK_CARD_UUID.test(event.presentation_id)
      || !cardId(event.card_id) || !validWorkCard(event.card, this.currentConversationId, event.card_id)) return false;
    const allowed = new Set(["type", "session_id", "presentation_id", "card_id", "card"]);
    if (Object.keys(event).some(key => !allowed.has(key))) return false;
    const priorCardId = this.workCardPresentationIds.get(event.presentation_id);
    if (priorCardId !== undefined && priorCardId !== event.card_id) return false;
    this.workCardPresentationIds.set(event.presentation_id, event.card_id);
    if (event.card.snapshot.kind === "linked_item_copy") {
      this.linkedItemCopyIdentities.set(event.card_id, {
        projectId: event.card.snapshot.project_id, itemId: event.card.snapshot.item_id,
      });
    } else if (event.card.snapshot.kind === "linked_project_session_copy") {
      this.linkedProjectSessionCopyIdentities.set(event.card_id, {
        projectId: event.card.snapshot.source.project_id, mappingClaim: event.card.snapshot.source.mapping_claim,
      });
    }
    return true;
  }

  private retireCardRequests(): void {
    for (const [key, value] of this.snapshotViewTimers) { window.clearTimeout(value.timer); this.cardRetired.add(key); }
    for (const key of this.snapshotViewAmbiguous) this.cardRetired.add(key);
    this.snapshotViewTimers.clear();
    this.snapshotViewAmbiguous.clear();
    for (const key of this.cardPending.keys()) this.cardRetired.add(key);
    this.cardPending.clear();
  }

  private sendCard(payload: Record<string, unknown>, key: string, itemId?: string): void {
    if (!this.currentConversationId) throw new Error("work-card requires a current session banner");
    if (this.cardPending.has(key) || this.cardRetired.has(key)) throw new Error("work-card request pending or retired; reconnect before reuse");
    this.cardPending.set(key, { session: this.currentConversationId, key, itemId });
    try { this.send(payload); } catch (error) { this.cardPending.delete(key); throw error; }
  }

  /** Caller owns the stable UUID; retries explicitly reuse it. */
  createWorkCard(requestId: string, itemId?: string): void {
    if (typeof requestId !== "string" || !WORK_CARD_UUID.test(requestId)) throw new Error("invalid work-card request UUID");
    if (itemId !== undefined && (typeof itemId !== "string" || !WORK_ITEM_ID.test(itemId))) throw new Error("invalid work-card item identity");
    this.sendCard({type: "work-card.create", request_id: requestId, ...(itemId === undefined ? {} : {item_id: itemId})}, `create:${requestId}`, itemId);
  }

  getWorkCard(id: string): void {
    if (!cardId(id)) throw new Error("invalid work-card identity");
    this.sendCard({type: "work-card.get", card_id: id}, `get:${id}`);
  }

  /** Explicit acknowledgement only: retrieval never marks a card seen. */
  markWorkCardSeen(id: string): void {
    if (!cardId(id)) throw new Error("invalid work-card identity");
    this.sendCard({type: "work-card.seen", card_id: id}, `seen:${id}`);
  }

  /** Send an idempotent receipt only for a presentation accepted on this client.
   * It is not a local durability or user-view acknowledgement; send failures remain retryable.
   */
  acknowledgeWorkCardReceived(identity: { sessionId: string; presentationId: string; cardId: string }): void {
    if (!cardObject(identity) || Object.keys(identity).length !== 3
      || Object.keys(identity).some(key => key !== "sessionId" && key !== "presentationId" && key !== "cardId")
      || identity.sessionId !== this.currentConversationId || !WORK_CARD_UUID.test(identity.presentationId)
      || !cardId(identity.cardId) || this.workCardPresentationIds.get(identity.presentationId) !== identity.cardId) {
      throw new Error("invalid, stale, or unaccepted work-card receipt identity");
    }
    const message: WorkCardReceivedMessage = { type: "work-card.received", session_id: identity.sessionId, presentation_id: identity.presentationId, card_id: identity.cardId };
    this.send(message);
  }
  /** Explicit snapshot detail expansion, separate from receipt and result viewing. */
  acknowledgeWorkCardSnapshotView(identity: {sessionId: string; presentationId: string; cardId: string}): void {
    if (!cardObject(identity) || Object.keys(identity).length !== 3 || identity.sessionId !== this.currentConversationId
      || !WORK_CARD_UUID.test(identity.presentationId) || !cardId(identity.cardId)
      || !(this.linkedItemCopyIdentities.has(identity.cardId) || this.linkedProjectSessionCopyIdentities.has(identity.cardId))) throw new Error("invalid or stale snapshot view identity");
    const key = `snapshot-view:${identity.presentationId}:${identity.cardId}`;
    if (this.cardPending.has(key) || this.cardRetired.has(key)) return;
    this.sendCard({type: "work-card.snapshot.view", session_id: identity.sessionId, presentation_id: identity.presentationId, card_id: identity.cardId}, key, identity.cardId);
    const token = {};
    const timer = window.setTimeout(() => {
      if (this.snapshotViewTimers.get(key)?.token !== token || this.currentConversationId !== identity.sessionId) return;
      this.snapshotViewTimers.delete(key);
      this.cardPending.delete(key);
      this.snapshotViewAmbiguous.add(key);
      const event: WorkCardSnapshotViewTimeoutEvent = {type: "work-card.snapshot.view.timeout", session_id: identity.sessionId, presentation_id: identity.presentationId, card_id: identity.cardId};
      for (const handler of this.handlers) handler(event);
    }, 15_000);
    this.snapshotViewTimers.set(key, {timer, token});
  }
  /** Renderer-attested explicit result-detail expansion; this is not delivery or acceptance proof. */
  acknowledgeWorkCardResultView(identity: {sessionId: string; presentationId: string; cardId: string; resultId: string; requirementId: string; requestId?: string}): void {
    if (!cardObject(identity) || Object.keys(identity).some(k => !["sessionId", "presentationId", "cardId", "resultId", "requirementId", "requestId"].includes(k))
      || identity.sessionId !== this.currentConversationId || !WORK_CARD_UUID.test(identity.presentationId)
      || !cardId(identity.cardId) || typeof identity.resultId !== "string" || !identity.resultId || identity.resultId.length > 256
      || typeof identity.requirementId !== "string" || !identity.requirementId || identity.requirementId.length > 256
      || (identity.requestId !== undefined && (!WORK_CARD_UUID.test(identity.requestId) || this.usedResultViewRequestIds.has(identity.requestId)))) {
      throw new Error("invalid or stale work-card result identity");
    }
    const key = identity.requestId ? `result-view-id:${identity.requestId}` : `result-view:${identity.presentationId}:${identity.resultId}:${identity.requirementId}`;
    this.sendCard({type: "work-card.result.view", session_id: identity.sessionId,
      presentation_id: identity.presentationId, card_id: identity.cardId,
      result_id: identity.resultId, requirement_id: identity.requirementId,
      ...(identity.requestId ? {request_id: identity.requestId} : {})}, key, identity.cardId);
    const pending = this.cardPending.get(key);
    if (pending && identity.requestId) {
      pending.resultIdentity = {presentationId: identity.presentationId, resultId: identity.resultId, requirementId: identity.requirementId};
      this.usedResultViewRequestIds.add(identity.requestId);
    }
  }

  /** Drop a closed detail's pending response; request IDs are never reused. */
  cancelWorkCardResultView(requestId: string): void {
    if (typeof requestId === "string" && WORK_CARD_UUID.test(requestId)) this.cardPending.delete(`result-view-id:${requestId}`);
  }

  listWorkCards(options: {limit?: number; beforeCardId?: string} = {}): string {
    if (!cardObject(options) || Object.keys(options).some(key => key !== "limit" && key !== "beforeCardId")) throw new Error("invalid work-card list options");
    if (options.limit !== undefined && (!Number.isInteger(options.limit) || options.limit < 1 || options.limit > 20)) throw new Error("invalid work-card list limit");
    if (options.beforeCardId !== undefined && !cardId(options.beforeCardId)) throw new Error("invalid work-card cursor");
    // Serial pages prevent an older page from arriving after a newer page.
    if ([...this.cardPending.keys()].some(key => key.startsWith("list:"))) throw new Error("work-card list request already pending");
    const requestId = crypto.randomUUID();
    if (!WORK_CARD_UUID.test(requestId) || this.usedCardListIds.has(requestId)) throw new Error("invalid or reused work-card list request UUID");
    this.usedCardListIds.add(requestId);
    this.sendCard({type: "work-card.list", request_id: requestId, ...(options.limit === undefined ? {} : {limit: options.limit}), ...(options.beforeCardId === undefined ? {} : {before_card_id: options.beforeCardId})}, `list:${requestId}`);
    return requestId;
  }

  private acceptWorkCardReceiptBacklog(event: unknown): event is WorkCardReceiptBacklogEvent {
    const fields = ["type", "session_id", "pending_count", "deferred_count", "legacy_untracked_count", "retry_batch_size", "batch_limit", "max_send_attempts"];
    if (!cardObject(event) || event.type !== "work-card.receipt.backlog" || Object.keys(event).length !== fields.length
      || Object.keys(event).some(key => !fields.includes(key)) || event.session_id !== this.currentConversationId      || typeof event.pending_count !== "number" || !Number.isSafeInteger(event.pending_count) || event.pending_count < 0
      || typeof event.deferred_count !== "number" || !Number.isSafeInteger(event.deferred_count) || event.deferred_count < 0
      || typeof event.legacy_untracked_count !== "number" || !Number.isSafeInteger(event.legacy_untracked_count) || event.legacy_untracked_count < 0
      || typeof event.retry_batch_size !== "number" || !Number.isSafeInteger(event.retry_batch_size) || event.retry_batch_size < 0
      || typeof event.batch_limit !== "number" || !Number.isSafeInteger(event.batch_limit) || event.batch_limit < 1 || event.batch_limit > 100
      || typeof event.max_send_attempts !== "number" || !Number.isSafeInteger(event.max_send_attempts) || event.max_send_attempts < 1 || event.max_send_attempts > 10
      || event.retry_batch_size > event.pending_count || event.retry_batch_size > event.batch_limit
      || !(event.pending_count > event.retry_batch_size || event.deferred_count > 0 || event.legacy_untracked_count > 0)) return false;
    const signature = [event.pending_count, event.deferred_count, event.legacy_untracked_count,
      event.retry_batch_size, event.batch_limit, event.max_send_attempts].join(":");
    if (signature === this.receiptBacklogSignature) return false;
    this.receiptBacklogSignature = signature;
    return true;
  }
  private acceptWorkCardReceivedResult(event: unknown): event is WorkCardReceivedResultEvent {
    if (!cardObject(event) || event.type !== "work-card.received.done"
      || !["received", "already_received", "invalid_request", "session_unavailable", "project_unbound", "session_changed", "history_unavailable", "rejected", "unavailable"].includes(String(event.status))
      || typeof event.message !== "string" || event.message.length > 512
      || event.session_id !== this.currentConversationId || typeof event.presentation_id !== "string" || !WORK_CARD_UUID.test(event.presentation_id)
      || !cardId(event.card_id) || this.workCardPresentationIds.get(event.presentation_id) !== event.card_id) return false;
    const success = event.status === "received" || event.status === "already_received";
    const allowed = new Set(["type", "status", "message", "session_id", "presentation_id", "card_id", ...(success ? ["received_at"] : [])]);
    if (Object.keys(event).some(key => !allowed.has(key))) return false;
    return success ? timestamp(event.received_at) : event.received_at === undefined;
  }
  private acceptCardEvent(event: unknown): event is WorkCardEvent {
    if (cardObject(event) && event.type === "work-card.snapshot.view.done") {
      if (!exactCardKeys(event, ["type", "session_id", "presentation_id", "card_id", "status", "message"])
        || event.session_id !== this.currentConversationId || typeof event.presentation_id !== "string" || !WORK_CARD_UUID.test(event.presentation_id)
        || !cardId(event.card_id) || typeof event.message !== "string" || event.message.length > 512 || typeof event.status !== "string"
        || !["acknowledged", "already_acknowledged", "invalid_request", "session_unavailable", "project_unbound", "session_changed", "history_unavailable", "rejected", "unavailable"].includes(event.status)) return false;
      const key = `snapshot-view:${event.presentation_id}:${event.card_id}`;
      const pending = this.cardPending.get(key);
      if (!pending || pending.session !== event.session_id || pending.itemId !== event.card_id) return false;
      const success = event.status === "acknowledged" || event.status === "already_acknowledged";
      // Without a request id, a failure on any retry may belong to an older
      // attempt. Ignore it; only a durable success can safely settle the retry.
      if (!success && this.snapshotViewAmbiguous.has(key)) return false;
      const timer = this.snapshotViewTimers.get(key);
      if (timer) window.clearTimeout(timer.timer);
      this.snapshotViewTimers.delete(key);
      this.cardPending.delete(key);
      if (success) { this.snapshotViewAmbiguous.delete(key); this.cardRetired.add(key); }
      else this.snapshotViewAmbiguous.add(key);
      return true;
    }
    if (cardObject(event) && event.type === "work-card.receipt.backlog") return this.acceptWorkCardReceiptBacklog(event);
    if (cardObject(event) && event.type === "work-card.received.done") return this.acceptWorkCardReceivedResult(event);
    if (!cardObject(event) || typeof event.type !== "string" || typeof event.status !== "string" || typeof event.message !== "string") return false;
    const op = ({"work-card.create.done":"create", "work-card.get.done":"get", "work-card.seen.done":"seen", "work-card.list.done":"list", "work-card.result.view.done":"result-view"} as Record<string, string>)[event.type];
    if (!op) return false;
    const usesRequestId = op === "create" || op === "list";
    const identity = usesRequestId ? event.request_id : event.card_id;
    if (op === "result-view") {
      if (typeof event.session_id !== "string" || typeof event.presentation_id !== "string" || !WORK_CARD_UUID.test(event.presentation_id)
        || !cardId(event.card_id) || typeof event.result_id !== "string" || typeof event.requirement_id !== "string") return false;
    } else if (usesRequestId ? typeof identity !== "string" || !WORK_CARD_UUID.test(identity) : !cardId(identity)) return false;
    if (op === "result-view" && event.request_id !== undefined && (typeof event.request_id !== "string" || !WORK_CARD_UUID.test(event.request_id))) return false;
    const key = op === "result-view" ? (event.request_id ? `result-view-id:${event.request_id}` : `result-view:${event.presentation_id}:${event.result_id}:${event.requirement_id}`) : `${op}:${identity}`;
    const pending = this.cardPending.get(key);
    if (!pending || pending.session !== this.currentConversationId) return false;
    if (op === "result-view" && pending.resultIdentity && (pending.resultIdentity.presentationId !== event.presentation_id
      || pending.resultIdentity.resultId !== event.result_id || pending.resultIdentity.requirementId !== event.requirement_id)) return false;
    const errors = new Set(["invalid_request","session_unavailable","project_unbound","session_changed","history_unavailable","request_conflict","ledger_unavailable","incomplete","rejected","save_failed","save_uncertain","not_found","invalid_cursor","unavailable"]);
    let valid = false;
    if (errors.has(event.status)) {
      const allowed = op === "result-view"
        ? new Set(["type", "status", "message", "session_id", "presentation_id", "card_id", "result_id", "requirement_id", "request_id"])
        : new Set(["type", "status", "message", usesRequestId ? "request_id" : "card_id"]);
      valid = Object.keys(event).every(k => allowed.has(k));
      if (op === "result-view") valid = valid && event.session_id === pending.session
        && key === pending.key && event.card_id === pending.itemId;
    } else if (op === "create" && (event.status === "saved" || event.status === "already_saved")) {
      valid = cardId(event.card_id) && validCardScope(event.source_scope, pending.session) && validWorkCard(event.card, pending.session, event.card_id, pending.itemId)
        && cardObject(event.card) && cardObject(event.card.snapshot) && cardObject(event.source_scope)
        && event.card.snapshot.kind !== "linked_item_copy"
        && event.card.snapshot.kind !== "linked_project_session_copy"
        && (pending.itemId === undefined ? event.card.snapshot.kind !== "independent" : event.card.snapshot.kind === "independent")
        && (event.card.snapshot.kind === "independent" ? event.source_scope.kind === "independent" : event.source_scope.project_id === event.card.snapshot.project_id);
    } else if (op === "get" && event.status === "found") {
      const snapshot = cardObject(event.card) && cardObject(event.card.snapshot) ? event.card.snapshot : undefined;
      const linkedItemSnapshot = snapshot?.kind === "linked_item_copy"
        ? snapshot as unknown as LinkedItemCopySnapshot : undefined;
      const linkedProjectSnapshot = snapshot?.kind === "linked_project_session_copy"
        ? snapshot as unknown as LinkedProjectSessionCopySnapshot : undefined;
      const ordinarySnapshot = snapshot && snapshot.kind !== "linked_item_copy"
        && snapshot.kind !== "linked_project_session_copy" ? snapshot : undefined;
      valid = cardId(identity) && cardObject(event.card) && !!snapshot
        && (linkedItemSnapshot
          ? validLinkedParentCardScope(event.source_scope, pending.session, linkedItemSnapshot)
          : linkedProjectSnapshot
            ? validLinkedProjectSessionCopyScope(event.source_scope, pending.session, linkedProjectSnapshot)
            : snapshot?.kind === "project_scoped"
              ? cardObject(event.source_scope)
                && exactCardKeys(event.source_scope, ["session_id", "project_id"])
                && event.source_scope.session_id === pending.session
                && event.source_scope.project_id === this.cardProjectId
              : validCardScope(event.source_scope, pending.session))
        && validWorkCard(event.card, pending.session, identity)
        && (linkedItemSnapshot !== undefined || linkedProjectSnapshot !== undefined
          || (!!ordinarySnapshot && (ordinarySnapshot.kind === "project_scoped"
            || (ordinarySnapshot.kind === "independent"
              ? cardObject(event.source_scope) && event.source_scope.kind === "independent"
              : cardObject(event.source_scope) && event.source_scope.project_id === ordinarySnapshot.project_id))));
      if (valid && linkedItemSnapshot && typeof identity === "string") {
        this.linkedItemCopyIdentities.set(identity, {
          projectId: linkedItemSnapshot.project_id, itemId: linkedItemSnapshot.item_id,
        });
      } else if (valid && linkedProjectSnapshot && typeof identity === "string") {
        this.linkedProjectSessionCopyIdentities.set(identity, {
          projectId: linkedProjectSnapshot.source.project_id,
          mappingClaim: linkedProjectSnapshot.source.mapping_claim,
        });
      }
    } else if (op === "seen" && (event.status === "acknowledged" || event.status === "already_acknowledged")) {
      const linkedItem = cardObject(event.source_scope) && event.source_scope.kind === "linked_item_copy";
      const linkedProject = cardObject(event.source_scope) && event.source_scope.kind === "linked_project_session_copy";
      const knownItem = typeof identity === "string" ? this.linkedItemCopyIdentities.get(identity) : undefined;
      const knownProject = typeof identity === "string" ? this.linkedProjectSessionCopyIdentities.get(identity) : undefined;
      valid = timestamp(event.viewed_at) && (linkedItem
        ? !!knownItem && validLinkedParentCardScope(event.source_scope, pending.session)
          && cardObject(event.source_scope) && event.source_scope.project_id === knownItem.projectId
          && event.source_scope.item_id === knownItem.itemId
        : linkedProject
          ? !!knownProject && validLinkedProjectSessionCopyScope(event.source_scope, pending.session)
            && cardObject(event.source_scope) && event.source_scope.project_id === knownProject.projectId
            && event.source_scope.mapping_claim === knownProject.mappingClaim
          : validCardScope(event.source_scope, pending.session));
    } else if (op === "result-view" && (event.status === "acknowledged" || event.status === "already_acknowledged")) {
      valid = event.session_id === pending.session && typeof event.requirement_id === "string"
        && key === pending.key && event.card_id === pending.itemId;
      if (event.local_assistant_reply_excerpt_detail !== undefined) {
        const d = event.local_assistant_reply_excerpt_detail;
        valid = valid && cardObject(d) && exactCardKeys(d, ["session_id", "item_id", "requirement_id", "result_id", "label", "escaped_excerpt_text", "source_availability", "result_status", "verification_status"])
          && d.session_id === event.session_id && d.requirement_id === event.requirement_id && d.result_id === event.result_id
          && typeof d.item_id === "string" && d.item_id.length > 0 && d.item_id.length <= 256
          && typeof d.label === "string" && d.label.length > 0 && d.label.length <= 4096
          && typeof d.escaped_excerpt_text === "string" && d.escaped_excerpt_text.length > 0 && d.escaped_excerpt_text.length <= 200000
          && !/[<>"']|&(?!amp;|lt;|gt;|quot;|#x27;)/.test(d.escaped_excerpt_text)
          && (d.source_availability === "available" || d.source_availability === "unavailable")
          && d.result_status === "unknown" && d.verification_status === "not_verified";
      }
    } else if (op === "list" && event.status === "ok") {
      valid = cardObject(event.source_scope) && event.source_scope.session_id === pending.session && Object.keys(event.source_scope).length === 1
        && Array.isArray(event.items) && event.items.length <= 20 && event.items.every(validIndexItem)
        && new Set(event.items.map((item: WorkCardIndexItem) => item.card_id)).size === event.items.length
        && typeof event.has_more === "boolean" && (event.has_more ? event.items.length > 0 && cardId(event.next_cursor) && event.next_cursor === event.items[event.items.length - 1].card_id : event.next_cursor === null);
    }
    if (!valid) return false;
    // Do not forward undocumented fields as part of a structured success event.
    if (!errors.has(event.status)) {
      const fields: Record<string, string[]> = {
        create: ["type", "status", "message", "request_id", "card_id", "card", "source_scope"],
        get: ["type", "status", "message", "card_id", "card", "source_scope"],
        seen: ["type", "status", "message", "card_id", "viewed_at", "source_scope"],
        "result-view": ["type", "status", "message", "session_id", "presentation_id", "card_id", "result_id", "requirement_id", "request_id", "local_assistant_reply_excerpt_detail"],
        list: ["type", "status", "message", "request_id", "items", "has_more", "next_cursor", "source_scope"],
      };
      if (Object.keys(event).some(k => !fields[op].includes(k))) return false;
    }
    this.cardPending.delete(key);
    return true;
  }

  sendEvolutionRequest(value: unknown): void {
    const request = normalizeEvolutionHostRequest(value, this.currentConversationId);
    this.send({ type: request.type, conversation_id: request.conversation_id });
  }

  private updateEvolutionConversationId(sessionId: string | null, bindingChanged = false): void {
    const disconnected = sessionId === null && !!this.currentConversationId;
    if ((sessionId ?? "") !== this.currentConversationId || bindingChanged) {
      this.retireCardRequests(); this.overviewPending.clear(); this.receiptBacklogSignature = "";
      this.workCardPresentationIds.clear();
      this.linkedItemCopyIdentities.clear();
      this.linkedProjectSessionCopyIdentities.clear();
    }
    this.currentConversationId = sessionId ?? "";
    if (disconnected) {
      this.autoRecognitionPending = null;
      for (const handler of this.handlers) handler({ type: "client.connection.closed" });
    }
    let evolutionSessionId: string | null = null;
    if (sessionId !== null) {
      try {
        evolutionSessionId = normalizeEvolutionHostRequest({ type: "evolution.inspect", conversation_id: sessionId }).conversation_id;
      } catch (error) {
        if (!(error instanceof TypeError)) throw error;
      }
    }
    const update = window.myAgentDesktop?.setEvolutionConversationId?.(evolutionSessionId);
    if (update) void update.catch(() => undefined);
  }

  sendMessage(
    text: string,
    attachmentIds?: string[],
    opts?: {
      forceAgent?: boolean;
      activeDoc?: string;
      activeDocSelection?: string;
      turnMode?: "agent" | "ask";
      requestId?: string;
      sessionId?: string;
      companionWorldContext?: CompanionWorldContextPayload;
    },
  ): { requestId: string; sessionId: string | null } {
    const requestId = opts?.requestId ?? crypto.randomUUID();
    const sessionId = opts?.sessionId ?? (this.currentConversationId || null);
    if (opts?.sessionId && opts.sessionId !== this.currentConversationId) {
      throw new Error("user message session does not match the active conversation");
    }
    const payload: Record<string, unknown> = {
      type: "user.message",
      request_id: requestId,
      ...(sessionId ? { session_id: sessionId } : {}),
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
    return { requestId, sessionId };
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

  sendPromptResponse(requestId: string, runId: string, sessionId: string, text: string): void {
    if (!WORK_CARD_UUID.test(requestId) || !runId.trim() || !sessionId.trim()
      || typeof text !== "string" || text.length > 4000) {
      throw new Error("tool prompt response requires a request UUID, run, session, and text up to 4000 characters");
    }
    if (sessionId !== this.currentConversationId) {
      throw new Error("tool prompt response session does not match the active conversation");
    }
    this.send({ type: "prompt.response", request_id: requestId, run_id: runId, session_id: sessionId, text });
  }

  sendExecutionStop(runId: string): void {
    const targetRunId = typeof runId === "string" ? runId.trim() : "";
    if (!targetRunId) throw new Error("execution stop requires a run id");
    this.send({ type: "execution.stop", run_id: targetRunId });
  }

  sendWorkInstruction(requestId: string, sessionId: string, runId: string, text: string): void {
    if (!WORK_CARD_UUID.test(requestId) || !sessionId.trim() || !runId.trim() || !text.trim()) {
      throw new Error("work instruction requires a request UUID, session, run, and non-empty text");
    }
    this.send({ type: "work.instruction", request_id: requestId, session_id: sessionId, run_id: runId, text });
  }


  sendParallelChatMessage(text: string, requestId: string): void {
    if (!WORK_CARD_UUID.test(requestId) || typeof text !== "string" || !text.trim()) {
      throw new Error("parallel chat requires a request UUID and non-empty text");
    }
    this.send({ type: "chat.message", request_id: requestId, text });
  }


  cancelParallelChat(requestId: string): void {
    if (!WORK_CARD_UUID.test(requestId)) throw new Error("parallel chat cancellation requires a request UUID");
    this.send({ type: "chat.cancel", request_id: requestId });
  }

  sendTurnCancel(): void {
    this.send({ type: "turn.cancel" });
  }

  acceptProposal(proposalId: string, options?: { confirm?: boolean; requestId?: string }): void {
    this.send({
      type: "proposal.accept",
      proposal_id: proposalId,
      ...(options?.confirm ? { confirm: true } : {}),
      ...(options?.requestId ? { request_id: options.requestId } : {}),
    });
  }

  rejectProposal(proposalId: string): void {
    this.send({ type: "proposal.reject", proposal_id: proposalId });
  }

  listMemories(): void {
    this.send({ type: "memory.list" });
  }

  listSessions(): void {
    this.send({ type: "session.list" });
  }

  private fetchSessionListEphemeral(lane: SidecarLane, timeoutMs = 12_000): Promise<SessionListItem[]> {
    return new Promise((resolve, reject) => {
      void this.resolveSidecarInfo(lane).then((info) => {
        const url = `ws://${info.host}:${info.port}`;
        const ws = new WebSocket(url);
        let settled = false;
        const finish = (fn: () => void) => {
          if (settled) return;
          settled = true;
          window.clearTimeout(timeout);
          try { ws.close(); } catch { /* ignore */ }
          fn();
        };
        const timeout = window.setTimeout(() => finish(() => reject(new Error(`session.list timeout (${lane})`))), timeoutMs);
        ws.addEventListener("open", () => ws.send(JSON.stringify({ type: "session.list" })));
        ws.addEventListener("message", (ev) => {
          try {
            const data = JSON.parse(String(ev.data)) as { type?: string; sessions?: SessionListItem[] };
            if (data.type !== "session.list" || !Array.isArray(data.sessions)) return;
            const sessions = data.sessions;
            finish(() => resolve(sessions.map((session) => ({ ...session, execution_lane: session.execution_lane ?? lane }))));
          } catch { /* wait for a valid frame */ }
        });
        ws.addEventListener("error", () => finish(() => reject(new Error(`WebSocket error: ${url}`))));
        ws.addEventListener("close", () => finish(() => reject(new Error(`WebSocket closed before session.list (${lane})`))));
      }).catch(reject);
    });
  }

  applySessionListPush(sessions: SessionListItem[]): SessionListItem[] {
    const mapped = sessions.map((session) => ({
      ...session,
      execution_lane: this.legacyLaneQueriesAllowed ? session.execution_lane ?? this.lane : this.lane,
    }));
    if (this.lane === "sandbox") this.sandboxSessionsCache = mapped;
    else this.stableSessionsCache = mapped;
    return this.legacyLaneQueriesAllowed
      ? mergeSessionLists(this.stableSessionsCache, this.sandboxSessionsCache)
      : mapped;
  }

  async listSessionsMerged(): Promise<SessionListItem[]> {
    if (this.sessionListMergedInflight) return this.sessionListMergedInflight;
    const run = async () => {
      const capabilities = await this.laneCapabilities();
      this.legacyLaneQueriesAllowed = isLegacyLaneQueryAllowed(capabilities);
      if (!this.legacyLaneQueriesAllowed) {
        try { this.listSessions(); } catch { /* current socket may be reconnecting */ }
        return this.lane === "sandbox" ? this.sandboxSessionsCache : this.stableSessionsCache;
      }
      const [stable, sandbox] = await Promise.all([
        this.fetchSessionListEphemeral("stable").catch(() => null),
        this.fetchSessionListEphemeral("sandbox", 8_000).catch(() => null),
      ]);
      if (stable) this.stableSessionsCache = stable;
      if (sandbox) this.sandboxSessionsCache = sandbox;
      return mergeSessionLists(this.stableSessionsCache, this.sandboxSessionsCache);
    };
    this.sessionListMergedInflight = run().finally(() => { this.sessionListMergedInflight = null; });
    return this.sessionListMergedInflight;
  }

  async createSandboxSession(): Promise<void> {
    // Keep the current sidecar. The experiment tree is a write fence, not a second process.
    // Switching lanes drops the chat whenever the other runtime is down or restarting.
    if (this.ws?.readyState !== WebSocket.OPEN) await this.ensureLane(this.lane);
    this.updateEvolutionConversationId(null);
    this.send({ type: "session.create_sandbox" });
  }

  async openSession(sessionId: string, lane: SidecarLane = this.lane): Promise<void> {
    await this.ensureLane(lane);
    this.updateEvolutionConversationId(null);
    this.send({ type: "session.open", session_id: sessionId });
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

  listRoleModels(): void {
    this.send({ type: "llm_role_models.get" });
  }

  getApprovalPreferences(): void {
    const api = window.myAgentDesktop?.getApprovalPreferences;
    if (!api) throw new Error("trusted Electron approval control is unavailable");
    void api().then((event) => this.handlers.forEach((handler) => handler(event))).catch(() => {
      this.handlers.forEach((handler) => handler({ type: "approval_preferences.state", status: "rejected", state_known: false, approval_mode: "manual", saved_approval_mode: "manual", available_modes: ["manual", "auto", "full"], message: "可信审批控制接口不可用；当前实际审批档位未知，切换已禁用，请重新打开设置重试。" }));
    });
  }

  setApprovalMode(mode: ApprovalPreferenceMode): void {
    if (mode !== "manual" && mode !== "auto" && mode !== "full") throw new Error("invalid approval mode");
    const api = window.myAgentDesktop?.setApprovalMode;
    if (!api) throw new Error("trusted Electron approval control is unavailable");
    void api(mode).then((event) => this.handlers.forEach((handler) => handler(event))).catch(() => {
      this.handlers.forEach((handler) => handler({ type: "approval_preferences.done", status: "rejected", approval_mode: "manual", saved_approval_mode: "manual", available_modes: ["manual", "auto", "full"], message: "可信审批控制接口不可用；设置结果未能确认，当前实际档位未知，切换已禁用，请重新打开设置重试。" }));
    });
  }

  setRoleModel(role: "main" | "subagent", model: string): void {
    if ((role !== "main" && role !== "subagent") || typeof model !== "string" || !model.trim()) throw new Error("invalid role model selection");
    this.send({ type: "llm_role_models.set", role, model });
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
    this.updateEvolutionConversationId(null);
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
    this.updateEvolutionConversationId(null);
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
    this.updateEvolutionConversationId(null);
    this.send({
      type: "project.thread.new",
      ...(projectId ? { project_id: projectId } : {}),
    });
  }

  restartToday(requestId?: string): void {
    this.updateEvolutionConversationId(null);
    this.send({
      type: "session.restart_today",
      ...(requestId ? { request_id: requestId } : {}),
    });
  }

  listHostScope(): void {
    this.send({ type: "host_scope.list" });
  }

  setTurnMode(mode: "agent" | "ask", options?: { confirm?: boolean; requestId?: string }): void {
    this.send({
      type: "session.set_turn_mode",
      mode,
      ...(options?.confirm ? { confirm: true } : {}),
      ...(options?.requestId ? { request_id: options.requestId } : {}),
    });
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
  const deadline = Date.now() + 30_000;
  let sidecar: { host: string; port: number } | null = null;
  while (Date.now() < deadline) {
    sidecar = await api.getSidecar();
    if (sidecar) break;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  if (!sidecar) {
    throw new Error("Python sidecar not ready");
  }
  const client = new AgentWsClient(sidecar.host, sidecar.port);
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
      getEvolutionState?: () => Promise<{ status: "queued" | "disabled"; message?: string }>;
      beginEvolution?: () => Promise<{ status: "queued" | "disabled"; message?: string }>;
      stopEvolution?: () => Promise<{ status: "queued" | "disabled"; message?: string }>;
      setEvolutionConversationId?: (sessionId: string | null) => Promise<boolean>;
      onEvolutionRequest?: (handler: (request: EvolutionHostRequest) => void) => () => void;
      getSidecar: () => Promise<{ host: string; port: number } | null>;
      getApprovalPreferences?: () => Promise<ApprovalPreferencesEvent & { type: "approval_preferences.state" }>;
      setApprovalMode?: (mode: ApprovalPreferenceMode) => Promise<ApprovalPreferencesEvent & { type: "approval_preferences.done" }>;
      getSessionLaneCapabilities?: () => Promise<SessionLaneCapabilities>;
      ensureSandboxSidecar?: () => Promise<{ ok: boolean; host?: string; port?: number; message?: string }>;
      restartSandboxSidecar?: () => Promise<{ ok: boolean; host?: string; port?: number; message?: string }>;
      getSandboxSidecar?: () => Promise<{ host: string; port: number } | null>;
      syncSandboxWorktree?: () => Promise<{ ok: boolean; message: string; detail?: string }>;
      resetShellDevMainLane?: () => Promise<{ ok: boolean; message: string; mainUrl?: string }>;
      switchSidecarCandidate?: (root: string) => Promise<Record<string, unknown>>;
      switchToCli: () => Promise<void>;
      openWorkbench?: () => Promise<void>;
      openExternal?: (url: string) => Promise<boolean>;
      openBilibiliSearch?: (query: string, bounds: { x: number; y: number; width: number; height: number }) => Promise<boolean>;
      setBilibiliViewState?: (state: { visible: boolean; bounds?: { x: number; y: number; width: number; height: number } }) => Promise<boolean>;
      getShellDevPreviewUrl?: () => Promise<string>;
      getShellDevMainUrl?: () => Promise<string>;
      promoteShellDevPreview?: () => Promise<{ ok: boolean; mainUrl?: string; previewUrl?: string; message?: string }>;
      resetShellDevMain?: () => Promise<{ ok: boolean; message?: string; mainUrl?: string }>;
      getSandboxAdoptStatus?: () => Promise<Record<string, unknown>>;
      inspectManagedSourceReview?: () => Promise<{ ok: true; report: Record<string, any> } | { ok: false; message: string }>;
      proposeManagedSourceSelection?: (request: { format: 1; mode: "managed_source_selection_request_v1"; reviewDigest: string; selectedPaths: string[] }) => Promise<{ ok: true; proposal: Record<string, unknown> } | { ok: false; message: string }>;
      createManagedSourceCheckpoint?: (request: { format: 1; mode: "managed_source_checkpoint_request_v1"; reviewDigest: string; proposalDigest: string; selectedPaths: string[] }) => Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; message: string }>;
      executeManagedSelectedMerge?: (request: { format: 1; mode: "managed_selected_merge_request_v1"; checkpointDigest: string }) => Promise<{ ok: true; receipt: Record<string, unknown> } | { ok: false; message: string; maintenanceRequired?: boolean }>;
      mergeSandboxIntoStable?: () => Promise<{ ok: boolean; message: string; detail?: string; steps?: string[]; merged?: boolean; synced?: boolean; recoveryStash?: string }>;
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
      readAgentTextPreview?: (displayRef: string) => Promise<{
        text: string;
        truncated: boolean;
        language: string;
        filename: string;
      } | null>;
      readConstellation: () => Promise<{ version: 1; stars: unknown[]; links: unknown[] }>;
      writeConstellation: (payload: { version: 1; stars: unknown[]; links: unknown[] }) => Promise<boolean>;
      clearConstellation: () => Promise<boolean>;
    };
  }
}
