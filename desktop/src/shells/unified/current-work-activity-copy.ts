const LIFECYCLE_KIND = "project_worker_host_lifecycle";

const RUN_STATUS_LABELS: Record<string, string> = {
  blocked: "受阻",
  unknown: "结果待确认",
  in_progress: "执行状态待确认",
  stopped: "已停止",
  pending: "待开始",
  completed: "已记录完成",
  failed: "失败",
  cancelled: "已取消",
};

const VERIFICATION_STATUS_LABELS: Record<string, string> = {
  not_verified: "尚未验证",
  passed: "验证已通过",
  failed: "验证未通过",
  unknown: "验证状态未知",
};

const HOST_STATUS_LABELS: Record<string, string> = {
  model_turn_finished: "主机回合已结束",
  blocked: "主机报告受阻",
  failed: "主机报告执行失败",
  cancelled: "主机回合已取消",
  limit_reached: "主机报告达到运行限制",
  unknown: "主机状态待确认",
};

const RUN_STATUS_SUMMARY_FALLBACK: Record<string, string> = {
  blocked: "工作结果记录为受阻；验收未运行。",
  unknown: "工作结果待确认；尚未验证。",
  in_progress: "执行状态待确认。",
  stopped: "工作已停止；结果尚未验证。",
  completed: "工作结果已记录；尚未验证。",
};

const READ_ATTEMPT_RESULT_LABELS: Record<string, string> = {
  success: "读取成功",
  retrying: "读取重试中",
  failed: "读取失败",
  cancelled: "读取已取消",
};

const INTERNAL_LEAK_PATTERN = /project_worker_host_lifecycle|host_status|model_turn_finished|worker_id|task_id|read_id|parent_run_id|reservation_id|acceptance_status|write_receipts/i;
const UUID_PATTERN = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const TASK_ID_PATTERN = /task-[0-9a-f]{64}/i;

export function formatOverviewRunStatus(status: string | undefined): string {
  if (!status) return "状态待确认";
  return RUN_STATUS_LABELS[status] ?? "状态待确认";
}

export function formatOverviewVerificationStatus(status: string | undefined): string {
  if (!status) return "验证状态未知";
  return VERIFICATION_STATUS_LABELS[status] ?? "验证状态未知";
}

function looksLikeInternalPayload(text: string): boolean {
  return INTERNAL_LEAK_PATTERN.test(text) || UUID_PATTERN.test(text) || TASK_ID_PATTERN.test(text);
}

function summarizeLifecyclePayload(payload: Record<string, unknown>, runStatus: string): string {
  const hostStatus = typeof payload.host_status === "string" ? payload.host_status : "";
  const hostLabel = HOST_STATUS_LABELS[hostStatus];
  if (hostStatus === "blocked") {
    return "工作器尚未启动；执行前检查受阻。验收未运行。";
  }
  if (hostLabel) {
    const readAttempts = Array.isArray(payload.read_attempts) ? payload.read_attempts : [];
    const readNote = readAttempts.length
      ? readAttempts.slice(0, 4).map(attempt => {
        if (!attempt || typeof attempt !== "object") return null;
        const record = attempt as Record<string, unknown>;
        const path = typeof record.path === "string" && record.path ? record.path : "文件";
        const result = typeof record.result === "string"
          ? (READ_ATTEMPT_RESULT_LABELS[record.result] ?? "读取状态未记录")
          : "读取状态未记录";
        return `${path}：${result}`;
      }).filter((line): line is string => Boolean(line)).join("；")
      : "";
    const receiptCount = Array.isArray(payload.write_receipts) ? payload.write_receipts.length : 0;
    const detail = readNote || (receiptCount > 0 ? `已记录 ${receiptCount} 项写入结果` : "");
    return detail ? `${hostLabel}；${detail}。验收未运行。` : `${hostLabel}；验收未运行。`;
  }
  return RUN_STATUS_SUMMARY_FALLBACK[runStatus] ?? "工作结果待确认；尚未验证。";
}

function truncatePlainSummary(text: string, max = 240): string {
  const trimmed = text.replace(/\s+/g, " ").trim();
  if (trimmed.length <= max) return trimmed;
  return `${trimmed.slice(0, max - 1)}…`;
}

/** Plain language for overview history rows; never forward host lifecycle JSON or internal ids. */
export function formatOverviewHistorySummary(summary: string | undefined, runStatus: string | undefined): string {
  const raw = typeof summary === "string" ? summary.trim() : "";
  const status = runStatus ?? "unknown";
  if (!raw) return RUN_STATUS_SUMMARY_FALLBACK[status] ?? "（无结果摘要）";
  if (looksLikeInternalPayload(raw)) {
    try {
      const payload = JSON.parse(raw) as unknown;
      if (payload && typeof payload === "object" && !Array.isArray(payload)
          && (payload as Record<string, unknown>).kind === LIFECYCLE_KIND) {
        return summarizeLifecyclePayload(payload as Record<string, unknown>, status);
      }
    } catch {
      // Fall through to safe fallback when JSON is malformed but still looks internal.
    }
    return RUN_STATUS_SUMMARY_FALLBACK[status] ?? "工作结果待确认；尚未验证。";
  }
  return truncatePlainSummary(raw);
}
