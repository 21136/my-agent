/** Strictly parse only the structured server auto-candidate contract; no text inference. */
export type WorkRecordAutoCandidate = {
  title: string; requirement: string;
  scope: { kind: "independent"; object_ref: null } | { kind: "project"; project_id: string };
  session_id: string; request_id: string; run_id: string; sequence: number;
  terminal_state: "settled"; finish_reason: string | null;
} & ({ operation?: never; item_id?: never } | { operation: "append_requirement"; item_id: string });
const autoCandidateUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const autoCandidateObject = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === "object" && !Array.isArray(x);
const autoCandidateExact = (o: Record<string, unknown>, keys: string[]): boolean => Object.keys(o).length === keys.length && keys.every(key => Object.hasOwn(o, key));
const autoCandidateReasons = new Set(["completed", "stop", "cancelled", "timeout", "error", "paused", "unknown", "stopped", "runaway_paused", "task_paused", "segment_cap_pause", "total_cap_exceeded"]);
export function parseWorkRecordAutoCandidate(value: unknown, sessionId: string): WorkRecordAutoCandidate | null {
  const baseFields = ["title", "requirement", "scope", "session_id", "request_id", "run_id", "sequence", "terminal_state", "finish_reason"];
  if (!autoCandidateObject(value) || !(autoCandidateExact(value, baseFields)
      || (autoCandidateExact(value, [...baseFields, "operation", "item_id"])
          && value.operation === "append_requirement" && typeof value.item_id === "string" && autoCandidateUuid.test(value.item_id)))
    || !sessionId || value.session_id !== sessionId
    || typeof value.title !== "string" || !value.title.trim() || value.title !== value.title.trim()
    || typeof value.requirement !== "string" || !value.requirement.trim() || value.requirement !== value.requirement.trim()
    || typeof value.request_id !== "string" || !autoCandidateUuid.test(value.request_id)
    || typeof value.run_id !== "string" || !autoCandidateUuid.test(value.run_id)
    || !Number.isSafeInteger(value.sequence) || (value.sequence as number) < 1 || value.terminal_state !== "settled"
    || !(value.finish_reason === null || typeof value.finish_reason === "string" && autoCandidateReasons.has(value.finish_reason))
    || !autoCandidateObject(value.scope)) return null;
  const scope = value.scope;
  if (!(scope.kind === "independent" && autoCandidateExact(scope, ["kind", "object_ref"]) && scope.object_ref === null)
    && !(scope.kind === "project" && autoCandidateExact(scope, ["kind", "project_id"]) && typeof scope.project_id === "string" && !!scope.project_id && scope.project_id === scope.project_id.trim())) return null;
  return value as unknown as WorkRecordAutoCandidate;
}

export type ProjectWorkerVerificationPayload = {
  session_id: string; project_id: string; item_id: string; requirement_id: string;
  project_name: string; requirement_text: string; pytest_target: string;
  worker_run_id: string; reservation_id: string;
};
const projectWorkerVerificationKeys = ["session_id", "project_id", "item_id", "requirement_id", "project_name", "requirement_text", "pytest_target", "worker_run_id", "reservation_id"];
export function parseProjectWorkerVerification(value: unknown, sessionId: string): ProjectWorkerVerificationPayload | null {
  if (!obj(value) || !sessionId || !keys(value, projectWorkerVerificationKeys)) return null;
  if (!projectWorkerVerificationKeys.every(key => typeof value[key] === "string" && (value[key] as string).trim().length > 0 && value[key] === (value[key] as string).trim())
      || value.session_id !== sessionId) return null;
  return value as unknown as ProjectWorkerVerificationPayload;
}
/** Compact confirm card — headline + reason + optional detail (UX-030). */

export type ConfirmPreviewView = {
  headline: string;
  /** Full agent-relative path when the confirm is a file write/patch. */
  targetPath: string;
  reason: string;
  detail: string;
  compact: boolean;
};

const CONFIRM_POLICY_LABELS: Record<string, string> = {
  "confirm:plan_domain": "计划文档（PROJECT / TASKS / ENV 等）修改前需你确认",
  "confirm:sensitive": "敏感路径写入前需你确认",
  "confirm:new_file": "新建文件前需你确认",
  "confirm:outside_project": "写入路径在项目外，需你确认",
  "confirm:no_project_binding": "当前未绑定项目，需你确认",
  "confirm:empty_path": "目标路径为空，需你确认",
  "confirm:host": "托管区操作需你确认",
  "confirm:other": "该写入需你确认",
};

function humanizePolicyLine(policyLine: string): string {
  const raw = policyLine.replace(/^写入策略：/, "").trim();
  return CONFIRM_POLICY_LABELS[raw] ?? (raw.startsWith("confirm:") ? "该操作需你确认" : raw);
}

function basename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").pop() || path;
}

// This exact prompt and payload shape identify card recall, not other model confirmations.
const RECALL_SYSTEM = "You are a read-only semantic recognizer. Compare the question directly against EVERY candidate supplied; the list is complete and must not be searched, reordered, shortened, or prefiltered. Return exactly one JSON object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from the supplied candidates. Do not invent IDs or add explanations. Never call tools, alter records, or infer/access any other session.";
const CARD_RECALL_BATCH_SYSTEM = "You are a read-only semantic recognizer. Compare the question with EVERY candidate in this batch only. Other batches may exist; do not infer their contents. Return exactly one JSON object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from this batch. Do not invent IDs, explain, call tools, alter records, or infer/access any other session.";
const obj = (x: unknown): x is Record<string, unknown> => x !== null && typeof x === "object" && !Array.isArray(x);
const keys = (x: Record<string, unknown>, names: string[]) => Object.keys(x).length === names.length && names.every(k => Object.hasOwn(x, k));
type RecallCandidate = { id: string; title: string; excerpt: string };
type RecallRequest = { question: string; candidates: RecallCandidate[] };
type RecallPreview = { endpoint: string; batch: boolean; requests: RecallRequest[] };

function parseRecallPreview(preview: string): RecallPreview | null {
  try {
    const p: unknown = JSON.parse(preview);
    if (!obj(p) || p.model_id !== "0x567-flash" || p.vendor !== "0x567" || typeof p.session_id !== "string" || !p.session_id || typeof p.endpoint !== "string") return null;
    const batch = Object.hasOwn(p, "provider_payloads");
    if (!keys(p, ["model_id", "vendor", "endpoint", "session_id", batch ? "provider_payloads" : "provider_payload"])) return null;
    if (batch && new TextEncoder().encode(preview).byteLength > 2 * 1024 * 1024) return null;
    const endpoint = new URL(p.endpoint);
    if (endpoint.protocol !== "https:" || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) return null;
    const bodies: unknown[] = batch ? (Array.isArray(p.provider_payloads) ? p.provider_payloads : []) : [p.provider_payload];
    if (!bodies.length || bodies.length > 32) return null;
    const requests: RecallRequest[] = [];
    for (const body of bodies) {
      if (!obj(body) || !keys(body, ["model", "messages", "temperature", "stream", "response_format"]) || body.model !== "gpt-6-luna" || body.temperature !== 0 || body.stream !== false || !obj(body.response_format) || !keys(body.response_format, ["type"]) || body.response_format.type !== "json_object" || !Array.isArray(body.messages) || body.messages.length !== 2) return null;
      const [system, user] = body.messages;
      if (!obj(system) || !keys(system, ["role", "content"]) || system.role !== "system" || system.content !== (batch ? CARD_RECALL_BATCH_SYSTEM : RECALL_SYSTEM) || !obj(user) || !keys(user, ["role", "content"]) || user.role !== "user" || typeof user.content !== "string") return null;
      const content: unknown = JSON.parse(user.content);
      if (!obj(content) || !keys(content, ["question", "candidates"]) || typeof content.question !== "string" || !content.question.trim() || !Array.isArray(content.candidates) || !content.candidates.length) return null;
      for (const candidate of content.candidates) {
        if (!obj(candidate) || !keys(candidate, ["id", "title", "excerpt"]) || typeof candidate.id !== "string" || !/^wc-[0-9a-f]{64}$/.test(candidate.id) || typeof candidate.title !== "string" || typeof candidate.excerpt !== "string") return null;
        const excerpt: unknown = JSON.parse(candidate.excerpt);
        if (!obj(excerpt)) return null;
        const projectScoped = excerpt.scope === "project";
        const excerptShapeIsValid = projectScoped
          ? keys(excerpt, ["scope", "items"])
            || (keys(excerpt, ["scope", "items", "project_id"])
              && typeof excerpt.project_id === "string" && !!excerpt.project_id.trim())
          : excerpt.scope === "independent" && keys(excerpt, ["scope", "items"]);
        if (!excerptShapeIsValid
            || !Array.isArray(excerpt.items) || !excerpt.items.every((item: unknown) => obj(item) && keys(item, ["title", "requirements"]) && typeof item.title === "string" && Array.isArray(item.requirements) && item.requirements.every((r: unknown) => typeof r === "string"))) return null;
      }
      requests.push(content as RecallRequest);
    }
    return { endpoint: p.endpoint, batch, requests };
  } catch { return null; }
}
type DialogueRecallCandidate = { id: string; title: string; excerpt: string };
type DialogueRecallRequest = { question: string; candidates: DialogueRecallCandidate[] };
type DialogueRecallPreview = { endpoint: string; sessionId: string; batch: boolean; requests: DialogueRecallRequest[] };

/** Recognize only the frozen complete single or batched dialogue-recall request. */
function parseDialogueRecallPreview(preview: string): DialogueRecallPreview | null {
  try {
    const raw: unknown = JSON.parse(preview);
    if (!obj(raw) || raw.model_id !== "0x567-flash" || raw.vendor !== "0x567"
        || typeof raw.endpoint !== "string" || typeof raw.session_id !== "string" || !raw.session_id) return null;
    const batch = Object.hasOwn(raw, "provider_payloads");
    if (!keys(raw, ["model_id", "vendor", "endpoint", "session_id", batch ? "provider_payloads" : "provider_payload"])) return null;
    const byteLimit = batch ? 2 * 1024 * 1024 : 768 * 1024;
    if (new TextEncoder().encode(preview).byteLength > byteLimit) return null;
    const endpoint = new URL(raw.endpoint);
    if (endpoint.protocol !== "https:" || endpoint.username || endpoint.password || endpoint.search || endpoint.hash) return null;
    const bodies: unknown[] = batch ? (Array.isArray(raw.provider_payloads) ? raw.provider_payloads : []) : [raw.provider_payload];
    if (!bodies.length || bodies.length > 32) return null;
    const requests: DialogueRecallRequest[] = [];
    let previousIndex = -1;
    let commonQuestion: string | null = null;
    for (const body of bodies) {
      if (!obj(body) || !keys(body, ["model", "messages", "temperature", "stream", "response_format"])
          || body.model !== "gpt-6-luna" || body.temperature !== 0 || body.stream !== false
          || !obj(body.response_format) || !keys(body.response_format, ["type"])
          || body.response_format.type !== "json_object" || !Array.isArray(body.messages) || body.messages.length !== 2) return null;
      const [system, user] = body.messages;
      if (!obj(system) || !keys(system, ["role", "content"]) || system.role !== "system"
          || system.content !== (batch ? CARD_RECALL_BATCH_SYSTEM : RECALL_SYSTEM)
          || !obj(user) || !keys(user, ["role", "content"]) || user.role !== "user" || typeof user.content !== "string") return null;
      const content: unknown = JSON.parse(user.content);
      if (!obj(content) || !keys(content, ["question", "candidates"])
          || typeof content.question !== "string" || !content.question.trim()
          || !Array.isArray(content.candidates) || !content.candidates.length) return null;
      if (commonQuestion !== null && content.question !== commonQuestion) return null;
      commonQuestion ??= content.question;
      const candidates: DialogueRecallCandidate[] = [];
      for (const candidate of content.candidates) {
        if (!obj(candidate) || !keys(candidate, ["id", "title", "excerpt"])
            || typeof candidate.id !== "string" || typeof candidate.title !== "string"
            || typeof candidate.excerpt !== "string" || !candidate.excerpt.trim()) return null;
        const match = /^msg-(\d{8,})$/.exec(candidate.id);
        if (!match) return null;
        const zeroBasedIndex = Number(match[1]);
        if (!Number.isSafeInteger(zeroBasedIndex) || zeroBasedIndex <= previousIndex
            || String(zeroBasedIndex).padStart(8, "0") !== match[1]) return null;
        const roleTitle = /^(user|assistant) message ([1-9]\d*)$/.exec(candidate.title);
        if (!roleTitle || Number(roleTitle[2]) !== zeroBasedIndex + 1) return null;
        previousIndex = zeroBasedIndex;
        candidates.push(candidate as DialogueRecallCandidate);
      }
      requests.push({ question: content.question, candidates });
    }
    return { endpoint: raw.endpoint, sessionId: raw.session_id, batch, requests };
  } catch { return null; }
}

export function isWorkCardRecallPreview(preview: string): boolean {
  return parseRecallPreview(preview) !== null || parseDialogueRecallPreview(preview) !== null;
}

// JSON string notation makes embedded control characters and whitespace unambiguous.
const recallValue = (text: string) => `<span class="unified-recall-value">${escapeHtml(JSON.stringify(text))}</span>`;
function renderRecallRequest(request: RecallRequest): string {
  const candidates = request.candidates.map((candidate, index) => {
    const excerpt = JSON.parse(candidate.excerpt) as { scope: string; items: Array<{ title: string; requirements: string[] }> };
    const items = excerpt.items.map((item, itemIndex) => `<div class="unified-recall-item"><div>事项 ${itemIndex + 1} 标题：${recallValue(item.title)}</div>${item.requirements.length ? `<ol>${item.requirements.map(requirement => `<li>${recallValue(requirement)}</li>`).join("")}</ol>` : "<div>无要求</div>"}</div>`).join("");
    return `<section class="unified-recall-candidate"><div>候选 ${index + 1} 标题：${recallValue(candidate.title)}</div><div>卡片 ID：${recallValue(candidate.id)} · 范围：${recallValue(excerpt.scope)}</div>${items}</section>`;
  }).join("");
  return `<div>问题：${recallValue(request.question)}</div>${candidates}`;
}

function renderDialogueRecallBatch(preview: string, index: number): string {
  const parsed = parseDialogueRecallPreview(preview);
  const request = parsed?.batch && Number.isInteger(index) && index >= 0 ? parsed.requests[index] : undefined;
  if (!request) return "";
  const candidates = request.candidates.map((candidate, candidateIndex) =>
    `<section class="unified-recall-candidate"><div>候选 ${candidateIndex + 1} · 来源：${recallValue(candidate.title)} · ID：${recallValue(candidate.id)}</div><div>完整原文（JSON 字符串）：${recallValue(candidate.excerpt)}</div></section>`
  ).join("");
  return `<div>本批问题：${recallValue(request.question)}</div>${candidates}`;
}

/** Batch content is created on expansion and never silently truncated. */
export function renderWorkCardRecallBatch(preview: string, index: number): string {
  const dialogue = renderDialogueRecallBatch(preview, index);
  if (dialogue) return dialogue;
  const parsed = parseRecallPreview(preview);
  return parsed?.batch && Number.isInteger(index) && index >= 0 && index < parsed.requests.length ? renderRecallRequest(parsed.requests[index]) : "";
}

/** Lossless readable projection; raw preview remains the authoritative full request. */
export function renderWorkCardRecallReadable(preview: string): string {
  const dialogue = parseDialogueRecallPreview(preview);
  if (dialogue) {
    const candidates = dialogue.requests.flatMap(request => request.candidates);
    if (dialogue.batch) {
      const batches = dialogue.requests.map((request, index) =>
        `<details class="unified-recall-batch" data-recall-batch="${index}"><summary>第 ${index + 1} 批（${request.candidates.length} 条原文）</summary><div class="unified-recall-batch-content"></div></details>`
      ).join("");
      return `<section class="unified-recall-readable" aria-label="外发内容解析展示"><p class="unified-recall-note">以下为便于阅读的解析展示，不可替代原始预览；字符串以 JSON 表示，控制符和换行显示为转义。</p><div>接收方：0x567 · 接收地址：${recallValue(dialogue.endpoint)}</div><div>当前 Session ${recallValue(dialogue.sessionId)}：共 ${candidates.length} 条原文，${dialogue.requests.length} 批；最多 ${dialogue.requests.length} 次请求，可能产生费用。一次确认授权全部批次。</div>${batches}<p class="unified-recall-raw-label">完整原始预览（逐字保留）</p></section>`;
    }
    const request = dialogue.requests[0];
    const readableCandidates = request.candidates.map((candidate, index) =>
      `<section class="unified-recall-candidate"><div>候选 ${index + 1} · 来源：${recallValue(candidate.title)} · ID：${recallValue(candidate.id)}</div><div>完整原文（JSON 字符串）：${recallValue(candidate.excerpt)}</div></section>`
    ).join("");
    return `<section class="unified-recall-readable" aria-label="外发内容解析展示"><p class="unified-recall-note">以下为便于阅读的解析展示，不可替代原始预览；字符串以 JSON 表示，控制符和换行显示为转义。</p><div>外发范围：当前 Session ${recallValue(dialogue.sessionId)} 的本次问题及全部 ${candidates.length} 条可检索用户/助手原文候选；完整发送，不省略、不截断。</div><div>接收方：0x567 · 接收地址：${recallValue(dialogue.endpoint)}</div><div>本次问题：${recallValue(request.question)}</div>${readableCandidates}<p class="unified-recall-raw-label">完整原始预览（逐字保留）</p></section>`;
  }
  const parsed = parseRecallPreview(preview);
  if (!parsed) return "";
  let content: string;
  if (parsed.batch) {
    const ids = new Set(parsed.requests.flatMap(request => request.candidates.map(candidate => candidate.id)));
    const n = parsed.requests.length;
    content = `<div>共 ${ids.size} 张卡片，${n} 批；最多 ${n} 次请求，可能产生费用。一次确认授权全部批次。</div>` + parsed.requests.map((request, i) => `<details class="unified-recall-batch" data-recall-batch="${i}"><summary>第 ${i + 1} 批（${request.candidates.length} 个候选）</summary><div class="unified-recall-batch-content"></div></details>`).join("");
  } else content = renderRecallRequest(parsed.requests[0]);
  return `<section class="unified-recall-readable" aria-label="外发内容解析展示"><p class="unified-recall-note">以下为便于阅读的解析展示，不可替代原始预览；字符串以 JSON 表示，控制符和换行显示为转义。</p><div>接收地址：${recallValue(parsed.endpoint)}</div>${content}</section><p class="unified-recall-raw-label">完整原始预览（逐字保留）</p>`;
}

/** Opening details is a UI gate, not proof that the contents were read. */
export function isConfirmChoiceAllowed(preview: string, choice: "y" | "n" | "a", detailsOpen: boolean): boolean {
  return choice === "n" || !isWorkCardRecallPreview(preview) || detailsOpen;
}

export function summarizeConfirmPreview(preview: string): ConfirmPreviewView {
  const recall = parseRecallPreview(preview);
  if (recall) {
    const ids = new Set(recall.requests.flatMap(request => request.candidates.map(candidate => candidate.id)));
    const counts = recall.batch ? ` 共 ${ids.size} 张卡片、${recall.requests.length} 批，最多 ${recall.requests.length} 次请求，可能产生费用；一次确认授权全部批次。` : "";
    return { headline: "卡片语义召回：外发确认", targetPath: "", reason: `将外发当前会话问题、卡片标题和要求到 0x567。${counts} 请先展开完整原始预览；展开不代表已阅读。`, detail: preview, compact: true };
  }
  const dialogue = parseDialogueRecallPreview(preview);
  if (dialogue) {
    const candidateCount = dialogue.requests.reduce((count, request) => count + request.candidates.length, 0);
    const batchNotice = dialogue.batch
      ? `共 ${dialogue.requests.length} 批，最多 ${dialogue.requests.length} 次模型请求，可能产生费用；一次确认授权全部批次。`
      : "";
    return {
      headline: "\u5bf9\u8bdd\u539f\u6587\u8bed\u4e49\u56de\u67e5\uff1a\u5916\u53d1\u786e\u8ba4",
      targetPath: "",
      reason: `\u5373\u5c06\u5411 0x567\uff08\u63a5\u6536\u5730\u5740\uff1a${dialogue.endpoint}\uff09\u53d1\u9001\u5f53\u524d Session \u7684\u672c\u6b21\u95ee\u9898\u53ca\u5168\u90e8 ${candidateCount} \u6761\u53ef\u68c0\u7d22\u7528\u6237/\u52a9\u624b\u539f\u6587\u5019\u9009\uff1b${batchNotice}\u5b8c\u6574\u53d1\u9001\uff0c\u4e0d\u7701\u7565\u3001\u4e0d\u622a\u65ad\u3002\u539f\u6587\u53ef\u80fd\u5305\u542b\u654f\u611f\u5185\u5bb9\u3002\u8bf7\u5148\u5c55\u5f00\u5b8c\u6574\u539f\u59cb\u9884\u89c8\uff1b\u5c55\u5f00\u4e0d\u4ee3\u8868\u5df2\u9605\u8bfb\u3002`,
      detail: preview,
      compact: true,
    };
  }

  const text = preview.trim();
  if (!text) {
    return {
      headline: "待确认的工具操作",
      targetPath: "",
      reason: "执行前需要你同意",
      detail: "",
      compact: true,
    };
  }

  const lines = text.split(/\n/).map((l) => l.trim()).filter(Boolean);
  const writeLine = lines.find((l) => l.startsWith("写入文件：") || l.startsWith("修补文件："));
  const contentLine = lines.find((l) => l.startsWith("内容："));
  const policyLine = lines.find((l) => l.startsWith("写入策略："));

  if (writeLine) {
    const path = writeLine.replace(/^写入文件：|^修补文件：/, "").trim();
    const file = basename(path);
    const verb = writeLine.startsWith("修补") ? "修补" : "写入";
    const headline = `${verb} ${file}`;
    const reasonParts: string[] = [];
    if (policyLine) reasonParts.push(humanizePolicyLine(policyLine));
    else reasonParts.push("修改文件前需你确认");
    if (contentLine) {
      const meta = contentLine.replace("内容：", "").trim();
      if (meta) reasonParts.push(meta);
    }
    const normalizedPath = path.replace(/\\/g, "/");
    return {
      headline,
      targetPath: normalizedPath,
      reason: reasonParts.join(" · "),
      detail: text,
      compact: true,
    };
  }

  const runLine = lines.find((l) => l.startsWith("运行命令："));
  const kindLine = lines.find((l) => l.startsWith("命令类型："));
  const fullIdx = lines.findIndex((l) => l === "命令全文：");
  if (runLine) {
    const cmdHead = runLine.replace(/^运行命令：/, "").trim();
    const reasonParts: string[] = [];
    if (kindLine) {
      const kind = kindLine.replace(/^命令类型：/, "").trim();
      if (kind === "danger") reasonParts.push("高风险命令，请仔细核对");
      else if (kind) reasonParts.push(`类型：${kind}`);
    }
    reasonParts.push("执行前需要你同意");
    const detail =
      fullIdx >= 0
        ? lines.slice(fullIdx).join("\n").replace(/^命令全文：\n?/, "")
        : text;
    const long = detail.length > 400 || detail.split("\n").length > 8;
    return {
      headline: cmdHead ? `运行 ${cmdHead.slice(0, 100)}` : "运行命令",
      targetPath: "",
      reason: reasonParts.join(" · "),
      detail,
      compact: long,
    };
  }

  const compact = text.length > 240 || lines.some((l) => l.length > 180);
  const headline = lines[0]?.slice(0, 120) ?? "工具确认";
  return {
    headline,
    targetPath: "",
    reason: "执行前需要你同意",
    detail: text,
    compact,
  };
}

/** Validate the payload only after the event subtype identifies its purpose. */
export function parseWorkRecordReplyExcerpt(preview: string, sessionId: string): string | null {
  if (typeof preview !== "string" || !sessionId) return null;
  const first = preview.indexOf("\n");
  const second = first < 0 ? -1 : preview.indexOf("\n", first + 1);
  if (second < 0) return null;
  try {
    const data: unknown = JSON.parse(preview.slice(second + 1));
    if (!obj(data) || !keys(data, ["purpose", "session_id", "item_id", "requirement_id", "run_id", "assistant_reply_excerpt", "source_availability", "sources", "result_status", "verification_status"])
        || data.purpose !== "assistant_reply_excerpt" || data.session_id !== sessionId
        || ![data.item_id, data.requirement_id, data.run_id].every(value => typeof value === "string" && value.length > 0)
        || typeof data.assistant_reply_excerpt !== "string" || !data.assistant_reply_excerpt.length
        || !Array.isArray(data.sources) || !((data.source_availability === "unavailable" && data.sources.length === 0)
          || (data.source_availability === "available" && data.sources.length > 0))
        || data.result_status !== "unknown" || data.verification_status !== "not_verified") return null;
    return data.assistant_reply_excerpt;
  } catch { return null; }
}

export function renderConfirmCardHtml(
  preview: string,
  actionsHtml: string,
  opts?: { resolvedStatus?: string; confirmSubtype?: "work_record_reply_excerpt" | "work_record_auto_candidate" | "project_worker_requirement_verification"; sessionId?: string; workRecordCandidate?: unknown; projectWorkerVerification?: unknown },
): string {
  const view = summarizeConfirmPreview(preview);
  if (opts?.resolvedStatus) {
    return `<div class="unified-confirm resolved is-compact is-status-chip" role="status">
      <span class="unified-confirm-status-dot" aria-hidden="true"></span>
      <span class="unified-confirm-status-text">${escapeHtml(opts.resolvedStatus)}</span>
      <span class="unified-confirm-status-hint">${escapeHtml(view.headline)}</span>
    </div>`;
  }

  if (opts?.confirmSubtype === "project_worker_requirement_verification") {
    const verification = parseProjectWorkerVerification(opts.projectWorkerVerification, opts.sessionId || "");
    const hasPreview = typeof preview === "string" && preview.trim().length > 0;
    const disclosure = '<p class="unified-confirm-reason">本次仅运行你授权的单个 pytest 目标；结果仅记录为用户授权目标测试的自报告测试结果，不自动标记整个要求完成。单个目标的自报告结果不证明整体需求已满足。</p>'
      + '<p class="unified-confirm-reason">同一操作系统账户下的恶意项目代码可伪造测试报告；此流程不能防范该威胁。pytest、插件及 conftest 均可执行本机项目代码。</p>';
    const fields = verification === null ? ""
      : '<strong>项目</strong><pre class="unified-confirm-preview"><span>' + escapeHtml(verification.project_name) + '</span></pre>'
        + '<strong>要求</strong><pre class="unified-confirm-preview"><span>' + escapeHtml(verification.requirement_text) + '</span></pre>'
        + '<strong>授权 pytest 目标</strong><pre class="unified-confirm-preview"><span>' + escapeHtml(verification.pytest_target) + '</span></pre>';
    const previewBlock = !hasPreview
      ? '<p role="alert">后端执行预览缺失，暂不可批准；可以拒绝。</p>'
      : '<details class="unified-confirm-details"><summary class="unified-confirm-details-toggle">查看后端执行预览</summary><pre class="unified-confirm-preview"><span>' + escapeHtml(preview) + '</span></pre></details>';
    const body = verification === null
      ? '<p role="alert">项目验收请求字段或会话绑定不完整，暂不可批准；可以拒绝。</p>'
      : disclosure + fields + previewBlock;
    return '<div class="unified-surface unified-confirm is-compact-pending" data-project-worker-verification role="region" aria-label="单目标测试授权确认"><div class="unified-confirm-kicker">执行前需要确认</div><div class="unified-confirm-headline">运行单目标测试</div>' + body + '<div class="unified-confirm-actions">' + actionsHtml + '</div></div>';
  }  if (opts?.confirmSubtype === "work_record_auto_candidate") {
    const candidate = parseWorkRecordAutoCandidate(opts.workRecordCandidate, opts.sessionId || "");
    const body = candidate === null
      ? '<p role="alert">候选字段或会话绑定无法核对，暂不可确认；可以拒绝。</p>'
      : '<p class="unified-confirm-reason">以下为完整拟保存内容；结果未知、未核验，不代表要求完成。工具回执与助理摘录不保存。</p>'
        + (candidate.operation === "append_requirement"
          ? '<strong>追加到已有事项</strong><pre class="unified-confirm-preview"><span>' + escapeHtml(candidate.item_id) + '</span></pre>' : '')
        + '<strong>标题</strong><pre class="unified-confirm-preview"><span>' + escapeHtml(candidate.title) + '</span></pre>'
        + '<strong>要求</strong><pre class="unified-confirm-preview"><span>' + escapeHtml(candidate.requirement) + '</span></pre>'
        + '<strong>范围</strong><pre class="unified-confirm-preview"><span>' + escapeHtml(JSON.stringify(candidate.scope, null, 2)) + '</span></pre>'
        + '<details class="unified-confirm-details"><summary class="unified-confirm-details-toggle">查看完整确认字段</summary><pre class="unified-confirm-preview"><span>' + escapeHtml(preview) + '</span></pre></details>';
    return '<div class="unified-surface unified-confirm is-compact-pending" data-work-record-auto-candidate role="region" aria-label="自动识别候选保存确认"><div class="unified-confirm-kicker">需要确认</div><div class="unified-confirm-headline">保存工作候选</div>' + body + '<div class="unified-confirm-actions">' + actionsHtml + '</div></div>';
  }

  if (opts?.confirmSubtype === "work_record_reply_excerpt") {
    const excerpt = parseWorkRecordReplyExcerpt(preview, opts.sessionId || "");
    const body = excerpt === null
      ? '<p role="alert">摘录或会话绑定无法核对，暂不可确认；可以拒绝。</p>'
      : '<p class="unified-confirm-reason">以下为拟保存的完整摘录；未核验，不代表要求完成。</p><pre class="unified-confirm-preview"><span>' + escapeHtml(excerpt) + '</span></pre><details class="unified-confirm-details"><summary class="unified-confirm-details-toggle">查看完整确认字段</summary><pre class="unified-confirm-preview">' + escapeHtml(preview) + '</pre></details>';
    return '<div class="unified-surface unified-confirm is-compact-pending" data-work-record-reply-excerpt role="region" aria-label="回复摘录保存确认"><div class="unified-confirm-kicker">需要确认</div><div class="unified-confirm-headline">保存回复摘录</div>' + body + '<div class="unified-confirm-actions">' + actionsHtml + '</div></div>';
  }

  const readable = renderWorkCardRecallReadable(preview);
  const detailBlock = view.compact
    ? `<details class="unified-confirm-details">
        <summary class="unified-confirm-details-toggle">查看详情</summary>
        ${readable ? `<div class="unified-recall-scroll">${readable}<pre class="unified-confirm-preview">${escapeHtml(view.detail)}</pre></div>` : `<pre class="unified-confirm-preview">${escapeHtml(view.detail)}</pre>`}
      </details>`
    : `<pre class="unified-confirm-preview">${escapeHtml(view.detail)}</pre>`;

  const reasonBlock = view.reason
    ? `<p class="unified-confirm-reason">${escapeHtml(view.reason)}</p>`
    : "";

  const sandboxHint =
    view.targetPath.includes(".worktrees/sandbox")
      ? `<p class="unified-confirm-scope-hint">实验副本（sandbox）</p>`
      : "";

  const pathBlock = view.targetPath
    ? `<p class="unified-confirm-target-path" title="${escapeHtml(view.targetPath)}"><code>${escapeHtml(
        view.targetPath,
      )}</code></p>`
    : "";

  return `<div class="unified-surface unified-confirm is-compact-pending" ${isWorkCardRecallPreview(preview) ? "data-work-card-recall" : ""} role="region" aria-label="工具确认">
    <div class="unified-confirm-kicker">需要确认</div>
    <div class="unified-confirm-headline">${escapeHtml(view.headline)}</div>
    ${pathBlock}
    ${sandboxHint}
    ${reasonBlock}
    ${detailBlock}
    <div class="unified-confirm-actions">${actionsHtml}</div>
  </div>`;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
