export type IncidentRepairStatus = "unavailable" | "available" | "requesting" | "send_failed" | "running" | "candidate" | "published" | "blocked" | "failed";

export type IncidentDetectedEvent = {
  type: "incident.detected";
  incident_id: string;
  kind: string;
  effect: string;
  repair_available: boolean;
};

export type IncidentRepairStatusEvent = {
  type: "incident.repair.status";
  incident_id: string;
  request_id: string;
  status: Exclude<IncidentRepairStatus, "unavailable" | "available" | "requesting" | "send_failed">;
};

export type IncidentRepairView = {
  incidentId: string;
  status: IncidentRepairStatus;
};

const INCIDENT_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const EFFECT = "tool_result_not_saved";
const VALID_STATUSES = new Set(["running", "candidate", "published", "blocked", "failed"]);

/** Ephemeral renderer-only projection. Never copies backend reason or model output. */
export class IncidentRepairState {
  private current: IncidentRepairView | null = null;
  private requestId: string | null = null;

  acceptDetected(event: IncidentDetectedEvent): boolean {
    if (!event || event.type !== "incident.detected" || typeof event.kind !== "string" || !event.kind || event.effect !== EFFECT
        || typeof event.repair_available !== "boolean" || typeof event.incident_id !== "string"
        || !INCIDENT_ID.test(event.incident_id)) return false;
    if (this.current?.incidentId === event.incident_id) return true;
    this.current = { incidentId: event.incident_id, status: event.repair_available ? "available" : "unavailable" };
    this.requestId = null;
    return true;
  }

  begin(requestId: string): { type: "incident.repair"; incident_id: string; request_id: string } | null {
    if (!this.current || (this.current.status !== "available" && this.current.status !== "send_failed") || !REQUEST_ID.test(requestId)) return null;
    this.requestId = requestId;
    this.current = { ...this.current, status: "requesting" };
    return { type: "incident.repair", incident_id: this.current.incidentId, request_id: requestId };
  }

  sendFailed(requestId: string): boolean {
    if (!this.current || this.requestId !== requestId || this.current.status !== "requesting") return false;
    this.requestId = null;
    this.current = { ...this.current, status: "send_failed" };
    return true;
  }

  acceptStatus(event: IncidentRepairStatusEvent): boolean {
    if (!this.current || event?.type !== "incident.repair.status" || event.incident_id !== this.current.incidentId
        || !this.requestId || event.request_id !== this.requestId || !VALID_STATUSES.has(event.status)) return false;
    const terminal = this.current.status === "published" || this.current.status === "blocked" || this.current.status === "failed";
    if (terminal) return false;
    const allowedAfterCandidate = new Set(["published", "blocked", "failed"]);
    if (this.current.status === "candidate" && !allowedAfterCandidate.has(event.status)) return false;
    this.current = { ...this.current, status: event.status };
    return true;
  }

  reset(): void {
    this.current = null;
    this.requestId = null;
  }

  view(): IncidentRepairView | null {
    return this.current ? { ...this.current } : null;
  }
}
