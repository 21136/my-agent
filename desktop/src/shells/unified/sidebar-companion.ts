/** Presentation-only project dock: never renames a project or changes its binding. */
export interface ProjectCompanionDock {
  partnerName: string;
  daysTogether: number;
  projectId: string;
  projectName?: string;
  projectDirectory?: string;
  avatarSrc: string;
  branch?: string;
  verification?: string;
}

function escape(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function readable(value: string): boolean {
  return Boolean(value) && !/^imported-[a-f0-9]{6,}$/i.test(value) && !/^[a-z]:$/i.test(value);
}

export function projectCompanionName(info: Pick<ProjectCompanionDock, "projectId" | "projectName" | "projectDirectory">): string {
  const name = (info.projectName || "").trim();
  if (readable(name)) return name;
  const folder = (info.projectDirectory || "").replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop() || "";
  if (readable(folder)) return folder;
  const id = info.projectId.trim();
  return readable(id) ? id : "";
}

export function renderProjectCompanionDockHtml(info: ProjectCompanionDock): string {
  const name = projectCompanionName(info);
  const heading = `${info.partnerName} · 相伴 ${info.daysTogether} 天`;
  const presence = name ? `正在陪你做「${name}」` : "正在陪你做项目";
  const details = ["点击切换项目", name ? `项目：${name}` : "当前项目", `项目编号：${info.projectId}`];
  if (info.projectDirectory) details.push(`目录：${info.projectDirectory}`);
  if (info.branch) details.push(`分支：${info.branch}`);
  if (info.verification) details.push(info.verification);
  const label = name ? `切换项目，当前：${name}` : "切换项目";
  return `<button type="button" class="sidebar-project-row-inner is-companion-dock" data-sidebar-action="open-projects" title="${escape(details.join("\n"))}" aria-label="${escape(label)}">
    <img class="sidebar-companion-ava" src="${escape(info.avatarSrc)}" alt="" width="34" height="34" draggable="false" />
    <span class="sidebar-project-main"><span class="sidebar-project-name">${escape(heading)}</span><span class="sidebar-project-sub is-project-presence">${escape(presence)}</span></span>
    <span class="sidebar-project-updown" aria-hidden="true"><i></i><i></i></span>
  </button>`;
}
