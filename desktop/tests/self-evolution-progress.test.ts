import assert from "node:assert/strict";
import test from "node:test";

import {
  SelfEvolutionProgress,
  SelfEvolutionRecovery,
  renderSelfEvolutionProgress,
  type SelfEvolutionEvent,
  type SelfEvolutionSnapshot,
} from "../src/shells/unified/self-evolution-progress.ts";

const identity = { session_id: "session-a", task_id: "task-a", attempt_id: "attempt-1", run_id: "run-1", turn_epoch: 7 } as const;
const snapshot: SelfEvolutionSnapshot = {
  ...identity,
  phase: "observing",
  snapshot_revision: 1,
  sequence: 0,
  status: "active",
  findings: [],
  verification: [],
  preview_handle: null,
};
function event(overrides: Partial<SelfEvolutionEvent> = {}): SelfEvolutionEvent {
  return {
    type: "self_evolution.update", ...identity, sequence: 1, phase: "observing",
    kind: "phase_changed", fact_codes: ["observation_recorded"], snapshot_revision: 2,
    evidence_refs: ["evidence-observation"], ...overrides,
  };
}

test("restores only a session-bound snapshot and rejects mismatched or malformed event identities", () => {
  const progress = new SelfEvolutionProgress();
  assert.equal(progress.restore(snapshot, { session_id: "other-session" }), false);
  assert.equal(progress.current(), null);
  assert.equal(progress.restore(snapshot, { session_id: identity.session_id }), true);
  assert.equal(progress.accept(event({ task_id: "other-task" })), null);
  assert.equal(progress.accept(event({ attempt_id: "old-attempt" })), null);
  assert.equal(progress.accept({ ...event(), owner_path: "C:/secret" }), null);
});

test("deduplicates sequence and revision without repeating a spoken milestone", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  const accepted = progress.accept(event());
  assert.ok(accepted);
  assert.equal(accepted.milestones.length, 1);
  assert.equal(progress.accept(event()), null);
  assert.equal(progress.accept(event({ sequence: 2, snapshot_revision: 2 })), null);
  assert.equal(progress.current()?.milestones.length, 1);
});

test("requires a newer revision and sequence, but permits checks to return to editing", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore({ ...snapshot, phase: "checking", snapshot_revision: 10, sequence: 4 }, { session_id: identity.session_id });
  const returned = progress.accept(event({ sequence: 5, phase: "editing", snapshot_revision: 11, fact_codes: ["edit_started"], evidence_refs: ["change-evidence"] }));
  assert.equal(returned?.phase, "editing");
});

test("an attempt switch requires a trusted restored snapshot; previous-attempt events stay rejected", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  const next = { ...snapshot, attempt_id: "attempt-2", run_id: "run-2", snapshot_revision: 4, sequence: 0, phase: "preparing" as const };
  assert.equal(progress.restore(next, { session_id: identity.session_id }), true);
  assert.equal(progress.accept(event({ sequence: 9, snapshot_revision: 9 })), null);
  assert.ok(progress.accept(event({ ...identity, attempt_id: "attempt-2", run_id: "run-2", sequence: 1, snapshot_revision: 5 })));
});

test("unknown phase, status, kind, or fact fails closed into a confirmation-needed state", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  const value = progress.accept(event({ phase: "new-phase" as SelfEvolutionEvent["phase"], sequence: 1, snapshot_revision: 2 }));
  assert.equal(value?.phase, "unknown");
  assert.equal(value?.status, "unknown");
  assert.equal(value?.milestones.at(-1)?.text, "当前状态待确认，我先不做判断。");
});

test("node voice is evidence-bounded; success claims need host evidence refs", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  assert.equal(progress.accept(event({ fact_codes: ["check_passed"], evidence_refs: [] })), null);
  const blocked = progress.accept(event({ kind: "verification_completed", phase: "checking", fact_codes: ["check_failed"], evidence_refs: ["check-42"] }));
  assert.equal(blocked?.milestones.at(-1)?.text, "检查没有通过，我会先看清问题再决定下一步。");
  assert.doesNotMatch(blocked?.milestones.at(-1)?.text ?? "", /通过了/);
});

test("fast adjacent milestones merge without losing observation and edit facts", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  const result = progress.accept(event({
    fact_codes: ["observation_recorded", "edit_started"],
    evidence_refs: ["observation-1", "change-1"],
  }));
  assert.equal(result?.milestones.length, 1);
  assert.match(result?.milestones[0].text ?? "", /找到现状/);
  assert.match(result?.milestones[0].text ?? "", /开始改造/);
});

test("renders an accessible, non-overlay status projection and escapes all snapshot text", () => {
  const html = renderSelfEvolutionProgress({
    ...snapshot, phase: "awaiting_review", status: "completed", findings: ["<img src=x onerror=alert(1)>"] ,
    verification: [{ label: "页面检查", result: "not_run", evidence_ref: null }], preview_handle: null,
  });
  assert.match(html, /role="status"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /等待你查看/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
  assert.doesNotMatch(html, /preview_handle|href=|https?:\/\//i);
});


test("generates concise active-node speech once for task acceptance and check start", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  const accepted = progress.accept(event({ kind: "task_started", fact_codes: ["task_accepted"], evidence_refs: [], sequence: 1, snapshot_revision: 2 }));
  assert.equal(accepted?.milestones[0].text, "我接下这项改造，先从确认现状开始。");
  const checking = progress.accept(event({ kind: "phase_changed", phase: "checking", fact_codes: ["check_started"], evidence_refs: ["check-plan"], sequence: 2, snapshot_revision: 3 }));
  assert.equal(checking?.milestones[1].text, "改动先告一段落，我现在开始做针对性检查。");
  assert.equal(progress.accept(event({ kind: "phase_changed", phase: "checking", fact_codes: ["check_started"], evidence_refs: ["check-plan"], sequence: 3, snapshot_revision: 4 }))?.milestones.length, 2);
});

test("keeps milestone wording attached to the task and safely renders it without overlay markup", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  progress.accept(event({ fact_codes: ["observation_recorded"], evidence_refs: ["observation-1"] }));
  const html = renderSelfEvolutionProgress(progress.current()!, progress.current()!.milestones);
  assert.match(html, /我找到现状依据了/);
  assert.match(html, /self-evolution-progress__milestones/);
  assert.doesNotMatch(html, /position:\s*fixed|<button|<a\s/);
});

test("does not accept an attempt switch at an unchanged task snapshot revision", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  assert.equal(progress.restore({ ...snapshot, attempt_id: "attempt-2", run_id: "run-2" }, { session_id: identity.session_id }), false);
});

test("same-revision restore is idempotent only and cannot roll back an accepted event watermark", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  progress.accept(event({ sequence: 2, snapshot_revision: 2 }));
  assert.equal(progress.restore({ ...snapshot, sequence: 1 }, { session_id: identity.session_id }), false);
  assert.equal(progress.current()?.sequence, 2);
  assert.equal(progress.restore({ ...snapshot, phase: "editing", sequence: 2 }, { session_id: identity.session_id }), false);
  assert.equal(progress.current()?.phase, "observing");
  assert.equal(progress.restore({ ...snapshot, phase: "observing", snapshot_revision: 2, sequence: 2 }, { session_id: identity.session_id }), true);
});

test("restore rejects run or turn identity changes within the same attempt", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  assert.equal(progress.restore({ ...snapshot, run_id: "substituted-run", snapshot_revision: 3 }, { session_id: identity.session_id }), false);
  assert.equal(progress.restore({ ...snapshot, turn_epoch: 8, snapshot_revision: 3 }, { session_id: identity.session_id }), false);
  assert.equal(progress.current()?.run_id, identity.run_id);
  assert.equal(progress.current()?.turn_epoch, identity.turn_epoch);
});

test("same task ids in different sessions never inherit milestones or spoken deduplication", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  progress.accept(event({ fact_codes: ["observation_recorded"], evidence_refs: ["session-a-observation"] }));

  const otherSession = "session-b";
  const secondSnapshot = { ...snapshot, session_id: otherSession };
  assert.equal(progress.restore(secondSnapshot, { session_id: otherSession }), true);
  assert.equal(progress.current()?.milestones.length, 0);
  const secondEvent = { ...event({ fact_codes: ["observation_recorded"], evidence_refs: ["session-b-observation"] }), session_id: otherSession };
  assert.equal(progress.accept(secondEvent)?.milestones.length, 1);
});

test("refuses raw backend snapshots and any projection containing a candidate private root", () => {
  const progress = new SelfEvolutionProgress();
  const nestedBackendSnapshot = {
    identity: { session_id: identity.session_id, task_id: identity.task_id, attempt_id: identity.attempt_id, run_id: identity.run_id, turn_epoch: identity.turn_epoch },
    phase: "observing",
    snapshot_revision: 1,
    candidate_digest: "digest-private",
    discoveries: [],
    evidence: [],
    candidate_root: "C:/private/candidate",
  };
  assert.equal(progress.restore(nestedBackendSnapshot, { session_id: identity.session_id }), false);
  assert.equal(progress.current(), null);
  assert.equal(progress.restore({ ...snapshot, candidate_root: "C:/private/candidate" } as SelfEvolutionSnapshot, { session_id: identity.session_id }), false);
  assert.equal(renderSelfEvolutionProgress({ ...snapshot, candidate_root: "C:/private/candidate" } as SelfEvolutionSnapshot), "");
});

test("rejects snapshots whose public status contradicts their authoritative phase", () => {
  const progress = new SelfEvolutionProgress();
  assert.equal(progress.restore({ ...snapshot, phase: "awaiting_review", status: "active" }, { session_id: identity.session_id }), false);
  assert.equal(progress.current(), null);
});

test("recovery request coordination rejects delayed A after B and restores from memory without storage", () => {
  const recovery = new SelfEvolutionRecovery();
  recovery.remember("session-a", "task-a");
  assert.deepEqual(recovery.hint("session-a", () => { throw new Error("storage disabled"); }), { session_id: "session-a", task_id: "task-a" });
  recovery.requested("session-a", "task-a", "request-a");
  recovery.requested("session-a", "task-b", "request-b");
  assert.equal(recovery.accepts("session-a", "task-a", "request-a"), false);
  assert.equal(recovery.accepts("session-a", "task-b", "request-b"), true);
  recovery.remember("session-a", "task-b");
  recovery.disconnected();
  assert.deepEqual(recovery.hint("session-a", () => null), { session_id: "session-a", task_id: "task-b" });
  assert.equal(recovery.hint("session-b", () => null), null);
  assert.equal(recovery.accepts("session-a", "task-b", "request-b"), false);
});

test("recovery may discard previously spoken milestones without reannouncing them", () => {
  const progress = new SelfEvolutionProgress();
  progress.restore(snapshot, { session_id: identity.session_id });
  progress.accept(event());
  const recovered = { ...snapshot, sequence: 1, snapshot_revision: 2 };
  assert.equal(progress.restore(recovered, { session_id: identity.session_id }, { clearMilestones: true }), true);
  assert.deepEqual(progress.current()?.milestones, []);
  assert.equal(progress.accept(event({sequence: 2, snapshot_revision: 3}))?.milestones.length, 0);
});

test("review delivery exposes only host-provided findings and verification evidence, with honest unavailable preview and merge states", () => {
  const html = renderSelfEvolutionProgress({
    ...snapshot, phase: "awaiting_review", status: "completed",
    findings: ["搜索区域会挤压结果列表"],
    verification: [
      { label: "定向单测", result: "passed", evidence_ref: "evidence-test-1" },
      { label: "真实页面预览", result: "not_run", evidence_ref: null },
    ],
    preview_handle: "opaque-preview-handle",
  });
  assert.match(html, /待审阅交付/);
  assert.match(html, /宿主观察摘要/);
  assert.match(html, /搜索区域会挤压结果列表/);
  assert.match(html, /文件级变更清单尚未由宿主提供/);
  assert.match(html, /定向单测/);
  assert.match(html, /evidence-test-1/);
  assert.match(html, /读取日志或证据正文的安全入口尚未接入/);
  assert.match(html, /真实可用预览尚未接入/);
  assert.match(html, /原生确认与受控合入当前不可用/);
  assert.doesNotMatch(html, /<button|<a\\s|href=|onclick=/i);
});

test("blocked or synthetic review snapshots never render preview, confirmation, or merge actions", () => {
  const html = renderSelfEvolutionProgress({
    ...snapshot, phase: "blocked", status: "blocked", preview_handle: "looks-real",
    findings: ["候选区检查未完成"], verification: [],
  });
  assert.match(html, /当前任务处于阻塞状态/);
  assert.match(html, /真实可用预览尚未接入/);
  assert.match(html, /原生确认与受控合入当前不可用/);
  assert.doesNotMatch(html, /data-self-evolution-(?:preview|confirm|merge)|<button|<a\\s/i);
});




test("reviewed candidate content is bound to the pending session, attempt and snapshot revision", async () => {
  const { SelfEvolutionDiffReview, renderSelfEvolutionDiffReview, renderSelfEvolutionReview } = await import("../src/shells/unified/self-evolution-progress.ts");
  const review = new SelfEvolutionDiffReview();
  const base = { ...identity, snapshot_revision: 5, request_id: "00000000-0000-4000-8000-000000000001" };
  const event = { type: "self_evolution.review.result", ...base, candidate_digest: "a".repeat(64),
    truncated: true, content_complete: false,
    files: [{ path: "<src>.py", change: "modified", before_content_status: "text", after_content_status: "text", diff: "-old\n+<script>" },
      { path: "large.dat", change: "added", before_content_status: "absent", after_content_status: "over_text_limit" }] };
  review.requested(base);
  assert.equal(review.accept({ ...event, session_id: "other" }, base), false);
  assert.equal(review.accept({ ...event, attempt_id: "old" }, base), false);
  assert.equal(review.accept({ ...event, request_id: "other" }, base), false);
  assert.equal(review.accept(event, base), true);
  const html = renderSelfEvolutionDiffReview(review.current(base));
  const mounted = renderSelfEvolutionReview({ ...snapshot, ...identity, snapshot_revision: 5 }, review.current(base));
  assert.match(mounted, /&lt;src&gt;\.py/);
  assert.match(mounted, /\+&lt;script&gt;/);
  assert.match(html, /&lt;src&gt;\.py/);
  assert.match(html, /\+&lt;script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /截断/);
  assert.match(html, /未提供|无法显示/);
  assert.equal(review.current({ ...base, snapshot_revision: 6 }), null);
});

test("selected apply appears only for host-disclosed eligible paths and defaults to no selection", async () => {
  const { SelfEvolutionDiffReview, renderSelfEvolutionApplyControls } = await import("../src/shells/unified/self-evolution-progress.ts");
  const review = new SelfEvolutionDiffReview();
  const base = { ...identity, snapshot_revision: 5, request_id: "review-1" };
  review.requested(base);
  const payload = { type: "self_evolution.review.result", ...base, candidate_digest: "a".repeat(64), truncated: false,
    content_complete: true, apply_available: true, apply_eligible_paths: ["src/one.ts"],
    files: [{ path: "src/one.ts", change: "modified", before_content_status: "text", after_content_status: "text", diff: "-a\\n+b" },
      { path: "src/two.ts", change: "modified", before_content_status: "text", after_content_status: "text", diff: "-c\\n+d" }] };
  assert.equal(review.accept(payload, base), true);
  const html = renderSelfEvolutionApplyControls(review.current(base), null);
  assert.match(html, /data-self-evolution-apply-path="src\/one\.ts"/);
  assert.doesNotMatch(html, /data-self-evolution-apply-path="src\/two\.ts"/);
  assert.match(html, /type="checkbox"/);
  assert.doesNotMatch(html, /checked/);
  assert.match(html, /实验区/);
  assert.match(html, /原生确认/);
});

test("apply capability fails closed when eligible paths are not a disclosed reviewed subset", async () => {
  const { SelfEvolutionDiffReview } = await import("../src/shells/unified/self-evolution-progress.ts");
  const review = new SelfEvolutionDiffReview();
  const base = { ...identity, snapshot_revision: 5, request_id: "review-1" };
  const payload = { type: "self_evolution.review.result", ...base, candidate_digest: "a".repeat(64), truncated: false,
    content_complete: true, apply_available: true, apply_eligible_paths: ["src/not-reviewed.ts"],
    files: [{ path: "src/one.ts", change: "modified", before_content_status: "text", after_content_status: "text" }] };
  review.requested(base);
  assert.equal(review.accept(payload, base), false);
  assert.equal(review.current(base), null);
});

test("selected apply request carries only opaque task identity, request id, and explicit reviewed subset", async () => {
  const { SelfEvolutionDiffReview, SelfEvolutionApply } = await import("../src/shells/unified/self-evolution-progress.ts");
  const review = new SelfEvolutionDiffReview();
  const base = { ...identity, snapshot_revision: 5, request_id: "review-1" };
  review.requested(base);
  review.accept({ type: "self_evolution.review.result", ...base, candidate_digest: "a".repeat(64), truncated: false,
    content_complete: true, apply_available: true, apply_eligible_paths: ["src/one.ts", "src/two.ts"],
    files: ["src/one.ts", "src/two.ts"].map(path => ({ path, change: "modified", before_content_status: "text", after_content_status: "text" })) }, base);
  const apply = new SelfEvolutionApply();
  const request = apply.requested(review.current(base)!, ["src/two.ts"], "apply-1");
  assert.deepEqual(request, { type: "self_evolution.apply.request", task_id: identity.task_id, attempt_id: identity.attempt_id,
    request_id: "apply-1", selected_paths: ["src/two.ts"] });
  assert.equal(apply.requested(review.current(base)!, ["src/not-reviewed.ts"], "apply-2"), null);
});

test("apply result, unavailable and cancellation are bound to the exact request identity", async () => {
  const { SelfEvolutionApply } = await import("../src/shells/unified/self-evolution-progress.ts");
  const apply = new SelfEvolutionApply();
  const base = { ...identity, snapshot_revision: 5, request_id: "apply-1" };
  apply.begin(base);
  const event = { type: "self_evolution.apply.result", ...base, status: "applied" };
  assert.equal(apply.accept({ ...event, task_id: "foreign-task" }, base), false);
  assert.equal(apply.accept({ ...event, request_id: "late-request" }, base), false);
  assert.equal(apply.accept({ ...event, turn_epoch: 8 }, base), false);
  assert.equal(apply.accept(event, { ...base, turn_epoch: 8 }), false);
  assert.equal(apply.current(), null);
  assert.equal(apply.accept(event, base), true);
  assert.equal(apply.current()?.outcome, "applied");

  apply.begin({ ...base, request_id: "apply-2" });
  assert.equal(apply.accept({ type: "self_evolution.apply.unavailable", session_id: base.session_id, task_id: base.task_id, attempt_id: base.attempt_id,
      run_id: base.run_id, turn_epoch: base.turn_epoch, snapshot_revision: base.snapshot_revision, request_id: "apply-2", reason: "cancelled" },
    { ...base, request_id: "apply-2" }), true);
  assert.equal(apply.current()?.outcome, "cancelled");
});


test("review without an explicit apply capability keeps selected apply unavailable", async () => {
  const { SelfEvolutionDiffReview, renderSelfEvolutionApplyControls } = await import("../src/shells/unified/self-evolution-progress.ts");
  const review = new SelfEvolutionDiffReview();
  const base = { ...identity, snapshot_revision: 5, request_id: "review-legacy" };
  review.requested(base);
  assert.equal(review.accept({ type: "self_evolution.review.result", ...base, candidate_digest: "a".repeat(64),
    truncated: false, content_complete: true, files: [] }, base), true);
  assert.equal(renderSelfEvolutionApplyControls(review.current(base), null), "");
});

test("partial apply result remains explicit and warns to verify before retry", async () => {
  const { SelfEvolutionApply, renderSelfEvolutionApplyControls } = await import("../src/shells/unified/self-evolution-progress.ts");
  const apply = new SelfEvolutionApply();
  const identity = { ...({ session_id: "session-a", task_id: "task-a", attempt_id: "attempt-a", run_id: "run-a", turn_epoch: 2 }), snapshot_revision: 7, request_id: "apply-original" };
  apply.begin(identity);
  const result = { type: "self_evolution.apply.result", ...identity, status: "partial" };
  assert.equal(Object.keys(result).length, 9);
  assert.equal(apply.accept(result, identity), true);
  assert.equal(apply.current()?.outcome, "partial");
  const html = renderSelfEvolutionApplyControls(null, apply.current());
  assert.match(html, /仅部分文件已写入/);
  assert.match(html, /检查实际改动并完成验证，再决定是否重试/);
  assert.match(html, /role="alert"/);
});

test("apply response remains bound to its original revision but is rejected for a new attempt", async () => {
  const { SelfEvolutionApply } = await import("../src/shells/unified/self-evolution-progress.ts");
  const apply = new SelfEvolutionApply();
  const original = { ...identity, snapshot_revision: 7, request_id: "apply-original" };
  const response = { type: "self_evolution.apply.result", ...original, status: "partial" };
  apply.begin(original);
  assert.equal(apply.accept(response, original), true);

  apply.begin(original);
  const newerSnapshot = { ...original, snapshot_revision: 8 };
  assert.equal(apply.accept(response, newerSnapshot), true);
  assert.equal(apply.current()?.outcome, "partial");
  apply.begin(original);
  assert.equal(apply.accept(response, { ...original, attempt_id: "attempt-next", run_id: "run-next" }), false);
});
test("apply result keeps its strict identity envelope and pending survives a same-run revision bump", async () => {
  const { SelfEvolutionApply } = await import("../src/shells/unified/self-evolution-progress.ts");
  const shell = (await import("node:fs")).readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  const apply = new SelfEvolutionApply();
  const original = { ...identity, snapshot_revision: 7, request_id: "apply-original" };
  const response = { type: "self_evolution.apply.result", ...original, status: "partial" };
  apply.begin(original);
  assert.equal(apply.accept({ ...response, unexpected: true }, { ...original, snapshot_revision: 8 }), false);
  assert.equal(apply.current(), null);
  apply.begin(original);
  assert.equal(apply.accept(response, { ...original, snapshot_revision: 8 }), true);
  assert.match(shell, /selfEvolutionApplyRequest\.turn_epoch !== view\.turn_epoch/);
  assert.match(shell, /const current: SelfEvolutionApplyResponseIdentity = \{ \.\.\.request, snapshot_revision: view\.snapshot_revision \}/);
  assert.ok(shell.includes("selfEvolutionDiffRequest.snapshot_revision !== view.snapshot_revision"));
});
