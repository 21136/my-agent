# BUG-021 · 项目进度闭环死结（report_progress 不可达）

> 日期：2026-07-30  
> 状态：**fixed** · 2026-07-31（Phase 21 F1–F6）  
> 设计决议：[PROJECT-MODE.md](../PROJECT-MODE.md) **§0e**  
> 任务：`TASKS.md` Phase 21 · T-2101～T-2108  
> 触发会话：`workspace/huiyi` · `20260730-27fd72d2`

---

## 1. 现象

1. 项目已 `confirmed`，助手完成一项工作后无法勾选 `TASKS.md`。
2. 直写 `TASKS.md` 被 executor 硬拒（要求改用 `report_progress`）。
3. `run_evolved(tool_name="report_progress")` 报「不在本会话清单」。
4. 侧栏不更新；Task 一停门不武装；用户误以为「工具缺失 / 要新造工具」。

## 2. 复现（无 LLM · 门禁模拟）

2026-07-30 在隔离 `AgentPaths` 上跑通：

```text
create_project → bind_project_session
  → topics=['coding'] · shell=project · ENV.md ✓
  → session_evolved：无 report_progress
  →（模拟 agent.run_turn draft 分支）shell 被改成 grow
      → confirm_project_plan 失败「未打开项目」
      → project.md 不注入 · after_turn 无 project.state
  → 恢复 shell=project → 确认成功
  → TASKS 直写被拦（文案指向 report_progress）
  → executor.validate(report_progress) → 不在清单
  → report_progress 缺 project_id → ok:false
  → _WORKSPACE_WRITE_TOOLS 不含 report_progress → task_stop 无法武装
```

## 3. 根因

| 层 | 原因 |
|----|------|
| 清单 | `registry.evolved_for_topics` 按 **目录 scope ∈ session.topics**；`report_progress` 的 scope=`project` |
| 主题 | 绑项目只加 `coding`（P17）；**不**注册 `project` 主题（P8）→ scope 永远进不了 topics |
| 门禁 | confirmed 后禁止直写 TASKS，只许 `report_progress` |
| 一停 | `_maybe_arm_task_stop` 只看 `write_text`/`append_text`/`copy_move` 碰 TASKS |
| draft 壳 | `agent.run_turn` 在 `project_plan_gate_open` 时强制 `active_shell="grow"`，与 `activity_router`（待确认→project）相反 |
| 参数 | evolved `report_progress` 运行时要 `project_id`，schema/清单未暴露，executor 不注入 |

## 4. 非根因

- 工具文件缺失（`evolve/tools/project/report_progress/` 存在且 `status=active`）
- Plan Agent / 侧栏刷新机制本身（`after_turn_project_hooks` 在 shell=project 时会读盘发 `project.state`）
- 用户「没装工具」——是 **allowlist 设计洞**，不是磁盘洞

## 5. 影响面

- 所有「仅 topics=coding」的 project 会话（含 huiyi 及历史 stab-*）
- Phase 20 Task 一停门在「助手勾选」路径上实际失效
- draft 阶段先聊天再确认的路径：确认卡 / `项目 确认` 可能失败

## 6. 修复

| ID | 落地 |
|----|------|
| F1 | `loader.session_evolved_tools` 在 project 绑定下并入 `scope=project` |
| F2 | `agent.run_turn` 去掉 draft→grow |
| F3 | `_maybe_arm_task_stop` 识别 `report_progress` |
| F4 | `_maybe_inject_report_progress_project_id` |
| F5 | overlay / `project.md` 文案 |
| F6 | `PlanAgent.report_progress` → `toggle_task` |

回归：`python -m unittest tests.test_project_progress_loop tests.test_task_stop`
