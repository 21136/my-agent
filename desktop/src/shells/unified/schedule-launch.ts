import type { WorkRecordsOverviewResultEvent } from "../../api/ws";

export type ScheduledWorkerCandidate = {
  projectId: string;
  itemId: string;
  requirementId: string;
  priority: number;
  task: string;
  suggestedReadTargets: string[];
  suggestedWriteTargets: string[];
};

const escapeHtml = (value: unknown): string => String(value ?? "")
  .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;").replaceAll("'", "&#39;");

const DEFAULT_READ_TARGETS = ["PROJECT.md", "README.md"];
const DEFAULT_WRITE_TARGETS = ["NOTES.md"];

function requirementsMap(result: WorkRecordsOverviewResultEvent): Map<string, string> {
  const requirements = new Map<string, string>();
  for (const group of result.groups) {
    if (group.kind !== "project" || group.visibility !== "visible") continue;
    for (const item of group.items) {
      for (const requirement of item.requirements) {
        if (typeof requirement.text === "string" && requirement.text.trim()) {
          requirements.set(`${group.id}\0${item.item_id}\0${requirement.requirement_id}`, requirement.text);
        }
      }
    }
  }
  return requirements;
}

function candidateFromPreviewRow(
  row: NonNullable<WorkRecordsOverviewResultEvent["schedule_preview"]>["candidates"][number],
  requirements: Map<string, string>,
): ScheduledWorkerCandidate | null {
  const key = `${row.project_id}\0${row.item_id}\0${row.requirement_id}`;
  const taskFromRow = typeof row.task === "string" && row.task.trim() ? row.task.trim() : "";
  const task = taskFromRow || requirements.get(key)?.trim() || "";
  if (!task || task.length > 1200) return null;
  const readTargets = Array.isArray(row.suggested_read_targets) && row.suggested_read_targets.length
    ? row.suggested_read_targets.filter(path => typeof path === "string" && path.trim())
    : DEFAULT_READ_TARGETS;
  const writeTargets = Array.isArray(row.suggested_write_targets) && row.suggested_write_targets.length
    ? row.suggested_write_targets.filter(path => typeof path === "string" && path.trim())
    : DEFAULT_WRITE_TARGETS;
  if (!writeTargets.length) return null;
  return {
    projectId: row.project_id,
    itemId: row.item_id,
    requirementId: row.requirement_id,
    priority: row.priority,
    task,
    suggestedReadTargets: readTargets,
    suggestedWriteTargets: writeTargets,
  };
}

/** Ordered parallel launch batch: up to two distinct projects from the authorized preview. */
export function scheduledWorkerCandidates(result: WorkRecordsOverviewResultEvent | null): ScheduledWorkerCandidate[] {
  if (!result || result.state !== "visible" || result.aggregation_scope !== "selected_project_sessions"
    || !result.groups?.length || result.schedule_preview?.state !== "visible"
    || result.schedule_preview.omissions?.length) return [];
  const previewRows = result.schedule_preview.candidates ?? [];
  if (!previewRows.length) return [];
  const requirements = requirementsMap(result);
  const seenProjects = new Set<string>();
  const batch: ScheduledWorkerCandidate[] = [];
  for (const row of previewRows) {
    const candidate = candidateFromPreviewRow(row, requirements);
    if (!candidate || seenProjects.has(candidate.projectId)) continue;
    seenProjects.add(candidate.projectId);
    batch.push(candidate);
    if (batch.length >= 2) break;
  }
  return batch;
}

function pathLines(targets: string[]): string {
  return targets.join("\n");
}

/** Render a read-only summary + prefilled scope fields and a one-click parallel dispatch CTA. */
export function renderScheduleLaunchForm(result: WorkRecordsOverviewResultEvent | null): string {
  const candidates = scheduledWorkerCandidates(result);
  if (!candidates.length) return "";
  const rows = candidates.map((candidate, index) => {
    const title = `${candidate.projectId} · ${candidate.itemId} · ${candidate.requirementId} · P${candidate.priority}`;
    return `<article class="unified-schedule-worker" data-schedule-worker data-candidate-index="${index}" aria-label="${escapeHtml(title)}"><header class="unified-schedule-worker-head"><strong>${escapeHtml(title)}</strong></header><p class="unified-schedule-task" data-schedule-source-task>来源要求：${escapeHtml(candidate.task)}</p><textarea data-schedule-task hidden readonly>${escapeHtml(candidate.task)}</textarea><label>可读取路径（每行一项，可微调）<textarea data-schedule-read rows="2" required>${escapeHtml(pathLines(candidate.suggestedReadTargets))}</textarea></label><label>可写入路径（每行一项，可微调）<textarea data-schedule-write rows="2" required>${escapeHtml(pathLines(candidate.suggestedWriteTargets))}</textarea></label></article>`;
  }).join("");
  const batchNote = candidates.length > 1
    ? "将按预览<strong>并行</strong>提交至多两个不同项目的 worker；这不是把事项放回排队或等待上下文。"
    : "将按预览启动一个 worker；任务与范围已根据授权候选预填，仍可微调路径。";
  return `<section class="unified-schedule-launch" data-schedule-launch aria-label="按预览并行启动 worker"><header class="unified-schedule-launch-head"><h3>按预览启动</h3></header><div class="unified-schedule-launch-form"><p class="unified-schedule-launch-lede">上方是只读调度预览；此处根据同一批候选预填任务与相对路径。点启动后走 <code>work-records.schedule.dispatch</code> → <code>run_project_workers</code>，服务端仍会核对来源、并发上限并逐项确认。</p><p class="unified-schedule-launch-batch-note">${batchNote}</p><div class="unified-schedule-worker-rows">${rows}</div><button type="button" class="unified-btn unified-btn-accent" data-schedule-launch-send>按预览启动</button><p class="unified-schedule-launch-note">启动后进度见「当前工作 / 工作动态 / 状态栏」；这不代表事项已完成或验证通过。</p></div></section>`;
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

function workerPayload(
  candidate: ScheduledWorkerCandidate,
  taskText: string,
  readText: string,
  writeText: string,
) {
  if (typeof taskText !== "string" || !taskText.trim() || taskText.trim() !== candidate.task) {
    throw new Error("任务必须与来源要求原文一致；请勿改写预填内容。");
  }
  return {
    project_id: candidate.projectId,
    item_id: candidate.itemId,
    requirement_id: candidate.requirementId,
    priority: candidate.priority,
    task: taskText.trim(),
    read_targets: pathsFromTextarea(readText),
    write_targets: pathsFromTextarea(writeText),
  };
}

export type ScheduleDispatchRowInput = {
  candidateIndex: number;
  taskText: string;
  readText: string;
  writeText: string;
};

/** Build explicit parallel dispatch payload for the workbench schedule.launch path. */
export function buildScheduleDispatchWorkers(
  result: WorkRecordsOverviewResultEvent | null,
  rows: ScheduleDispatchRowInput[],
) {
  const candidates = scheduledWorkerCandidates(result);
  if (!rows.length || rows.length > 2) throw new Error("一次最多并行启动两个不同项目的 worker。");
  const workers = rows.map(row => {
    const candidate = candidates[row.candidateIndex];
    if (!candidate) throw new Error("候选已变化或不可用，请重新读取当前工作概况。");
    return workerPayload(candidate, row.taskText, row.readText, row.writeText);
  });
  if (new Set(workers.map(worker => worker.project_id)).size !== workers.length) {
    throw new Error("不能为同一项目并行提交两个 worker。");
  }
  return {
    projectIds: result?.selected_project_ids ?? workers.map(worker => worker.project_id),
    workers,
  };
}

/** Legacy chat-shaped intent builder; schedule tab uses direct dispatch instead. */
export function buildScheduleLaunchMessage(
  result: WorkRecordsOverviewResultEvent | null,
  candidateIndex: number,
  taskText: string,
  readText: string,
  writeText: string,
): string {
  const candidate = scheduledWorkerCandidates(result)[candidateIndex];
  if (!candidate) throw new Error("候选已变化或不可用，请重新读取当前工作概况。");
  const row = workerPayload(candidate, taskText, readText, writeText);
  return [
    "我明确选择启动以下一个已排队要求；请在本回合调用 run_project_workers，且只提交这一项。",
    `来源项目：${row.project_id}`,
    `事项 ID：${row.item_id}`,
    `要求 ID：${row.requirement_id}`,
    `本次任务（用户填写；须与来源要求原文完全一致）：${row.task}`,
    `来源要求原文：${candidate.task}`,
    `读取范围：${row.read_targets.join("、")}`,
    `写入范围：${row.write_targets.join("、")}`,
    `workers 中 task 必须精确为本次任务，schedule_candidate 的 project_id、item_id、requirement_id、priority 必须分别精确为上述 ${row.project_id}、${row.item_id}、${row.requirement_id}、${row.priority}；read_targets/write_targets 必须严格使用上述范围。若不支持或无法逐项满足，不要改用其他项目、要求、路径或写入方式，请说明未启动。`,
  ].join("\n");
}
