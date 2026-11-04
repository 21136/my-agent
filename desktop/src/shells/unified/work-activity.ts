import type { ProcessActivityBlock, ToolEntry } from "../activity-state";

export type WorkActivityItem = {
  callId: string;
  action: string;
  path: string;
  status: ToolEntry["status"];
  startedAt: number;
};

/** Read-only projection of tool events already accepted into the current chat model. */
export function projectRecentWorkActivity(blocks: unknown[], limit = 5): WorkActivityItem[] {
  const tools: ToolEntry[] = [];
  for (const candidate of blocks) {
    if (!candidate || typeof candidate !== "object" || (candidate as { kind?: unknown }).kind !== "process") continue;
    const process = candidate as ProcessActivityBlock;
    for (const entry of process.entries ?? []) {
      if (entry.kind === "tool") tools.push(entry);
    }
  }
  return tools
    .filter((entry) => Number.isFinite(entry.startedAt) && typeof entry.callId === "string")
    .sort((a, b) => b.startedAt - a.startedAt)
    .slice(0, Math.max(0, Math.floor(limit)))
    .map((entry) => ({
      callId: entry.callId,
      action: (entry.summary || entry.tool).replace(/\s+/g, " ").trim().slice(0, 72),
      path: typeof entry.path === "string" ? entry.path.trim().replace(/\\/g, "/") : "",
      status: entry.status,
      startedAt: entry.startedAt,
    }));
}
