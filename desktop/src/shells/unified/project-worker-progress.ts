export const PROJECT_WORKER_PROGRESS_TTL_MS = 12_000;

export interface ProjectWorkerProgressContext {
  sessionId: string;
  runId: string;
  executionState: string;
  turnActive: boolean;
}

export interface ProjectWorkerActivityEvent {
  type: "activity.update";
  run_id: string;
  session_id: string;
  activity_source: "project_workers";
  batch_seq: number;
  phase: "started" | "returned";
  project_id: string;
  started: number;
  returned: number;
  total: number;
  text: string;
}

type ProgressPhase = "started" | "returned";
const SAFE_PROJECT_ID = /^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$/;
const ALLOWED_FIELDS = new Set([
  "type", "run_id", "session_id", "activity_source", "batch_seq", "phase", "project_id",
  "started", "returned", "total", "text",
]);

function activeContext(context: ProjectWorkerProgressContext): boolean {
  return Boolean(context.sessionId && context.runId && context.turnActive) && context.executionState === "running";
}

function validatedEvent(value: unknown, context: ProjectWorkerProgressContext): {
  phase: ProgressPhase; projectId: string; batchSeq: number; started: number; returned: number; total: number;
} | null {
  if (!value || typeof value !== "object" || Array.isArray(value) || !activeContext(context)) return null;
  const event = value as Record<string, unknown>;
  if (Object.keys(event).some(key => !ALLOWED_FIELDS.has(key))
      || event.type !== "activity.update" || event.run_id !== context.runId
      || event.session_id !== context.sessionId || event.activity_source !== "project_workers"
      || (event.phase !== "started" && event.phase !== "returned")
      || typeof event.project_id !== "string"
      || !(SAFE_PROJECT_ID.test(event.project_id) || event.project_id === "项目")
      || !Number.isSafeInteger(event.batch_seq) || (event.batch_seq as number) < 1
      || !Number.isSafeInteger(event.started) || !Number.isSafeInteger(event.returned)
      || !Number.isSafeInteger(event.total) || typeof event.text !== "string") return null;
  const started = event.started as number, returned = event.returned as number, total = event.total as number;
  if (total < 1 || total > 10_000 || started < 1 || started > total || returned < 0 || returned > started) return null;
  if (event.phase === "returned" && returned < 1) return null;
  return { phase: event.phase, projectId: event.project_id, batchSeq: event.batch_seq as number, started, returned, total };
}

/** A run/session-bound, bounded status snapshot; never persists or appends a chat block. */
export class ProjectWorkerProgressStatus {
  private sessionId = "";
  private runId = "";
  private batchSeq = 0;
  private started = 0;
  private returned = 0;
  private total = 0;
  private text = "";
  private expiresAt = 0;
  private readonly ttlMs: number;

  constructor(ttlMs = PROJECT_WORKER_PROGRESS_TTL_MS) { this.ttlMs = ttlMs; }

  accept(value: unknown, context: ProjectWorkerProgressContext, nowMs = Date.now()): string | null {
    const event = validatedEvent(value, context);
    if (!event) return null;
    const sameRun = this.sessionId === context.sessionId && this.runId === context.runId;
    if (sameRun && event.batchSeq < this.batchSeq) return null;
    const sameBatch = sameRun && event.batchSeq === this.batchSeq;
    const previousStarted = sameBatch ? this.started : 0;
    const previousReturned = sameBatch ? this.returned : 0;
    if ((sameBatch && event.total !== this.total) || event.started < previousStarted || event.returned < previousReturned) return null;
    if (event.phase === "started" && event.started <= previousStarted) return null;
    if (event.phase === "returned" && event.returned <= previousReturned) return null;

    this.sessionId = context.sessionId;
    this.runId = context.runId;
    this.batchSeq = event.batchSeq;
    this.started = event.started;
    this.returned = event.returned;
    this.total = event.total;
    const label = event.phase === "started" ? "已启动" : "已返回";
    const projectLabel = event.projectId === "项目" ? "某项目" : "项目 " + event.projectId;
    this.text = projectLabel + " " + label + " · 已启动 " + event.started + "/" + event.total
      + "，已返回 " + event.returned + "/" + event.total;
    this.expiresAt = nowMs + this.ttlMs;
    return this.text;
  }

  current(context: ProjectWorkerProgressContext, nowMs = Date.now()): string | null {
    if (!activeContext(context) || context.sessionId !== this.sessionId || context.runId !== this.runId || nowMs >= this.expiresAt) {
      this.clear();
      return null;
    }
    return this.text || null;
  }

  clear(): void {
    // Retain the run/batch watermark after visual clearing or TTL expiry so
    // an older delayed snapshot cannot resurrect a previous batch.
    this.text = "";
    this.expiresAt = 0;
  }
}
