/** Main stage owned by the active rail tab. */
export type MainFocus = "chat" | "document" | "projects" | "settings" | "music" | "shell-dev";

/** Left icon rail. Each tab owns sidebar context and main stage together. */
export type RailTab = "now" | "docs" | "projects" | "settings" | "music" | "shell-dev";

export const RAIL_TAB_LABELS: Record<RailTab, { label: string; title: string }> = {
  now: { label: "当下", title: "当下" },
  docs: { label: "文档", title: "文档" },
  projects: { label: "项目", title: "我的项目" },
  settings: { label: "设置", title: "设置" },
  music: { label: "听歌", title: "听歌" },
  "shell-dev": { label: "壳预览", title: "壳开发预览" },
};

const RAIL_TABS = new Set<RailTab>(["now", "docs", "projects", "settings", "music", "shell-dev"]);

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
      return "chat";
    case "settings":
      return "settings";
    case "music":
      return "music";
    case "shell-dev":
      return "shell-dev";
    case "docs":
      return "document";
    case "projects":
      return "projects";
  }
}
