/** Settled tool-step status. A missing-file probe is a note, not a failure. */

export const ABSENT_READ_SUMMARY = "文件还不存在";

export type ToolStepStatus = "running" | "ok" | "fail" | "note";

export function settledToolStatus(
  ok: boolean,
  summary?: string,
  note?: string,
): Exclude<ToolStepStatus, "running"> {
  if (note === "absent" || (summary ?? "").trim() === ABSENT_READ_SUMMARY) return "note";
  return ok ? "ok" : "fail";
}
