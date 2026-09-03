/** UX-028 M0 — activity timeline entries (source of truth for process blocks). */

import { reasoningSummaryLine } from "./unified/output-display";

export type ProcessToolCard = {
  callId: string;
  tool: string;
  summary: string;
  status: "running" | "ok" | "fail";
  startedAt: number;
  endedAt?: number;
  endSummary?: string;
  progressText?: string;
  logsTail?: string;
};

export type ThinkEntry = {
  kind: "think";
  id: string;
  text: string;
  phase: "streaming" | "pinned";
  startedAt: number;
  pinnedAt?: number;
  userOpen?: boolean;
};

export type ToolEntry = {
  kind: "tool";
} & ProcessToolCard;

export type ActivityEntry = ThinkEntry | ToolEntry;

export type ProcessActivityBlock = {
  kind: "process";
  lines: string[];
  collapsed: boolean;
  turnKey: string;
  entries?: ActivityEntry[];
  /** @deprecated derived from entries — kept for incremental render helpers */
  reasoning: string;
  reasoningPhase?: "idle" | "streaming" | "pinned";
  reasoningUserOpen?: boolean;
  reasoningStartedAt?: number;
  reasoningPinnedAt?: number;
  llmPending?: boolean;
  tools?: ProcessToolCard[];
};

let activityIdSeq = 0;

function nextThinkId(): string {
  activityIdSeq += 1;
  return `think-${activityIdSeq}`;
}

export function ensureProcessEntries(proc: ProcessActivityBlock): ActivityEntry[] {
  if (proc.entries && proc.entries.length > 0) {
    return proc.entries;
  }
  const entries: ActivityEntry[] = [];
  if (proc.reasoning.trim()) {
    entries.push({
      kind: "think",
      id: nextThinkId(),
      text: proc.reasoning,
      phase: proc.reasoningPhase === "streaming" ? "streaming" : "pinned",
      startedAt: proc.reasoningStartedAt ?? Date.now(),
      pinnedAt: proc.reasoningPinnedAt,
      userOpen: proc.reasoningUserOpen,
    });
  }
  for (const tool of proc.tools ?? []) {
    entries.push({ kind: "tool", ...tool });
  }
  proc.entries = entries;
  syncProcessLegacyFields(proc);
  return entries;
}

export function getProcessTools(proc: ProcessActivityBlock): ProcessToolCard[] {
  ensureProcessEntries(proc);
  return proc.entries!.filter((e): e is ToolEntry => e.kind === "tool");
}

export function syncProcessLegacyFields(proc: ProcessActivityBlock): void {
  const entries = proc.entries ?? [];
  const thinks = entries.filter((e): e is ThinkEntry => e.kind === "think");
  const tools = entries.filter((e): e is ToolEntry => e.kind === "tool");
  proc.tools = tools.map(({ kind: _k, ...tool }) => tool);

  const openThink = thinks.find((t) => t.phase === "streaming");
  const lastThink = thinks.length ? thinks[thinks.length - 1] : undefined;

  if (openThink) {
    proc.reasoning = openThink.text;
    proc.reasoningPhase = "streaming";
    proc.reasoningStartedAt = openThink.startedAt;
    proc.reasoningPinnedAt = openThink.pinnedAt;
    proc.reasoningUserOpen = openThink.userOpen;
  } else if (lastThink?.text.trim()) {
    proc.reasoning = lastThink.text;
    proc.reasoningPhase = "pinned";
    proc.reasoningStartedAt = lastThink.startedAt;
    proc.reasoningPinnedAt = lastThink.pinnedAt ?? lastThink.startedAt;
    proc.reasoningUserOpen = lastThink.userOpen;
  } else {
    proc.reasoning = "";
    proc.reasoningPhase = proc.llmPending ? "idle" : "idle";
    proc.reasoningStartedAt = proc.llmPending ? Date.now() : undefined;
    proc.reasoningPinnedAt = undefined;
    proc.reasoningUserOpen = false;
  }
}

function findOpenThink(entries: ActivityEntry[]): ThinkEntry | undefined {
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry.kind === "think" && entry.phase === "streaming") {
      return entry;
    }
  }
  return undefined;
}

export function pinOpenThink(proc: ProcessActivityBlock): void {
  const entries = ensureProcessEntries(proc);
  const open = findOpenThink(entries);
  if (!open) return;
  if (open.text.trim() || open.phase === "streaming") {
    open.phase = "pinned";
    open.pinnedAt = Date.now();
    open.userOpen = false;
  }
  syncProcessLegacyFields(proc);
}

export function pinAllStreamingThinks(proc: ProcessActivityBlock): void {
  const entries = ensureProcessEntries(proc);
  for (const entry of entries) {
    if (entry.kind === "think" && entry.phase === "streaming") {
      entry.phase = "pinned";
      entry.pinnedAt = Date.now();
      entry.userOpen = false;
    }
  }
  syncProcessLegacyFields(proc);
}

export function prepareProcessForLlmRound(proc: ProcessActivityBlock): void {
  ensureProcessEntries(proc);
  pinOpenThink(proc);
  proc.llmPending = false;
}

export function markLlmPending(proc: ProcessActivityBlock): void {
  ensureProcessEntries(proc);
  proc.llmPending = true;
  if (!proc.reasoningStartedAt) {
    proc.reasoningStartedAt = Date.now();
  }
  syncProcessLegacyFields(proc);
}

export function clearLlmPending(proc: ProcessActivityBlock): void {
  proc.llmPending = false;
  syncProcessLegacyFields(proc);
}

export function appendReasoning(proc: ProcessActivityBlock, text: string): void {
  if (!text) return;
  const entries = ensureProcessEntries(proc);
  proc.llmPending = false;
  let open = findOpenThink(entries);
  if (!open) {
    open = {
      kind: "think",
      id: nextThinkId(),
      text: "",
      phase: "streaming",
      startedAt: Date.now(),
    };
    entries.push(open);
  } else if (open.phase === "pinned") {
    open.phase = "streaming";
    open.userOpen = true;
  }
  open.text += text;
  syncProcessLegacyFields(proc);
}

export function addToolStart(
  proc: ProcessActivityBlock,
  tool: ProcessToolCard,
): void {
  const entries = ensureProcessEntries(proc);
  pinOpenThink(proc);
  proc.llmPending = false;
  entries.push({ kind: "tool", ...tool });
  syncProcessLegacyFields(proc);
}

export function findToolEntry(
  proc: ProcessActivityBlock,
  callId: string,
): ToolEntry | undefined {
  const entries = ensureProcessEntries(proc);
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry.kind === "tool" && entry.callId === callId) {
      return entry;
    }
  }
  return undefined;
}

export function finalizeProcessAfterTurn(proc: ProcessActivityBlock): void {
  proc.llmPending = false;
  pinAllStreamingThinks(proc);
  const entries = proc.entries ?? [];
  const hasThinkText = entries.some((e) => e.kind === "think" && e.text.trim());
  if (!hasThinkText) {
    proc.reasoningPhase = "idle";
    proc.reasoningStartedAt = undefined;
    proc.reasoningPinnedAt = undefined;
    proc.reasoningUserOpen = false;
    proc.reasoning = "";
  }
  syncProcessLegacyFields(proc);
}

export function isProcessThinkingLive(
  proc: ProcessActivityBlock,
  currentTurnKey: string,
): boolean {
  if (proc.turnKey !== currentTurnKey) return false;
  if (proc.llmPending) return true;
  const entries = proc.entries ?? [];
  return entries.some((e) => e.kind === "think" && e.phase === "streaming");
}

export function toggleThinkOpen(proc: ProcessActivityBlock, thinkId?: string): void {
  const entries = ensureProcessEntries(proc);
  const targets = thinkId
    ? entries.filter((e): e is ThinkEntry => e.kind === "think" && e.id === thinkId)
    : entries.filter(
        (e): e is ThinkEntry =>
          e.kind === "think" && e.phase === "pinned" && Boolean(e.text.trim()),
      );
  const think = targets.length ? targets[targets.length - 1] : undefined;
  if (!think || think.phase === "streaming") return;
  think.userOpen = !think.userOpen;
  syncProcessLegacyFields(proc);
}

export function openLastPinnedThink(proc: ProcessActivityBlock): void {
  const entries = ensureProcessEntries(proc);
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry.kind === "think" && entry.phase === "pinned" && entry.text.trim()) {
      entry.userOpen = true;
      syncProcessLegacyFields(proc);
      return;
    }
  }
}

export function formatThinkElapsedSec(entry: ThinkEntry): number {
  const end = entry.phase === "streaming" ? Date.now() : (entry.pinnedAt ?? Date.now());
  return Math.max(1, Math.round((end - entry.startedAt) / 1000));
}

export function thinkEntryTitle(
  entry: ThinkEntry,
  opts?: { waiting?: boolean },
): string {
  if (!entry.text.trim() && opts?.waiting) {
    const sec = formatThinkElapsedSec({ ...entry, phase: "streaming" });
    return sec > 0 ? `思考中…（${sec}s）` : "思考中…";
  }
  if (entry.phase === "streaming") {
    const sec = formatThinkElapsedSec(entry);
    return sec > 0 ? `思考中…（${sec}s）` : "思考中…";
  }
  const summary = reasoningSummaryLine(entry.text);
  if (summary) return `思考 · ${summary}`;
  const sec = formatThinkElapsedSec(entry);
  return `思考 · ${sec}s`;
}

export function isThinkBodyOpen(
  entry: ThinkEntry,
  opts?: { waiting?: boolean },
): boolean {
  if (opts?.waiting) return true;
  if (!entry.text.trim()) return false;
  if (entry.phase === "streaming") return true;
  return Boolean(entry.userOpen);
}

export function activityEntriesPrint(entries: ActivityEntry[], llmPending: boolean): string {
  const parts = entries.map((e) => {
    if (e.kind === "think") {
      return `T:${e.id}:${e.phase}:${e.text.length}:${e.userOpen ? 1 : 0}`;
    }
    return `U:${e.callId}:${e.status}:${e.endSummary ?? ""}:${e.progressText ?? ""}:${(e.logsTail ?? "").length}`;
  });
  return `${parts.join(",")}:${llmPending ? 1 : 0}`;
}
