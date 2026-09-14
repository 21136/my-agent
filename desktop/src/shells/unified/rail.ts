import type { MainFocus } from "./plan-review";

/** Bottom left-rail tabs. Each tab owns sidebar context and main stage together. */
export type RailTab = "now" | "tasks" | "docs" | "threads" | "projects";

export const RAIL_TAB_LABELS: Record<RailTab, { label: string; title: string }> = {
  now: { label: "当下", title: "当下" },
  tasks: { label: "任务", title: "任务" },
  docs: { label: "文档", title: "文档" },
  threads: { label: "会话", title: "会话线" },
  projects: { label: "项目", title: "我的项目" },
};

const RAIL_TABS = new Set<RailTab>(["now", "tasks", "docs", "threads", "projects"]);

export function isRailTab(value: string | undefined): value is RailTab {
  return Boolean(value && RAIL_TABS.has(value as RailTab));
}

export function mainFocusForRail(tab: RailTab): MainFocus {
  switch (tab) {
    case "now":
      return "chat";
    case "tasks":
      return "plan_full";
    case "docs":
      return "document";
    case "threads":
      return "chat";
    case "projects":
      return "projects";
  }
}
