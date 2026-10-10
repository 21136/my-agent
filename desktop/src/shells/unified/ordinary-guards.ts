/** Ordinary-mode UI guards: no raw session ids, no useless error status. */

const RAW_SESSION_ID = /\d{8}-[0-9a-f]{8}/i;

export function canRequestProjectDocs(projectId: string | null | undefined): boolean {
  return Boolean((projectId || "").trim());
}

export function userFacingErrorStatus(message: string | null | undefined): string {
  const text = (message || "").replace(/\s+/g, " ").trim();
  if (!text || text === "错误") return "出了点问题，请重试";
  if (/no active project session/i.test(text)) return "请先打开项目";
  const short = text.length > 72 ? `${text.slice(0, 72)}…` : text;
  return short.startsWith("出错") ? short : `出错了：${short}`;
}

/** Resume / session-id notices stay in the chat. The status line must not repeat them. */
export function noticeMirrorsToStatus(text: string): boolean {
  const line = (text || "").trim();
  if (!line) return false;
  if (/续接项目|专用会话|已续接项目|新开一条会话/.test(line)) return false;
  if (RAW_SESSION_ID.test(line)) return false;
  return /狂奔|恢复|租约|续接|占用|启动狂奔/.test(line);
}

export function userFacingContextStatus(message: string | null | undefined): string {
  const text = (message || "").trim();
  if (!text) return "已切换工作上下文";
  if (/续接项目|专用会话|已续接项目|新开一条会话/.test(text) || RAW_SESSION_ID.test(text)) {
    return "已打开项目";
  }
  return text.length > 48 ? `${text.slice(0, 48)}…` : text;
}

export function sessionBannerStatus(modelLabel: string, turnModeLabel: string): string {
  const model = (modelLabel || "").trim() || "Flash";
  const mode = (turnModeLabel || "").trim();
  return mode ? `${model} · ${mode}` : model;
}

export function sessionListMeta(meta: string, sessionId: string): string {
  const text = (meta || "").trim();
  if (text && text !== sessionId && !RAW_SESSION_ID.test(text)) return text;
  return "会话记录";
}

export function userFacingThreadTitle(title: string, sessionId: string): string {
  const text = (title || "").trim();
  if (!text || text === sessionId || RAW_SESSION_ID.test(text)) return "未命名会话";
  return text;
}
