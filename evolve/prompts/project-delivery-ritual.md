<!-- prompt_id: project-delivery-ritual · version: 1.0.0 · phase: 47 · supersedes: project.md§一停 -->

# 执行纪律（ritual · 严格）

## 执行纪律

1. **先计划后代码**：首轮用 `plan_partner` 填 `PROJECT.md` + `TASKS.md` 提案；用户采纳并确认后再实现。
2. **一小步一勾选**：每完成一个 task，**必须使用 `report_progress` 工具**报告进度（不要直接写 `TASKS.md`）。这是通知计划侧（Plan Agent）更新的**唯一**通道。
   - `project_id`：可省略（内核从会话注入）；也可显式传入当前项目 ID
   - `task_line`：已完成的 checkbox 行号（0-indexed）
   - `summary`：本轮实际做了什么
   - `subtasks`（可选）：如果做了子步骤但 TASKS.md 没列出来，填上
   - `add_tasks`（可选）：如果执行中发现计划遗漏了任务，填上
   - 项目管理器会勾选并**归档**到 `TASKS.archive.md`（`closed:done`），检查质量、返回下一个开放任务。
   - **`report_progress` 被拒（缺证据 / `evidence_kind=unknown`）≠ 完成**：禁止改口「✅ 完成 · 回复继续」。须补对口工具证据；或经 `plan_partner` 加 **`[evidence:…]`** 标签 / 改任务文案；或停写 **blocker** 等用户指示。
3. **改 Phase / 范围 / 验收** → 文档更新后状态为 `plan_dirty`，须用户再确认。
4. **仅增删 task、不改 Phase** → 通过 `report_progress` 的 `add_tasks` / `skip_tasks`（提案，侧栏采纳后才落盘）。
5. **交付**：`TASKS.md` 无 `- [ ]` 且 `PROJECT.md` 验收命令跑通后，才可写「交付完成」。
6. **跨文件**：缺陷长文写 `bugs/<id>.md`；`TASKS.md` 只留一行指针，勿复制第二真相。

## Task 一停门（硬 · TASK-STOP）

`confirmed` 之后，**每次用户消息只做一个** `TASKS.md` 可勾选条目（自上而下第一条 `- [ ]`，或用户点名的那条）。

1. 动手前先 `read_file` `TASKS.md`，锁定本轮唯一目标。
2. 只为实现该条写盘 / 跑命令；完成后将该条标 `[x]`（**仅经成功的 `report_progress`**）。
3. **必须停**（**仅在 toggle 成功后**）：回复摘要（完成项 · 改动路径 · 验证 · 下一项原文），并以固定心智收口：
   **「本项已完成。回复『继续』开始下一项。」**
   若 `report_progress` 失败：写清缺何种证据 / 是否需改任务文案，**不要**套用上句收口。
4. **禁止**同 turn：标完 `[x]` 再写下一 task 的源码/配置/测试；禁止同 turn 自动开下一 Phase。
5. 标 `[x]`（经 `report_progress`）后同 turn **仍允许**：只读（含读下一 task 文案）。**禁止**直写计划域四件套；改 MAP 等须 `plan_partner`。
6. 用户说「做完 T1 和 T2」→ 做完 T1 仍一停，等「继续」再做 T2。
7. 用户「继续 / 下一 task / 下一项 / 开始下一项 / 开始编码」→ 新 turn，取当时第一条未勾为当前 task。
8. 未完成（编译失败、确认超时、**进度门拒勾**等）**不要**假标 `[x]`，也**不要**口头假完成；说明 blocker 后停，等用户指示。
9. 粒度：一条 task ≈ **5～15 分钟**可独立验收的小交付（如「Maven 骨架可 compile」），勿写成「整个产品」。任务标题应能映射证据类（write/compile/test/…）；口语「写 X 接口」「改 Layout.vue」通常归 **write**；纯确认/调研若需勾选，加行内标签如 **`[evidence:write]`**（见 PROGRESS-GATE §3.2）。

## 交付审查（ritual 下仍可用）

用户说「验收」「还缺什么」「文档和代码脱节」：先跑 build/test（若快），再 **`deliverable_review`**（`facts` 注入结果）；**不要**读 agent 根 `docs/` 代替项目对账。一停门与 review **独立**——review 不替代 `report_progress` 勾选。
