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

test("goal pause/budget top banner stays hidden for active work", () => {
  assert.equal(renderGoalBudgetNotice(goal()), "");
});

test("goal pause/budget top banner stays hidden for every pause reason", () => {
  for (const pause_reason of [
    "budget_exhausted",
    "process_restart",
    "budget_extended",
    "repair_exhausted",
    "user",
    "cancelled",
    "authorization_required",
    "custom <reason>",
    null,
  ]) {
    assert.equal(
      renderGoalBudgetNotice(goal({ status: "paused", pause_reason })),
      "",
      String(pause_reason),
    );
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
    assert.doesNotMatch(marker, /已恢复|正在执行|当前目标/);
  }
});

test("ordinary assistant messages do not get a historical pause marker", () => {
  assert.equal(renderHistoricalGoalPauseMarker("任务继续处理中。"), "");
});

test("unified assistant transcript applies the historical marker without changing message content", () => {
  const source = readFileSync(new URL("../src/shells/unified/index.ts", import.meta.url), "utf8");
  assert.match(source, /block\.kind === "assistant" \? renderHistoricalGoalPauseMarker\(block\.text\)/);
});