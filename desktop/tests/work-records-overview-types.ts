import type { WorkRecordsOverviewGroup } from "../src/api/ws";

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
