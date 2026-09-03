# 狂奔开头流程闸门（Runaway Startup Gates）

> 版本 **0.2.0** · 2026-09-03  
> 状态：**UI-6041～6044 done** · UI-6045 done · **结尾 → [RUNAWAY-VERIFICATION-ORCHESTRATOR.md](./RUNAWAY-VERIFICATION-ORCHESTRATOR.md)**  
> 关联：[RUNAWAY-FLOW-STATE-MACHINE.md](./RUNAWAY-FLOW-STATE-MACHINE.md) · [RUNAWAY-EXPERIENCE.md](./RUNAWAY-EXPERIENCE.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) · [TASKS.md](./TASKS.md) UI-6041～6052 · [BUG-FIX-AGENT.md](./BUG-FIX-AGENT.md)

---

## 0. 摘要

狂奔在 **implementation / verification** 段相对成熟；**开头**（新建 → 开狂奔 → 进 implementation）与 **结尾**（repairing / ENV）曾各有一条「知道要修什么、却没有合法写入口」的死结。

- **结尾**：Phase 59 bug-fix + UI-6045（ENV `quality.commands`）已编码；**谓词门与 verification 短接**见 [RUNAWAY-VERIFICATION-ORCHESTRATOR.md](./RUNAWAY-VERIFICATION-ORCHESTRATOR.md)（UI-6046～6052）。
- **开头**：本文档记录 2026-09-03 审计结论；修复项 **UI-6041～6044** 按优先级落地。

---

## 1. 设计意图（开头）

```text
新建/绑定 → requirements
  → organize（整理四件套）→ documentation
  → confirm_design → design
  → start_project_task(T-*) → implementation + checkpoint implementing
  → …狂奔写码 / 验收 …
```

Harness 函数：`_prepare_runaway_project_start()`（`agent.py`）应在狂奔下**自动**跑完 requirements→design，无需用户手打「项目 整理文档」等 CLI。

---

## 2. 已发现问题（按修复顺序）

| ID | 严重度 | 现象 | 根因 | 修复 |
|----|--------|------|------|------|
| **UI-6041** | P0 | 开狂奔 / Harness 续跑 / 短指令「继续实现」后仍卡在 `requirements`/`documentation`/`design`，业务代码被 stage_gate 拦 | `_prepare_runaway_project_start` 仅在 `intent==requirements`（长文需求）时调用；Harness 行含「实现」→ `execute` | 狂奔 + 早期 stage：每回合 `begin_turn` 后调 prep（requirements intent 仍 plan 后再 prep）；`project.runaway.set` 同步 prep |
| **UI-6042** | P1 | 恢复/续跑时 progress_gate 武装错任务 | `begin_turn` 用 `first_open_task`，狂奔应用 `active_task_id` 或 `next_open_task` | 狂奔武装逻辑对齐 advance |
| **UI-6043** | P1 | overlay 写「可连续执行」，executor 仍报「准备流程不能写业务代码」 | `format_project_overlay` 早期 stage 文案与 `project_mode_block_reason` 不一致 | requirements/早期 stage 明示 stage_gate |
| **UI-6044** | P1 | 狂奔下侧栏/路由仍显示「计划待确认」 | `activity_router` 未像 `project_api` 一样看 `runaway_enabled` | 狂奔跳过 plan_gate 路由标签 |

### 非本批（记录备查）

| 项 | 说明 |
|----|------|
| 切换项目清空狂奔 | `bind_project_session` 设 `runaway_enabled=False` — 产品选择，暂不改为保留 |
| L2 manifest stale | 计划变更后拦写码 — 正确，与狂奔无关 |
| `confirm_project_design` 缺 token | prep 半途 `_pause_runaway` — 需 Harness 修文档或用户手改 |
| 主 Agent 直写七件套 | 设计如此；repairing 由 bug-fix（含 ENV）负责 |

---

## 3. 代码锚点

| 区域 | 路径 | 符号 |
|------|------|------|
| 开头 prep 循环 | `agent-core/agent.py` | `_prepare_runaway_project_start`, `_maybe_run_runaway_startup_prep`, `run_runaway_startup_prep_if_needed` |
| Turn 入口 | `agent-core/agent.py` | `run_turn` · `runaway_requirements_turn` |
| 狂奔开关 | `agent-core/project_api.py` | `project.runaway.set` |
| 阶段权限 | `agent-core/project_mode.py` | `project_mode_block_reason`, `format_project_overlay` |
| 武装任务 | `agent-core/tools/executor.py` | `begin_turn` |
| 活动路由 | `agent-core/activity_router.py` | `compute_activity_route` |
| 意图 | `agent-core/turn_intent.py` | `is_requirement_input`, `classify_turn` |
| 验收修复 | `agent-core/subagent.py` | `run_bug_fix` · `BUG_FIX_PLAN_WRITE_ALLOWLIST` |

---

## 4. 验收（S-6041～6044）

| ID | 场景 |
|----|------|
| S-6041a | 狂奔 + `requirements` + `execute` intent → prep 跑至至少 `documentation` |
| S-6041b | `project.runaway.set` enabled + 早期 stage → 同步 prep |
| S-6042 | 狂奔 + `active_task_id` + 依赖队列 → `begin_turn` 武装 active 而非首个 open |
| S-6043 | 狂奔 + `requirements` → overlay 含「禁止写业务代码」类 stage_gate |
| S-6044 | 狂奔 + `plan_status=draft` → 路由标签非「计划待确认」 |

自动化：`agent-core/tests/test_runaway_startup.py`

---

## 5. 变更记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-09-03 | 0.1.0 | 审计成文；UI-6041～6044 排期 |
| 2026-09-03 | 0.1.1 | UI-6045（bug-fix ENV）已编码 |
| 2026-09-03 | 0.2.0 | UI-6041～6044 编码 + `test_runaway_startup.py` |
| 2026-09-03 | 0.2.1 | 指向 RUNAWAY-VERIFICATION-ORCHESTRATOR（结尾谓词门） |
