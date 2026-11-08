import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  isConfirmChoiceAllowed,
  renderConfirmCardHtml,
  summarizeConfirmPreview,
} from "../src/shells/unified/confirm-preview.ts";

const batchAuthorizationPreview = (overrides: Record<string, unknown> = {}) => [
  "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM",
  JSON.stringify({
    schema_version: 1,
    purpose: "project_worker_batch_authorization",
    endpoint: { provider: "0x567", model_id: "0x567-flash", model: "gpt-6-luna", url: "https://api-cdn.0x567.com/v1/chat/completions" },
    projects: [{
      project_id: "project-alpha",
      task_id: "task-1",
      task: "更新界面",
      project_root: "workspace/project-alpha",
      read_targets: ["src/Panel.tsx"],
      write_targets: ["src/Panel.tsx"],
      generated_content_write: "Model-generated content may be written only to write_targets through ToolExecutor/write_policy and existing lease/source-snapshot/readback checks.",
    }],
    disclosed_data_categories: [
      "listed_worker_tasks",
      "worker_system_and_developer_instructions",
      "contents_of_listed_read_targets_only",
      "worker_multi_turn_model_and_tool_exchange_with_named_endpoint",
      "model_generated_content_for_listed_write_targets",
    ],
    not_disclosed: ["parent_conversation", "memory", "unlisted_project_data"],
    authorization: {
      scope: "per_project_per_worker_only",
      expires_in_seconds: 3600,
      requests_per_worker: "bounded_by_existing_worker_request_limit",
      effects_on_cancel: "blocks_future_posts_and_write_starts; cannot recall already-sent request or undo an already-started write",
    },
    ...overrides,
  }, null, 2),
].join("\n");

test("run_command confirm shows the real command, not a char count", () => {
  const cmd = "python3 -m unittest -v test_todo.py && rm -f .test-todos.json && " + "x".repeat(200);
  const preview = [
    `运行命令：${cmd.slice(0, 120)}…（共 ${cmd.length} 字符 · 1 行）`,
    "工作目录：workspace/todo",
    "命令类型：other",
    "命令全文：",
    cmd,
  ].join("\n");
  const view = summarizeConfirmPreview(preview);
  assert.match(view.headline, /运行 python3 -m unittest/);
  assert.equal(view.detail, cmd);
  assert.ok(!/^运行命令：\d+ 字符/.test(view.headline));
});

test("write confirm shows full path on the card", () => {
  const preview = [
    "写入文件：.worktrees/sandbox/desktop/src/shells/unified/image-zoom.ts",
    "内容：2844 字符 · 65 行",
    "写入策略：confirm:no_project_binding",
  ].join("\n");
  const view = summarizeConfirmPreview(preview);
  assert.equal(view.headline, "写入 image-zoom.ts");
  assert.equal(
    view.targetPath,
    ".worktrees/sandbox/desktop/src/shells/unified/image-zoom.ts",
  );
  assert.match(view.reason, /未绑定项目/);
});

test("danger run_command still labels the risk", () => {
  const preview = ["运行命令：rm -rf /", "命令类型：danger", "命令全文：", "rm -rf /"].join("\n");
  const view = summarizeConfirmPreview(preview);
  assert.match(view.reason, /高风险/);
  assert.equal(view.detail, "rm -rf /");
});

test("project worker unlinked execution confirm explains authorization and preserves raw preview", () => {
  const preview = [
    "PROJECT_WORKER_UNLINKED_EXECUTION_CONFIRM",
    JSON.stringify({
      reason: "用户要求并行处理",
      tasks: [{ project_id: "project-alpha", task: "更新页面", read_targets: ["src/**"], write_targets: ["src/page.ts"] }],
      record_access: "none; do not read or write work-record sidecars",
      record_effect: "unlinked; no task-to-requirement binding or lifecycle tracking",
      execution_gates: [
        "each worker project scope is confirmed separately",
        "each exact outbound model request is confirmed separately",
        "each project file write is confirmed separately",
      ],
      acceptance: "not_run",
    }, null, 2),
  ].join("\n");

  const view = summarizeConfirmPreview(preview);
  assert.match(view.headline, /不关联工作记录/);
  assert.match(view.reason, /不会读取、创建或绑定工作记录/);
  assert.match(view.reason, /worker 生命周期/);
  assert.match(view.reason, /项目范围、每次外发和文件写入仍需分别确认/);
  assert.equal(view.detail, preview);
  assert.equal(view.compact, true);
});

test("current dialogue work-record batch confirm explains independent record scope and later gates", () => {
  const preview = [
    "CURRENT_DIALOGUE_WORK_RECORD_BATCH_CONFIRM",
    JSON.stringify({
      reason: "按用户请求拆分项目任务",
      original_user_request: "分别更新两个项目",
      current_conversation_id: "dialogue-42",
      record_scope: "independent WorkRecord item in the current dialogue only",
      tasks: [{ project_id: "project-alpha", task: "更新页面", read_targets: ["src/**"], write_targets: ["src/page.ts"] }],
      project_assignment: "user-confirmed descriptive ownership; not a file permission grant",
      other_project_sessions: "no WorkRecordStore is read or written by this creation path",
      record_effect: "preserve original request plus one requirement per worker in the current dialogue",
      failure_boundary: "if the complete record batch cannot be persisted, no worker is dispatched",
      execution_gates: [
        "each worker project scope is confirmed separately",
        "each exact outbound model request is confirmed separately",
        "each project file write is confirmed separately",
      ],
      acceptance: "not_run",
    }, null, 2),
  ].join("\n");

  const view = summarizeConfirmPreview(preview);
  assert.match(view.headline, /当前会话.*独立.*记录/);
  assert.match(view.reason, /只在当前会话创建独立工作记录/);
  assert.match(view.reason, /原始请求及每个 worker 的任务和项目归属/);
  assert.match(view.reason, /项目归属仅作说明，不会授予文件权限/);
  assert.match(view.reason, /项目范围、每次外发和文件写入仍需分别确认/);
  assert.equal(view.detail, preview);
  assert.equal(view.compact, true);
});


test("batch authorization confirmation renders every scope and disclosure before approval", () => {
  const preview = batchAuthorizationPreview();
  const html = renderConfirmCardHtml(preview, '<button data-confirm="y">批准</button><button data-confirm="n">取消</button>');
  assert.match(html, /集中授权/);
  assert.match(html, /project-alpha/);
  assert.match(html, /workspace\/project-alpha/);
  assert.match(html, /src\/Panel\.tsx/);
  assert.match(html, /0x567/);
  assert.match(html, /gpt-6-luna/);
  assert.match(html, /0x567-flash/);
  assert.match(html, /https:\/\/api-cdn\.0x567\.com\/v1\/chat\/completions/);
  assert.match(html, /listed_worker_tasks/);
  assert.match(html, /worker_system_and_developer_instructions/);
  assert.match(html, /contents_of_listed_read_targets_only/);
  assert.match(html, /worker_multi_turn_model_and_tool_exchange_with_named_endpoint/);
  assert.match(html, /model_generated_content_for_listed_write_targets/);
  assert.match(html, /parent_conversation/);
  assert.match(html, /memory/);
  assert.match(html, /unlisted_project_data/);
  assert.match(html, /3600/);
  assert.match(html, /cannot recall already-sent request/);
  assert.match(html, /Model-generated content may be written only/);
  assert.doesNotMatch(html, /<details/);
  assert.match(html, /data-project-worker-batch-authorization/);
});

test("batch authorization displays the exact registry model endpoint and effective expiry", () => {
  const parts = batchAuthorizationPreview().split("\n");
  const payload = JSON.parse(parts.slice(1).join("\n"));
  payload.endpoint = { provider: "0x567", model_id: "0x567-next", model: "gpt-next", url: "https://api-cdn.0x567.com:8443/custom/v1/chat/completions" };
  payload.authorization.expires_in_seconds = 1800;
  const html = renderConfirmCardHtml("PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n" + JSON.stringify(payload), "");
  assert.match(html, /0x567-next/);
  assert.match(html, /gpt-next/);
  assert.match(html, /api-cdn\.0x567\.com:8443\/custom\/v1\/chat\/completions/);
  assert.match(html, /有效期 1800 秒/);
});

test("batch authorization preview escapes untrusted task and target text as inert HTML", () => {
  const parts = batchAuthorizationPreview().split("\n");
  const payload = JSON.parse(parts.slice(1).join("\n"));
  payload.projects[0].task = '<img src=x onerror="alert(1)">';
  payload.projects[0].read_targets = ['src/<svg onload="alert(2)">.tsx'];
  const preview = "PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n" + JSON.stringify(payload);
  const html = renderConfirmCardHtml(preview, "");
  assert.doesNotMatch(html, /<img src=x/);
  assert.doesNotMatch(html, /<svg onload=/);
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
  assert.match(html, /&lt;svg onload=&quot;alert\(2\)&quot;&gt;/);
});

test("malformed and unknown batch authorization previews cannot be approved", () => {
  const malformed = 'PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n{"schema_version":1}';
  const unknownVersion = batchAuthorizationPreview({ schema_version: 2 });
  const unknownField = batchAuthorizationPreview({ extra_permission: true });
  const changedEndpoint = batchAuthorizationPreview({ endpoint: { provider: "0x567", model_id: "0x567-flash", model: "gpt-6-luna", url: "https://api-cdn.0x567.com/other" } });
  const traversalTarget = batchAuthorizationPreview({ projects: [{ project_id: "project-alpha", task_id: "task-1", task: "更新界面", project_root: "workspace/project-alpha", read_targets: ["../secret.txt"], write_targets: ["src/Panel.tsx"], generated_content_write: "Model-generated content may be written only to write_targets through ToolExecutor/write_policy and existing lease/source-snapshot/readback checks." }] });
  const changedExpiry = batchAuthorizationPreview({ authorization: { scope: "per_project_per_worker_only", expires_in_seconds: 3601, requests_per_worker: "bounded_by_existing_worker_request_limit", effects_on_cancel: "blocks_future_posts_and_write_starts; cannot recall already-sent request or undo an already-started write" } });
  for (const preview of [malformed, unknownVersion, unknownField, changedEndpoint, traversalTarget, changedExpiry]) {
    const html = renderConfirmCardHtml(preview, '<button data-confirm="y">批准</button><button data-confirm="n">拒绝</button>');
    assert.match(html, /暂不可批准/);
    assert.match(html, /data-confirm="y"[^>]*disabled/);
    assert.equal(isConfirmChoiceAllowed(preview, "y", true), false);
    assert.equal(isConfirmChoiceAllowed(preview, "a", true), false);
    assert.equal(isConfirmChoiceAllowed(preview, "n", true), true);
  }
});


test("actual ProjectWorkerHost authorization preview fixture stays readable and approvable", () => {
  const fixtureText = readFileSync(new URL("./fixtures/project-worker-batch-authorization-preview.txt", import.meta.url), "utf8");
  // Git/Windows may check out this fixture with CRLF; the backend WebSocket preview is LF.
  const preview = fixtureText.replace(/\r\n/g, "\n");
  const html = renderConfirmCardHtml(preview, '<button data-confirm="y">批准</button><button data-confirm="n">拒绝</button>');
  assert.match(preview, /^PROJECT_WORKER_BATCH_AUTHORIZATION_CONFIRM\n/);
  assert.match(html, /project-alpha/);
  assert.match(html, /project-beta/);
  assert.match(html, /Update alpha/);
  assert.match(html, /Update beta/);
  assert.match(html, /0x567-flash/);
  assert.match(html, /https:\/\/api-cdn\.0x567\.com\/v1\/chat\/completions/);
  assert.match(html, /3600/);
  assert.doesNotMatch(html, /data-project-worker-batch-confirm[^>]*is-invalid/);
  assert.doesNotMatch(html, /<details/);
  assert.match(html, /data-confirm="y"/);
  assert.equal(isConfirmChoiceAllowed(preview, "y", false), true);
});
