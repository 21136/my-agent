import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { renderGoalBudgetNotice, renderHistoricalGoalPauseMarker, type GoalStateEvent } from "../src/shells/unified/goal-budget.ts";

const goal = (overrides: Partial<GoalStateEvent> = {}): GoalStateEvent => ({
  type: "goal.state",
  goal_id: "goal-1",
  goal_text: "Fix the issue",
  status: "active",
  pause_reason: null,
  next_step: "Continue",
  can_auto_continue: true,
  budgets: {
    tool_turns_used: 3,
    tool_turns_limit: 50,
    wall_seconds_used: 42,
    wall_seconds_limit: 900,
    repair_count: 1,
    repair_limit: 3,
  },
  ...overrides,
});

test("normal active work hides cumulative usage and budget controls", () => {
  assert.equal(renderGoalBudgetNotice(goal()), "");
});

test("budget_exhausted explains an explicit human request can recover the original goal", () => {
  const html = renderGoalBudgetNotice(goal({ status: "paused", pause_reason: "budget_exhausted" }));
  assert.match(html, /检查当前进展/);
  assert.match(html, /明确发送下一步请求/);
  assert.doesNotMatch(html, /累计用量|工具轮次|运行时间|扩额|\/goal|button|data-action/);
});

test("process restart and budget extension are identified as recoverable by a manual request", () => {
  for (const pause_reason of ["process_restart", "budget_extended"]) {
    const html = renderGoalBudgetNotice(goal({ status: "paused", pause_reason }));
    assert.match(html, /明确发送下一步请求/, pause_reason);
    assert.doesNotMatch(html, /原目标保持暂停|新建目标|\/goal new:/, pause_reason);
  }
});

test("non-recoverable and unknown pauses keep the original goal paused and direct the user to a new goal", () => {
  for (const pause_reason of ["repair_exhausted", "user", "cancelled", "authorization_required", "custom <reason>", null]) {
    const html = renderGoalBudgetNotice(goal({ status: "paused", pause_reason }));
    assert.match(html, /原目标保持暂停/, String(pause_reason));
    assert.match(html, /普通请求不会解除/, String(pause_reason));
    assert.match(html, /当前会话.*动手模式/s, String(pause_reason));
    assert.match(html, /\/goal new: &lt;新目标内容&gt;/, String(pause_reason));
    assert.match(html, /发送后会新建目标并开始处理/, String(pause_reason));
    assert.match(html, /原目标记录/, String(pause_reason));
    assert.match(html, /权限.*确认/, String(pause_reason));
    assert.doesNotMatch(html, /明确发送下一步请求/, String(pause_reason));
    assert.doesNotMatch(html, /&lt;reason&gt;[^<]*可恢复/, String(pause_reason));
  }
});

test("terminal goals do not leave a stale pause notice", () => {
  assert.equal(renderGoalBudgetNotice(goal({ status: "done" })), "");
});


test("historical pause messages are marked as historical without asserting recovery", () => {
  for (const text of [
    "任务已暂停 应用重启中断了此前的执行",
    "目标已暂停 (budget_exhausted)",
    "[Goal] 本回合工具执行额度已用尽；自动续段已停止。",
  ]) {
    const marker = renderHistoricalGoalPauseMarker(text);
    assert.match(marker, /历史暂停记录/);
    assert.match(marker, /仅描述当时状态，不代表当前状态/);
    assert.doesNotMatch(marker, /已恢复|正在执行|当前已/);
  }
});

test("ordinary assistant messages do not get a historical pause marker", () => {
  assert.equal(renderHistoricalGoalPauseMarker("任务继续处理中。"), "");
});

test("unified assistant transcript applies the historical marker without changing message content", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  assert.match(source, /block\.kind === "assistant" \? renderHistoricalGoalPauseMarker\(block\.text\)/);
});