import http from "node:http";

export type ApprovalPreferenceMode = "manual" | "auto" | "full";
export type ApprovalPreferencesPayload = {
  type: "approval_preferences.state" | "approval_preferences.done";
  status: "ok" | "rejected";
  state_known?: boolean;
  approval_mode: ApprovalPreferenceMode;
  saved_approval_mode: ApprovalPreferenceMode;
  available_modes: ApprovalPreferenceMode[];
  message?: string;
};
export type ApprovalControlBootstrap = { port: number; token: string };

const SECRET = /^[0-9a-f]{64}$/;
const MODES = ["manual", "auto", "full"] as const;

export function parseApprovalControlBootstrap(value: unknown): ApprovalControlBootstrap | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (item.approval_control_available !== true || !Number.isSafeInteger(item.approval_control_port)
      || Number(item.approval_control_port) < 1 || Number(item.approval_control_port) > 65535
      || typeof item.approval_control_token !== "string" || !SECRET.test(item.approval_control_token)) return null;
  return { port: item.approval_control_port as number, token: item.approval_control_token };
}

function validPayload(value: unknown, eventType: ApprovalPreferencesPayload["type"]): value is ApprovalPreferencesPayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const item = value as Record<string, unknown>;
  const modes = item.available_modes;
  return item.type === eventType && (item.status === "ok" || item.status === "rejected")
    && MODES.includes(item.approval_mode as ApprovalPreferenceMode)
    && MODES.includes(item.saved_approval_mode as ApprovalPreferenceMode)
    && (item.state_known === undefined || typeof item.state_known === "boolean")
    && Array.isArray(modes) && modes.length === 3
    && MODES.every((mode) => modes.includes(mode))
    && Object.keys(item).every((key) => ["type", "status", "state_known", "approval_mode", "saved_approval_mode", "available_modes", "message"].includes(key))
    && (item.message === undefined || typeof item.message === "string");
}

export function requestApprovalPreferences(
  control: ApprovalControlBootstrap,
  mode?: ApprovalPreferenceMode,
): Promise<ApprovalPreferencesPayload> {
  if (!Number.isSafeInteger(control?.port) || control.port < 1 || control.port > 65535 || !SECRET.test(control.token)) {
    return Promise.reject(new Error("可信审批控制接口不可用"));
  }
  if (mode !== undefined && !MODES.includes(mode)) return Promise.reject(new Error("审批档位无效"));
  const eventType = mode === undefined ? "approval_preferences.state" : "approval_preferences.done";
  const body = mode === undefined ? "" : JSON.stringify({ type: "approval_preferences.set", approval_mode: mode });
  return new Promise((resolve, reject) => {
    const request = http.request({
      host: "127.0.0.1", port: control.port,
      path: "/approval-preferences", method: mode === undefined ? "GET" : "POST",
      headers: {
        Authorization: `Bearer ${control.token}`,
        ...(mode === undefined ? {} : { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(body) }),
      },
      timeout: 5000,
    }, (response) => {
      const chunks: Buffer[] = [];
      let size = 0;
      response.on("data", (chunk: Buffer) => {
        size += chunk.length;
        if (size > 8192) request.destroy(new Error("审批设置响应超过限制"));
        else chunks.push(chunk);
      });
      response.on("end", () => {
        if (response.statusCode !== 200) return reject(new Error("可信审批控制接口拒绝请求"));
        try {
          const payload: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          if (!validPayload(payload, eventType)) throw new Error("审批设置响应格式无效");
          resolve(payload);
        } catch (error) { reject(error); }
      });
    });
    request.on("timeout", () => request.destroy(new Error("审批控制接口超时")));
    request.on("error", reject);
    request.end(body);
  });
}
