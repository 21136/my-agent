/** Main stage owned by the active rail tab. */
export type MainFocus = "chat" | "document" | "projects";

/** Left icon rail. Each tab owns sidebar context and main stage together. */
export type RailTab = "now" | "docs" | "projects" | "settings";

export const RAIL_TAB_LABELS: Record<RailTab, { label: string; title: string }> = {
  now: { label: "当下", title: "当下" },
  docs: { label: "文档", title: "文档" },
  projects: { label: "项目", title: "我的项目" },
  settings: { label: "设置", title: "设置" },
};

const RAIL_TABS = new Set<RailTab>(["now", "docs", "projects", "settings"]);

export function isRailTab(value: string | undefined): value is RailTab {
  return Boolean(value && RAIL_TABS.has(value as RailTab));
}

/** Legacy tabs (任务 / 会话) were folded into 当下; map old values safely. */
export function normalizeRailTab(value: string | undefined): RailTab {
  return isRailTab(value) ? value : "now";
}

export function mainFocusForRail(tab: RailTab): MainFocus {
  switch (tab) {
    case "now":
    case "settings":
      return "chat";
    case "docs":
      return "document";
    case "projects":
      return "projects";
  }
}
