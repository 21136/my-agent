import type { WorkRecordsOverviewResultEvent } from "../../api/ws";

export type ScheduledWorkerCandidate = {
  projectId: string;
  itemId: string;
  requirementId: string;
  priority: number;
  task: string;
};

const escapeHtml = (value: unknown): string => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

/** Only offer candidates whose exact queued requirement text is in this same server projection. */
export function scheduledWorkerCandidates(result: WorkRecordsOverviewResultEvent | null): ScheduledWorkerCandidate[] {
  if (!result || result.state !== "visible" || result.aggregation_scope !== "selected_project_sessions"
    || result.schedule_preview?.state !== "visible" || result.schedule_preview.omissions?.length) return [];
  const requirements = new Map<string, string>();
  for (const group of result.groups) {
    if (group.kind !== "project" || group.visibility !== "visible") continue;
    for (const item of group.items) {
      for (const requirement of item.requirements) {
        if (requirement.status !== "queued" && requirement.status !== "pending") continue;
        requirements.set(`${group.id}\0${item.item_id}\0${requirement.requirement_id}`, requirement.text);
      }
    }
  }
  const head = result.schedule_preview.candidates[0];
  if (!head) return [];
  const task = requirements.get(`${head.project_id}\0${head.item_id}\0${head.requirement_id}`);
  if (typeof task !== "string" || !task.trim() || task.length > 1200) return [];
  return [{ projectId: head.project_id, itemId: head.item_id,
    requirementId: head.requirement_id, priority: head.priority, task }];
}

/** Render a user-operated task form; it does not send, authorize, or start anything by itself. */
export function renderScheduleLaunchForm(result: WorkRecordsOverviewResultEvent | null): string {
  const candidates = scheduledWorkerCandidates(result);
  if (!candidates.length) return "";
  const options = candidates.map((candidate, index) => `<option value="${index}">${escapeHtml(candidate.projectId)} · ${escapeHtml(candidate.itemId)} · ${escapeHtml(candidate.requirementId)} · P${candidate.priority}</option>`).join("");
  return `<section class="unified-schedule-launch" data-schedule-launch aria-label="准备启动一个排队要求"><header class="unified-schedule-launch-head"><h3>准备启动一个排队要求</h3></header><div class="unified-schedule-launch-form"><p>选择具体要求并填写本次任务及读写范围。任务必须与来源要求原文完全一致，避免只读预览变成授权；这里只发送真实用户消息，服务端仍会重新核对来源、状态、范围并逐次请求现有确认。</p><label>排队要求<select data-schedule-candidate>${options}</select></label><p data-schedule-source-task class="unified-schedule-task">来源要求：${escapeHtml(candidates[0].task)}</p><label>本次任务（须与上方来源要求完全一致）<textarea data-schedule-task rows="3" required maxlength="1200" placeholder="请填写上方来源要求的完整原文"></textarea></label><label>可读取路径（每行一项）<textarea data-schedule-read rows="2" required placeholder="例如 PROJECT.md"></textarea></label><label>可写入路径（每行一项）<textarea data-schedule-write rows="2" required placeholder="例如 NOTES.md"></textarea></label><button type="button" class="unified-btn unified-btn-accent" data-schedule-launch-send>发送启动请求</button><p class="unified-schedule-launch-note">发送后进入普通 Agent 回合；模型是否发起工具调用仍取决于 Agent 回合，实际启动只可能发生在服务端 fresh 检查和逐次确认通过后。</p></div></section>`;
}

function pathsFromTextarea(value: string): string[] {
  const rows = value.split(/\r?\n/).map(path => path.trim()).filter(Boolean);
  if (!rows.length || rows.length > 12 || rows.some(path => path.length > 240
      || /[\0\r\n]/.test(path) || path.startsWith("/") || path.startsWith("\\\\")
      || /^[a-z]:/i.test(path) || path.split(/[\\/]+/).some(segment => segment === ".." || segment === "."))) {
    throw new Error("请填写 1–12 个有效的相对路径，每行一个；不能使用绝对路径或越界路径。");
  }
  return [...new Set(rows)];
}

/** Build explicit user intent from the selected candidate and user-entered file scope. */
export function buildScheduleDispatchWorkers(
  result: WorkRecordsOverviewResultEvent | null,
  candidateIndex: number,
  taskText: string,
  readText: string,
  writeText: string,
) {
  const candidate = scheduledWorkerCandidates(result)[candidateIndex];
  if (!candidate) throw new Error("候选已变化或不可用，请重新读取当前工作概况。");
  if (typeof taskText !== "string" || !taskText.trim() || taskText.trim() !== candidate.task)
    throw new Error("请将本次任务完整填写为来源要求原文；来源只读预览本身不会授权启动。");
  const readTargets = pathsFromTextarea(readText);
  const writeTargets = pathsFromTextarea(writeText);
  return {
    projectIds: result?.selected_project_ids ?? [candidate.projectId],
    workers: [{
      project_id: candidate.projectId,
      item_id: candidate.itemId,
      requirement_id: candidate.requirementId,
      priority: candidate.priority,
      task: taskText.trim(),
      read_targets: readTargets,
      write_targets: writeTargets,
    }],
  };
}

export function buildScheduleLaunchMessage(
  result: WorkRecordsOverviewResultEvent | null,
  candidateIndex: number,
  taskText: string,
  readText: string,
  writeText: string,
): string {
  const candidate = scheduledWorkerCandidates(result)[candidateIndex];
  if (!candidate) throw new Error("候选已变化或不可用，请重新读取当前工作概况。");
  if (typeof taskText !== "string" || !taskText.trim() || taskText.trim() !== candidate.task)
    throw new Error("请将本次任务完整填写为来源要求原文；来源只读预览本身不会授权启动。");
  const readTargets = pathsFromTextarea(readText);
  const writeTargets = pathsFromTextarea(writeText);
  return [
    "我明确选择启动以下一个已排队要求；请在本回合调用 run_project_workers，且只提交这一项。",
    `来源项目：${candidate.projectId}`,
    `事项 ID：${candidate.itemId}`,
    `要求 ID：${candidate.requirementId}`,
    `本次任务（用户填写；须与来源要求原文完全一致）：${taskText.trim()}`,
    `来源要求原文：${candidate.task}`,
    `读取范围：${readTargets.join("、")}`,
    `写入范围：${writeTargets.join("、")}`,
    `workers 中 task 必须精确为本次任务，schedule_candidate 的 project_id、item_id、requirement_id、priority 必须分别精确为上述 ${candidate.projectId}、${candidate.itemId}、${candidate.requirementId}、${candidate.priority}；read_targets/write_targets 必须严格使用上述范围。若不支持或无法逐项满足，不要改用其他项目、要求、路径或写入方式，请说明未启动。`,
  ].join("\n");
}
