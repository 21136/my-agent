# 提示词注册表（PROMPT-REGISTRY）

> 版本 **0.1.0** · 2026-08-06 · Phase **47** · [DELIVERABLE-REVIEW.md](./DELIVERABLE-REVIEW.md) T-4700  
> **用途**：可注入 system / 子代理 prompt 的**唯一索引**；改 prompt 必须同步本表 + version 头。  
> **纪律**：可执行规则优先 **B/C 代码**；本表 `enforced_by=code` 的行，prompt 仅作说明。

---

## 1. 如何使用

1. 新增/拆分 prompt 文件 → 在本表加一行，`version` bump  
2. 文件首行 HTML 注释：`<!-- prompt_id: … · version: … · phase: … -->`  
3. PR / 实施前跑 `IT-473`（loader 快照）与 §3 手工核对  
4. **禁止**在 `core.txt` 复制本表长文

---

## 2. 注册表

| id | version | path | injected_when | enforced_by | supersedes | phase |
|----|---------|------|---------------|-------------|------------|-------|
| core | 2026-08-05 | `agent-core/prompts/core.txt` | 所有会话 S0 | both | — | 2 |
| safety | * | `evolve/prompts/safety.md` | 始终 | prompt | — | 3 |
| project | 1.0.0 | `evolve/prompts/project.md` | project 绑定 | prompt | — | 11 |
| project-boundaries | — | `evolve/prompts/project-boundaries.md` | project 绑定 | prompt | project.md§边界 | **47 todo** |
| project-delivery-solo | 1.1.0 | `evolve/prompts/project-delivery-solo.md` | project + `profile=solo` | both | project.md§执行纪律 | **47+48** |
| project-delivery-ritual | — | `evolve/prompts/project-delivery-ritual.md` | project + `profile=ritual` | both | project.md§一停 | **47 todo** |
| coding | * | `evolve/prompts/coding.md` | topics 含 coding | prompt | — | 3 |
| tool-workshop | * | `evolve/prompts/tool_workshop.md` | workshop_eligible | prompt | — | 46 |
| subagent-explore | * | `evolve/subagents/explore.md` | spawn explore | prompt | — | 7 |
| subagent-explore-tool | * | `evolve/subagents/explore_tool.md` | spawn explore(tool) | prompt | — | 46 |
| subagent-checker-tool | * | `evolve/subagents/checker_tool.md` | spawn checker scaffold | prompt | — | 17 |
| subagent-checker-test | 0.1 | `evolve/subagents/checker_project_test.md` | spawn checker test_fail | prompt | — | 44 |
| subagent-review-deliverable | — | `evolve/subagents/review_deliverable.md` | spawn deliverable_review | prompt | — | **47 todo** |
| overlay-project | * | `project_mode.format_project_overlay` | project 绑定每轮 | **code** | — | 11 |
| overlay-task-paused | * | `loader.TASK_PAUSED_MARKER` | ritual + toggle 成功 | **code** | — | 20 |

**图例**：`enforced_by=code` → 以 Python 注入为准，prompt 不得矛盾；`both` → 代码与 prompt 须一致。

---

## 3. Phase 47 迁移对照（`project.md` v1 → v2）

| 原 `project.md` 章节 | 目标 id | solo 注入 | ritual 注入 |
|----------------------|---------|-----------|-------------|
| 边界、换线 | project-boundaries | ✓ | ✓ |
| 计划确认门 | project-boundaries | ✓ | ✓ |
| 计划域纪律 B5 | project-boundaries | ✓ | ✓ |
| §执行纪律 report_progress | project-delivery-ritual | ✗ | ✓ |
| §Task 一停门 | project-delivery-ritual | ✗ | ✓ |
| ENV / 构建纪律 | project-boundaries | ✓ | ✓ |
| （新）交付审查 | project-delivery-solo | ✓ | 可选短句 |
| （新）L0/L1 完成定义 | project-delivery-solo | ✓ | ✗ |

---

## 4. 过期句黑名单（solo profile）

下列字符串 **不得**出现在 `solo` 会话的合并 system 中（`IT-473` 断言）：

- `回复「继续」开始下一项`
- `回复『继续』开始下一项`
- `每完成一条 TASKS 勾选必须停`
- `task_stop: 每完成一条`
- `一律 report_progress`
- `必须使用 report_progress 工具`（solo）

ritual profile **允许**含以上句子。

---

## 5. 与 B/C 层代码映射

| 行为 | 代码真源 | prompt 应 |
|------|----------|-----------|
| 未 confirmed 禁写码 | `project_mode.plan_gate` | 一致 |
| 计划域禁直写 | `executor` + B5 | 一致 |
| solo 不一停 | `task_stop` profile 分支 | **不提**一停 |
| solo Gate 放宽 | `progress_gate` profile 分支 | **不提** unknown 死刑 |
| 主人改 TASKS | `plan_agent` source=user | solo 不写「外部修改」 |
| spawn review | `executor._run_deliverable_review` | core 一行指针 |

---

## 6. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-06 | Phase 47 T-4700 初版；待实施行标 todo |
