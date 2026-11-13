import type { MergedWorkRecordsOverviewRunHistory, ServerEvent, WorkRecordsOverviewGroup, WorkRecordsOverviewRunHistory } from "../src/api/ws";

declare const group: WorkRecordsOverviewGroup;
if (group.kind === "linked_source_snapshot") {
  const status: "unknown" | "none" = group.attention_status;
  const reason: "publication_pending" | "superseded_snapshot_unviewed" | "view_not_confirmed" | "worker_result_unverified" | "captured_requirement_unresolved" | "superseded_snapshot_attention" | "view_evidence_unavailable" | "elapsed_terminal_capture" = group.attention_reason;
  const timer: "unknown" | "waiting" | "not_viewed" | "unavailable" | "elapsed" = group.visibility_timer_status;
  const project: string = group.source_snapshot.project_id;
  void [status, reason, timer, project];
} else {
  // @ts-expect-error Legacy groups do not claim source snapshot attention facts.
  group.attention_reason;
}

// Merged history rows are enriched by the authorized selected-projects query;
// per-requirement history intentionally keeps only the base run facts.
declare const mergedHistoryEvent: Extract<ServerEvent, { type: "work-records.overview.result" }>;
if (mergedHistoryEvent.history_scope === "selected_projects_history") {
  const mergedRow: MergedWorkRecordsOverviewRunHistory | undefined = mergedHistoryEvent.history?.[0];
  if (mergedRow) {
    const projectId: string = mergedRow.project_id;
    const sessionId: string = mergedRow.session_id;
    const itemId: string = mergedRow.item_id;
    const requirementId: string = mergedRow.requirement_id;
    void [projectId, sessionId, itemId, requirementId];
  }
}
const requirementHistoryRow: WorkRecordsOverviewRunHistory = {
  run_id: "run", result_id: "result", status: "completed", summary: "summary",
  verification_status: "verified", recorded_at: null,
};
void requirementHistoryRow;
