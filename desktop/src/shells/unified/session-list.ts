/**
 * Sidebar session list (当下 rail): merges the global `session.list` with the
 * bound project's `project.threads`, groups rows by date and filters by
 * project. Pure rendering helpers so the list is testable without the DOM.
 */

export interface SessionSummary {
  session_id: string;
  title: string;
  preview?: string;
  updated_at: string;
  message_count?: number;
  project_id?: string;
  execution_lane?: "stable" | "sandbox";
}

export interface ThreadSummary {
  sessionId: string;
  title: string;
  preview: string;
  updatedAt: string;
  archived: boolean;
}

export type SessionFilter = "all" | "chat" | "project";
export type SessionDot = "running" | "live" | "archived" | "project" | "idle";
export type DateBucket = "today" | "yesterday" | "week" | "earlier";

export const DATE_BUCKET_LABELS: Record<DateBucket, string> = {
  today: "今天",
  yesterday: "昨天",
  week: "本周",
  earlier: "更早",
};

export interface SessionListInput {
  sessions: SessionSummary[];
  threads: ThreadSummary[];
  projectId: string;
  activeSessionId: string;
  currentSessionId: string;
  working: boolean;
  query: string;
  filter: SessionFilter;
  loaded: boolean;
  now?: Date;
}

export interface SessionRow {
  kind: "chat" | "project" | "thread";
  sessionId: string;
  projectId: string;
  title: string;
  meta: string;
  updatedAt: string;
  dot: SessionDot;
  current: boolean;
  sidecarLane: "stable" | "sandbox";
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function parseTime(value: string): Date | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d;
}

function startOfDay(d: Date): number {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
}

export function dateBucket(value: string, now: Date = new Date()): DateBucket {
  const d = parseTime(value);
  if (!d) return "earlier";
  const day = 86_400_000;
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / day);
  if (diffDays <= 0) return "today";
  if (diffDays === 1) return "yesterday";
  if (diffDays < 7) return "week";
  return "earlier";
}

const WEEKDAYS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

export function shortTime(value: string, now: Date = new Date()): string {
  const d = parseTime(value);
  if (!d) return "";
  const bucket = dateBucket(value, now);
  if (bucket === "today") {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  if (bucket === "yesterday") return "昨天";
  if (bucket === "week") return WEEKDAYS[d.getDay()];
  return `${String(d.getMonth() + 1).padStart(2, "0")}/${String(d.getDate()).padStart(2, "0")}`;
}

/** Merge sources into rows. The bound project shows its threads instead of one collapsed row. */
export function buildSessionRows(input: SessionListInput): SessionRow[] {
  const rows: SessionRow[] = [];
  const pid = input.projectId.trim();
  const useThreads = Boolean(pid) && input.threads.length > 0;
  for (const s of input.sessions) {
    const sid = s.session_id;
    const isCurrent = sid === input.currentSessionId;
    if (s.project_id) {
      if (useThreads && s.project_id === pid) continue;
      rows.push({
        kind: "project",
        sessionId: sid,
        projectId: s.project_id,
        title: s.title || s.project_id,
        meta: isCurrent ? "项目 · 正在推进" : "项目 · 随时续上",
        updatedAt: s.updated_at,
        dot: isCurrent && input.working ? "running" : "project",
        current: isCurrent,
        sidecarLane: s.execution_lane === "sandbox" ? "sandbox" : "stable",
      });
      continue;
    }
    // Empty ordinary sessions (created on launch/reconnect) are noise; keep only the current one.
    if (s.message_count === 0 && !isCurrent) continue;
    const count = s.message_count ? `${s.message_count} 句` : "";
    const untitled = !s.title || s.title === sid;
    const lane = s.execution_lane === "sandbox" ? "sandbox" : "stable";
    rows.push({
      kind: "chat",
      sessionId: sid,
      projectId: "",
      title: untitled ? (s.message_count ? "随手闲聊" : "新会话") : s.title,
      meta: ["随聊", lane === "sandbox" ? "实验运行时" : "", count].filter(Boolean).join(" · "),
      updatedAt: s.updated_at,
      dot: isCurrent && input.working ? "running" : "idle",
      current: isCurrent,
      sidecarLane: lane,
    });
  }
  if (useThreads) {
    for (const t of input.threads) {
      const isCurrent = t.sessionId === input.currentSessionId;
      const live = t.sessionId === input.activeSessionId;
      rows.push({
        kind: "thread",
        sessionId: t.sessionId,
        projectId: pid,
        title: t.title || t.sessionId,
        meta: `${pid} · ${live ? "活线" : "归档"}${t.preview ? ` · ${t.preview}` : ""}`,
        updatedAt: t.updatedAt,
        dot: isCurrent && input.working ? "running" : live ? "live" : "archived",
        current: isCurrent,
        sidecarLane: "stable",
      });
    }
  }
  return rows;
}

export function filterSessionRows(rows: SessionRow[], filter: SessionFilter, query: string): SessionRow[] {
  const q = query.trim().toLocaleLowerCase();
  return rows.filter((row) => {
    if (filter === "chat" && row.kind !== "chat") return false;
    if (filter === "project" && row.kind !== "thread") return false;
    if (!q) return true;
    return `${row.title} ${row.meta} ${row.projectId}`.toLocaleLowerCase().includes(q);
  });
}

export function sessionFilterCounts(rows: SessionRow[]): Record<SessionFilter, number> {
  return {
    all: rows.length,
    chat: rows.filter((r) => r.kind === "chat").length,
    project: rows.filter((r) => r.kind === "thread").length,
  };
}

function rowTime(row: SessionRow): number {
  return parseTime(row.updatedAt)?.getTime() ?? 0;
}

function renderRow(row: SessionRow, now: Date): string {
  const attrs = row.kind === "thread"
    ? `data-thread-id="${escapeHtml(row.sessionId)}"`
    : `data-open-session="${escapeHtml(row.sessionId)}" data-project-id="${escapeHtml(row.projectId)}" data-sidecar-lane="${escapeHtml(row.sidecarLane)}"`;
  const tag = row.current ? '<span class="ma-session-tag">当前</span>' : "";
  return `<button type="button" class="ma-session${row.current ? " is-current" : ""}${row.kind === "thread" ? " is-thread" : ""}" ${attrs}${row.current ? ' aria-current="true"' : ""}>
    <span class="ma-session-dot is-${row.dot}" aria-hidden="true"></span>
    <span class="ma-session-main"><span class="ma-session-title">${escapeHtml(row.title)}${tag}</span><span class="ma-session-meta">${escapeHtml(row.meta)}</span></span>
    <span class="ma-session-time">${escapeHtml(shortTime(row.updatedAt, now))}</span>
  </button>`;
}

export function renderSessionFiltersHtml(input: SessionListInput): string {
  const counts = sessionFilterCounts(buildSessionRows(input));
  const chip = (id: SessionFilter, label: string, n: number) =>
    `<button type="button" class="ma-session-chip${input.filter === id ? " is-on" : ""}" data-session-filter="${id}" aria-pressed="${input.filter === id}"><span>${escapeHtml(label)}</span><span class="n">${n}</span></button>`;
  const parts = [chip("all", "全部", counts.all)];
  if (input.projectId && counts.project > 0) parts.push(chip("project", input.projectId, counts.project));
  parts.push(chip("chat", "随聊", counts.chat));
  return parts.join("");
}

export function renderSessionListHtml(input: SessionListInput): string {
  const now = input.now ?? new Date();
  if (!input.loaded) return '<p class="ma-session-empty">加载中…</p>';
  const rows = filterSessionRows(buildSessionRows(input), input.filter, input.query)
    .sort((a, b) => rowTime(b) - rowTime(a));
  if (!rows.length) {
    return `<p class="ma-session-empty">${input.query.trim() ? "没有匹配的会话" : "还没有会话 · 点「新会话」开始"}</p>`;
  }
  const order: DateBucket[] = ["today", "yesterday", "week", "earlier"];
  const groups = new Map<DateBucket, SessionRow[]>();
  for (const row of rows) {
    const bucket = dateBucket(row.updatedAt, now);
    groups.set(bucket, [...(groups.get(bucket) ?? []), row]);
  }
  return order
    .filter((bucket) => groups.has(bucket))
    .map((bucket) => {
      const items = groups.get(bucket)!;
      return `<div class="ma-session-group"><div class="ma-session-group-label">${DATE_BUCKET_LABELS[bucket]}<span class="cnt">${items.length}</span></div>${items.map((r) => renderRow(r, now)).join("")}</div>`;
    })
    .join("");
}
