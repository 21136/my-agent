/** Sidebar terminal list grouping, titles, and DOM patch helpers. */

export const TERMINAL_ENDED_PREVIEW = 5;

export const TERMINAL_SCROLL_SELECTORS = [
  ".sidebar-terminals-list",
  ".sidebar-terminals-ended-list",
  ".sidebar-terminal-output",
];

export const SERVICE_SCROLL_SELECTORS = [
  ".sidebar-services-list",
  ".sidebar-services-log pre",
];

const ATTENTION_STATES = new Set(["lost", "orphaned", "failed", "unsupported"]);
const KEEP_FLAGS = new Set(["-i", "--reload", "--watch", "--hot"]);
const SKIP_FLAGS_WITH_ARG = new Set(["-c", "-C", "-e", "--command"]);

export type TerminalBucket = "active" | "attention" | "ended";

export type TerminalListSession = {
  session_id: string;
  command: string;
  cwd: string;
  state: string;
  alive?: boolean;
  exit_code?: number | null;
  signal?: string | null;
  reason?: string | null;
  last_activity_at?: string;
};

export type PanelHost = {
  dataset: { [key: string]: string | undefined };
  innerHTML: string;
  querySelector(selectors: string): { scrollTop: number } | null;
};

export type TerminalsFingerprintInput = {
  terminalSessions: TerminalListSession[];
  terminalsLoading: boolean;
  terminalsError: string;
  terminalsCollapsed: boolean;
  terminalsEndedCollapsed: boolean;
  terminalsEndedShowAll: boolean;
  terminalDetails: TerminalListSession | null;
  terminalOutput: string;
  terminalOutputLoading: boolean;
  terminalOutputError: string;
  terminalOutputCursorReset: boolean;
  terminalOutputTruncated: boolean;
};

export type ServicesFingerprintInput = {
  services: Array<{
    name: string;
    alive?: boolean;
    status?: string | null;
    ready_port?: number | null;
  }>;
  servicesLoading: boolean;
  servicesError: string;
  servicesCollapsed: boolean;
  servicesLogName: string;
  servicesLogText: string;
};

export function terminalIsActive(session: { state: string }): boolean {
  return session.state === "starting" || session.state === "running";
}

export function terminalNeedsAttention(session: { state: string }): boolean {
  return ATTENTION_STATES.has(session.state);
}

export function terminalBucket(session: { state: string }): TerminalBucket {
  if (terminalIsActive(session)) return "active";
  if (terminalNeedsAttention(session)) return "attention";
  return "ended";
}

function tokenizeCommand(command: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let quote: string | null = null;
  for (const ch of command) {
    if (quote) {
      if (ch === quote) quote = null;
      else current += ch;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (/\s/.test(ch)) {
      if (current) {
        tokens.push(current);
        current = "";
      }
      continue;
    }
    current += ch;
  }
  if (current) tokens.push(current);
  return tokens;
}

function commandBasename(token: string): string {
  const cleaned = token.replace(/\\/g, "/");
  const base = cleaned.split("/").pop() || cleaned;
  return base.replace(/\.exe$/i, "");
}

function isEnvAssignment(token: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*=/.test(token);
}

function isCodeBlob(token: string): boolean {
  return token.length > 28 || /[;{}\n]/.test(token) || /\b(import|from|print|sleep)\b/.test(token);
}

export function terminalCwdLabel(cwd: string): string {
  const normalized = (cwd || "").replace(/\\/g, "/").replace(/\/+$/, "");
  if (!normalized || normalized === ".") return "";
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return parts.join("/");
  return parts.slice(-2).join("/");
}

export function terminalHumanTitle(command: string, cwd = ""): string {
  const raw = (command || "").trim();
  const fallback = terminalCwdLabel(cwd) || "未命名会话";
  if (!raw) return fallback;
  const tokens = tokenizeCommand(raw).filter((token) => !isEnvAssignment(token));
  if (tokens.length === 0) return fallback;
  const exe = commandBasename(tokens[0] || "");
  const extras: string[] = [];
  for (let i = 1; i < tokens.length && extras.length < 3; i += 1) {
    const token = tokens[i];
    if (token === "-m" && tokens[i + 1] && !tokens[i + 1].startsWith("-")) {
      extras.push("-m", tokens[i + 1]);
      i += 1;
      continue;
    }
    if (SKIP_FLAGS_WITH_ARG.has(token)) {
      i += 1;
      continue;
    }
    if (token.startsWith("-")) {
      if (KEEP_FLAGS.has(token)) extras.push(token);
      continue;
    }
    if (isCodeBlob(token)) continue;
    extras.push(commandBasename(token));
  }
  const title = [exe, ...extras].filter(Boolean).join(" ").trim();
  return title || fallback;
}

function activityMs(value: string | undefined): number {
  const parsed = Date.parse(value || "");
  return Number.isFinite(parsed) ? parsed : 0;
}

function byRecentThenId(a: TerminalListSession, b: TerminalListSession): number {
  return activityMs(b.last_activity_at) - activityMs(a.last_activity_at)
    || a.session_id.localeCompare(b.session_id);
}

export function groupTerminalSessions(
  sessions: TerminalListSession[],
  options: {
    selectedId?: string | null;
    endedCollapsed: boolean;
    endedShowAll: boolean;
    endedPreview?: number;
  },
): {
  active: TerminalListSession[];
  attention: TerminalListSession[];
  ended: TerminalListSession[];
  endedVisible: TerminalListSession[];
  endedTotal: number;
  endedHiddenCount: number;
} {
  const active = sessions.filter((session) => terminalBucket(session) === "active").sort(byRecentThenId);
  const attention = sessions.filter((session) => terminalBucket(session) === "attention").sort(byRecentThenId);
  const ended = sessions.filter((session) => terminalBucket(session) === "ended").sort(byRecentThenId);
  const preview = options.endedPreview ?? TERMINAL_ENDED_PREVIEW;
  const selectedId = options.selectedId || "";
  let endedVisible: TerminalListSession[] = [];
  if (!options.endedCollapsed) {
    endedVisible = options.endedShowAll ? ended : ended.slice(0, preview);
  } else if (selectedId) {
    const selected = ended.find((session) => session.session_id === selectedId);
    if (selected) endedVisible = [selected];
  }
  return {
    active,
    attention,
    ended,
    endedVisible,
    endedTotal: ended.length,
    endedHiddenCount: Math.max(0, ended.length - endedVisible.length),
  };
}

export function terminalsSummaryParts(active: number, attention: number, ended: number): string[] {
  const parts: string[] = [];
  if (active > 0) parts.push(`${active} 运行中`);
  if (attention > 0) parts.push(`${attention} 需处理`);
  if (parts.length === 0 && ended > 0) parts.push("无活动会话");
  return parts;
}

function sessionFingerprint(session: TerminalListSession | null): unknown {
  if (!session) return null;
  return [
    session.session_id,
    session.state,
    session.command,
    session.cwd,
    session.alive ?? null,
    session.exit_code ?? null,
    session.signal ?? null,
    session.reason ?? null,
  ];
}

export function terminalsPanelFingerprint(state: TerminalsFingerprintInput): string {
  return JSON.stringify({
    sessions: state.terminalSessions.map((session) => sessionFingerprint(session)),
    loading: state.terminalsLoading,
    error: state.terminalsError,
    collapsed: state.terminalsCollapsed,
    endedCollapsed: state.terminalsEndedCollapsed,
    endedShowAll: state.terminalsEndedShowAll,
    details: sessionFingerprint(state.terminalDetails),
    output: state.terminalOutput,
    outputLoading: state.terminalOutputLoading,
    outputError: state.terminalOutputError,
    cursorReset: state.terminalOutputCursorReset,
    truncated: state.terminalOutputTruncated,
  });
}

export function servicesPanelFingerprint(state: ServicesFingerprintInput): string {
  return JSON.stringify({
    services: state.services.map((service) => [
      service.name,
      service.alive ?? false,
      service.status ?? "",
      service.ready_port ?? null,
    ]),
    loading: state.servicesLoading,
    error: state.servicesError,
    collapsed: state.servicesCollapsed,
    logName: state.servicesLogName,
    logText: state.servicesLogText,
  });
}

export function patchPanelHtml(
  panel: PanelHost,
  html: string,
  key: string,
  scrollSelectors: string[],
): void {
  if (panel.dataset.renderKey === key && panel.innerHTML !== "") return;
  const scrolls = scrollSelectors.map((selector) => {
    const el = panel.querySelector(selector);
    return { selector, top: el?.scrollTop ?? 0 };
  });
  panel.innerHTML = html;
  panel.dataset.renderKey = key;
  for (const item of scrolls) {
    const el = panel.querySelector(item.selector);
    if (el) el.scrollTop = item.top;
  }
}

export function clearPanelHtml(panel: PanelHost): void {
  panel.innerHTML = "";
  delete panel.dataset.renderKey;
}
