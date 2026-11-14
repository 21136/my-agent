import type { WorkRecordsOverviewResultEvent, WorkRecordsOverviewRunHistory } from "../../api/ws";

type Page = NonNullable<WorkRecordsOverviewResultEvent["history_page"]>;
type Source = NonNullable<WorkRecordsOverviewResultEvent["history_sources"]>[number];
export type MergedHistoryRow = WorkRecordsOverviewRunHistory & { project_id: string; session_id: string; item_id: string; item_title?: string; requirement_text?: string };
export type MergedHistoryState = { rows: MergedHistoryRow[]; page: Page | null; sources: Source[] };

export function clearMergedHistory(state: MergedHistoryState): MergedHistoryState {
  return { rows: [], page: null, sources: [] };
}

export function appendMergedHistoryPage(
  state: MergedHistoryState, page: Page, sources: Source[], rows: MergedHistoryRow[], requestedCursor?: string,
): MergedHistoryState | null {
  if (page.scope !== "selected_projects_history" || page.page_size !== 10
    || !Number.isSafeInteger(page.offset) || page.offset < 0
    || typeof page.has_more !== "boolean"
    || (page.has_more ? typeof page.next_cursor !== "string" || !page.next_cursor : page.next_cursor !== null)
    || rows.length > page.page_size || (page.has_more && rows.length !== page.page_size)
    || (rows.length === 0 && page.has_more)) return null;
  const sortedSources = [...sources].sort((a, b) => a.project_id.localeCompare(b.project_id));
  if (new Set(sortedSources.map(source => source.project_id)).size !== sortedSources.length) return null;
  const sourceKey = (source: Source) => `${source.project_id}\0${source.session_id}\0${source.revision}`;
  const initial = page.offset === 0;
  if (!initial && (!state.page?.has_more || state.page.next_cursor === null || requestedCursor !== state.page.next_cursor || page.offset !== state.rows.length
    || state.sources.length !== sortedSources.length
    || state.sources.map(sourceKey).sort().join("\n") !== sortedSources.map(sourceKey).join("\n"))) return null;
  const existing = initial ? [] : state.rows;
  const identities = new Set(existing.map(row => `${row.project_id}\0${row.session_id}\0${row.result_id}`));
  for (const row of rows) {
    const identity = `${row.project_id}\0${row.session_id}\0${row.result_id}`;
    if (identities.has(identity)) return null;
    identities.add(identity);
  }
  return { rows: [...existing, ...rows], page, sources: sortedSources };
}


export function mergedHistoryFailureMessage(state: string, reason?: unknown): string | null {
  if (state === "cancelled") return "统一历史查询已取消；旧历史与游标已清除。";
  if (state === "expired") return "统一历史游标或来源已过期；旧历史与游标已清除，请重新请求首屏。";
  if (state === "error") return reason === "multi_project_source_unavailable"
    ? "未授权或来源暂不可用；旧历史与游标已清除。"
    : "统一历史读取失败；旧历史与游标已清除，请稍后重试。";
  return null;
}


export function resetMergedHistoryForFailure(
  state: MergedHistoryState, status: string, reason?: unknown,
): { history: MergedHistoryState; message: string } | null {
  const message = mergedHistoryFailureMessage(status, reason);
  return message === null ? null : { history: clearMergedHistory(state), message };
}
