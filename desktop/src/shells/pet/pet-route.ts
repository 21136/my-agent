import type { ServerEvent, ShellId } from "../../api/ws";

export type PetRouteEvent = Extract<ServerEvent, { type: "ui.route" }>;
export type RouteTier = "auto" | "prompt" | "ignore";

const AUTO_REASON_MARKERS = [
  "proposal 待处理",
  "计划待确认",
  "养 agent",
  "evolved",
  "造 / 改 evolved",
  "workspace 项目",
  "workspace 开发",
  "只读探索 evolve",
] as const;

export function classifyPetRoute(event: PetRouteEvent): RouteTier {
  if (!event.auto || event.shell === "daily") {
    return "ignore";
  }
  if (event.shell === "govern") {
    return "prompt";
  }
  if (event.shell === "grow" || event.shell === "project") {
    return isAutoOpenReason(event.reason) ? "auto" : "prompt";
  }
  return "ignore";
}

function isAutoOpenReason(reason: string): boolean {
  return AUTO_REASON_MARKERS.some((marker) => reason.includes(marker));
}

export function resolveWorkbenchShell(shell: ShellId): { shell: ShellId; mappedNotice?: string } {
  if (shell === "govern") {
    return {
      shell: "grow",
      mappedNotice: "治理壳未就绪，已在生长壳打开",
    };
  }
  return { shell };
}

export function formatRouteNotice(event: PetRouteEvent, tier: RouteTier): string {
  const topicNote =
    event.topics_added && event.topics_added.length
      ? ` · 已加主题 ${event.topics_added.join(", ")}`
      : "";
  if (tier === "auto") {
    return `已切到工作台：${event.reason}${topicNote}`;
  }
  return `更适合在工作台：${event.reason}${topicNote}`;
}
