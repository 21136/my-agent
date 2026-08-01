import type { ServerEvent } from "../../api/ws";

/** @deprecated — ui.route removed; stub kept for backward compat during Phase 3→4 cleanup */
export type RouteTier = "auto" | "prompt" | "ignore";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function classifyPetRoute(event: any): RouteTier {
  if (!event || !event.auto || event.shell === "daily") return "ignore";
  return "prompt";
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function formatRouteNotice(event: any, _tier: RouteTier): string {
  return `在工作台：${event?.reason ?? ""}`;
}

export function resolveWorkbenchShell(shell: string): { shell: string; mappedNotice?: string } {
  return { shell };
}
