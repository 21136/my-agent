/**
 * Plain-language polish for system-injected chat copy (notices, acks, auto labels).
 * Structured WS events stay unchanged; only user-visible strings pass through here.
 */

import { TOOL_DISPLAY_NAMES, builtinToolDisplayName } from "../../copy/tool-display";

/** Success acks the chat should not surface as notice bubbles or inline badges. */
const SILENT_SYSTEM_ACKS = new Set(["会话已保存"]);

const RAW_SESSION_ID = /\d{8}-[0-9a-f]{8}/gi;
const UUID_RE =
  /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;
const INTERNAL_KV_RE =
  /\b(?:run_id|session_id|project_id|request_id|reservation_id|worker_run_id|binding_id|item_id|requirement_id)\s*[:=]\s*\S+/gi;

const PREFIX_STRIP = /^\[(?:guard|Harness|内核|Terminal)\]\s*/i;

const PHRASE_REWRITES: Array<[RegExp, string]> = [
  [/^本会话 workspace evolved 均已允许。$/u, "本会话扩展区写入均已放行。"],
  [/来源 Session/gi, "来源会话"],
  [/Session 来源/gi, "会话来源"],
  [/Session 项目/gi, "会话项目"],
  [/多项目来源 Session/gi, "多项目来源会话"],
  [/no active project session/gi, "请先打开项目"],
  [/content_workspace_path/g, "暂存区路径"],
  [/workspace\/_staging/g, "暂存区"],
];

const TOOL_PATTERN = Object.keys(TOOL_DISPLAY_NAMES)
  .sort((a, b) => b.length - a.length)
  .map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
  .join("|");
const TOOL_TOKEN_RE = TOOL_PATTERN
  ? new RegExp(`\\b(${TOOL_PATTERN})\\b`, "gi")
  : null;

function collapseWs(text: string): string {
  return text
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([；。，、])/g, "$1")
    .trim();
}

/** Returns null when the notice should be omitted entirely (silent ack). */
export function polishSystemNoticeText(
  text: string,
  opts?: { plainText?: boolean },
): string | null {
  let out = text.replace(/\r\n/g, "\n").trim();
  if (!out) return null;
  if (SILENT_SYSTEM_ACKS.has(out)) return null;

  if (!opts?.plainText) {
    out = out.replace(PREFIX_STRIP, "");
    for (const [pattern, replacement] of PHRASE_REWRITES) {
      out = out.replace(pattern, replacement);
    }
    if (TOOL_TOKEN_RE) {
      out = out.replace(TOOL_TOKEN_RE, (token) => builtinToolDisplayName(token));
    }
    out = out.replace(RAW_SESSION_ID, "");
    out = out.replace(UUID_RE, "");
    out = out.replace(INTERNAL_KV_RE, "");
    out = collapseWs(out);
  }

  return out || null;
}

export function polishToolProgressText(text: string): string {
  const trimmed = text.trim();
  if (!trimmed) return trimmed;
  const split = trimmed.match(/^([a-z][\w]*)\s*[·•]\s*(.+)$/i);
  if (split) {
    return `${builtinToolDisplayName(split[1])} · ${split[2]}`;
  }
  return polishSystemNoticeText(trimmed) ?? trimmed;
}

export function workInstructionChatStatusLabel(
  state: string,
  deliveryStage?: string,
  reason?: string,
): string | null {
  switch (state) {
    case "sending":
      return "正在发送…";
    case "queued":
      return "已排队，等待处理";
    case "orchestration_dispatching":
      return "正在安排各项目…";
    case "orchestration_active":
      return "各项目施工中…";
    case "delivered":
      if (deliveryStage === "next_model_request_context") return null;
      return null;
    case "undelivered": {
      const detail = reason ? polishSystemNoticeText(reason) ?? reason : "";
      return detail ? `未送达：${detail}` : "未送达";
    }
    default:
      return "工作状态未确认";
  }
}

export function parallelChatDeliveryLabel(
  deliveryState: string,
  cancelRequested?: boolean,
): string | null {
  if (deliveryState === "pending") return "正在发送…";
  if (deliveryState === "delivered" && cancelRequested) return "正在取消…";
  if (deliveryState === "delivered" || deliveryState === "done") return null;
  if (deliveryState === "error") return "聊天未完成";
  return null;
}

export function parallelChatUserFootnote(deliveryState: string): string | null {
  if (deliveryState === "delivered" || deliveryState === "done") return null;
  return "仅聊天，未进入工作安排";
}

export function toolPromptChatStatusLabel(
  state: string,
  reason?: string,
): string | null {
  switch (state) {
    case "waiting":
      return "工具等待你的输入";
    case "sending":
      return "正在提交…";
    case "accepted":
      return null;
    default: {
      const detail = reason ? polishSystemNoticeText(reason) ?? reason : "";
      return detail ? `未接受：${detail}` : "工具未接受输入";
    }
  }
}

export function polishStatusBarNotice(text: string): string {
  const polished = polishSystemNoticeText(text);
  if (!polished) return "";
  return polished.length > 48 ? `${polished.slice(0, 48)}…` : polished;
}
