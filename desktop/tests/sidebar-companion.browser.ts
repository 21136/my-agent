/** Browser-only event integration checks, using the real project state reducer. */
import type { ServerEvent } from "../src/api/ws";
import { applyProjectStateEvent, beginAtomicProjectSwitch, resetProjectScopedState, type ProjectPanelState } from "../src/shells/unified/project-panel";

export function runSidebarCompanionStateTests(): string[] {
  const results: string[] = [];
  function check(condition: unknown, label: string): void { if (!condition) throw Error(label); results.push(label); }
  const state = { projectId: "", projectSummary: "", switchInProgress: false, pendingPickerId: "", switchOverlay: null, undoTimerId: null } as ProjectPanelState;
  function event(project_id: string | null, name?: string, directory?: string): Extract<ServerEvent, {type: "project.state"}> {
    return {type: "project.state", project_id, project_root: project_id ? `workspace/${project_id}` : null, plan_status: "draft", map_markdown: "", project_summary: "", acceptance_command: null, acceptance_expected_exit: null, can_verify: false, project_display_name: name, project_directory: directory};
  }
  check(applyProjectStateEvent(state, event("imported-b1f2b097", "毕业设计", "F:/课题/毕业设计")) && state.projectDisplayName === "毕业设计" && state.projectDirectory === "F:/课题/毕业设计" && state.projectId === "imported-b1f2b097", "真实状态处理保留逻辑 ID，仅接收展示名称");
  beginAtomicProjectSwitch(state, "music");
  check(!state.projectDisplayName && !state.projectDirectory, "切换开始立即清空旧项目展示信息");
  check(!applyProjectStateEvent(state, event("imported-b1f2b097", "旧名字", "F:/旧目录")) && !state.projectDisplayName, "迟到的旧项目事件不能污染新卡片");
  check(applyProjectStateEvent(state, event("music", "Music Dreamer", "F:/Music Dreamer")) && state.projectDisplayName === "Music Dreamer", "目标项目事件正常更新展示名称");
  applyProjectStateEvent(state, event("music"));
  check(!state.projectDisplayName && !state.projectDirectory, "兼容未提供展示字段的旧服务端，不沿用旧名称");
  state.projectDisplayName = "Music Dreamer";
  state.projectDirectory = "F:/Music Dreamer";
  applyProjectStateEvent(state, event(null, "应丢弃", "F:/应丢弃"));
  check(state.projectId === "" && !state.projectDisplayName && !state.projectDirectory, "回到随聊不残留项目名称和路径");
  state.projectDisplayName = "遗留";
  state.projectDirectory = "F:/遗留";
  resetProjectScopedState(state);
  check(!state.projectDisplayName && !state.projectDirectory, "通用项目重置同步清理展示字段");
  return results;
}
