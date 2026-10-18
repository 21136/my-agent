export type CandidateSwitchResult = {
  ok: boolean;
  state: string;
  recovered: boolean;
  candidateRoot?: string;
  error?: string;
};

export function normalizeCandidateSwitchResult(value: unknown): CandidateSwitchResult {
  if (!value || typeof value !== "object") {
    return { ok: false, state: "blocked", recovered: false, error: "候选切换返回无效结果" };
  }
  const payload = value as Record<string, unknown>;
  return {
    ok: payload.ok === true,
    state: typeof payload.state === "string" && payload.state.trim() ? payload.state : "blocked",
    recovered: payload.recovered === true,
    candidateRoot: typeof payload.candidateRoot === "string" ? payload.candidateRoot : undefined,
    error: typeof payload.error === "string" && payload.error.trim() ? payload.error : undefined,
  };
}

export function formatCandidateSwitchResult(result: CandidateSwitchResult): string {
  if (result.ok) return "候选 Host Agent 已通过健康检查并切换";
  if (result.state === "rolled_back" && result.recovered) {
    return `候选版本未通过健康检查，已回滚到旧版本${result.error ? `：${result.error}` : ""}`;
  }
  if (result.state === "blocked") {
    return `候选切换已阻止${result.error ? `：${result.error}` : ""}`;
  }
  return `候选切换失败（${result.state}）${result.error ? `：${result.error}` : ""}`;
}
