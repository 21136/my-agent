# 多项目编排：主 Agent 调度 · 项目子 Agent 干活（草案 / 未实现）

> **状态：proposal / 草案 · 未实现**  
> 记录时间：2026-10-28（Asia/Shanghai）  
> 真源意图：用户确认「主 agent 管所有，子 agent 按项目干活」，以便**同时改多个项目且不串台**；并要求**兼容现有架构**，不全盘重做。  
> 本文档只固化设计，**不包含实现代码**。

相关现行实现：[AGENT-PARENT-ORCHESTRATION](./AGENT-PARENT-ORCHESTRATION.md) · [EXPLORE-SCOPE-RAILS](./EXPLORE-SCOPE-RAILS.md) · [ORCHESTRATION](./ORCHESTRATION.md) · [PROJECT-THREADS](./PROJECT-THREADS.md) · [PROJECT-SWITCH-STATE-ISOLATION](./PROJECT-SWITCH-STATE-ISOLATION.md) · [SUBAGENT-BUDGET](./SUBAGENT-BUDGET.md) · [DAY-JOURNAL-MEMORY](./DAY-JOURNAL-MEMORY.md)（记忆分层草案） · Goal Runtime P0（gent-core/goal_runtime.py）

---

## 0. 一句话

**UI 仍是一个主会话、一个主 Agent**；需要动多个项目时，主 Agent **按项目 spawn 作用域子 Agent（worker）**，每个 worker **钉死一个 project_root**，结果经现有 **overlay 回灌**；项目身份继续走 ind_project_dir / workspace junction，**不新建项目库，不重建 LangGraph / TASKS / runaway**。

---

## 1. 兼容地图（Reuse vs Add）

### 1.1 直接复用（不改真源契约）

| 现件 | 路径 / 锚点 | 本方案怎么用 |
|------|-------------|--------------|
| 主回合 | gent-core/agent.py 
un_turn | 唯一对用户说话、调工具、发 notice 的入口 |
| 子代理调度 | gent-core/subagent.py SubagentRunner | 增一种角色，不另起调度框架 |
| 回灌 | ormat_subagent_overlay → session.subagent_overlay / 	urn.notice | worker 结果同样进 overlay，主 Agent 再总结 |
| 范围提示 | explore_scope.py scope_rail / uild_kernel_auto_explore_task | worker 任务包装可复用 scope_rail=project 思路（提示层，不是沙箱） |
| 项目绑定 | ind_project_dir + project_cli.bind_external_project_dir | 缺项目时由**主 Agent**绑定；worker **不**改会话级绑定 |
| 会话字段 | Session.project_id / project_root / ctive_shell | UI「当前项目」仍最多一个；worker 用**会话切片/钉扎副本** |
| 写隔离 | executor._validate_foreign_project_write 等 | worker 的执行器 project_root 钉在本项目，外写继续拦 |
| 预算 | SUBAGENT-BUDGET / subagent_*_max | worker 纳入同一硬帽族，另加并发上限 |
| 长任务 | Goal Runtime P0 | 父 Goal 可罩多项目；子进度回灌，**不**跨进程复活 Run |
| 项目活线 | PROJECT-THREADS T1/T7 | **同一 project_id 同时最多一个活的执行体**；多项目 = 多个不同 id 的 worker |

### 1.2 最小新增（仅扩展，不重做）

| 新增 | 说明 |
|------|------|
| SubagentKind 增 project_worker（名可微调） | 与 explore/checker/review 并列 |
| SubagentRunner.run_project_worker(...) | 入参：	ask + **钉死的** project_id/project_root + 可选预算 |
| Worker 工具白名单 | 比 explore 宽（可写），比主 Agent 窄；**禁止** ind_project_dir / propose_context_switch / 跨项目写 |
| 父侧 spawn API | 主回合工具或内部 API：一次可排队 1..N 个 worker（先串后并） |
| Overlay 文案 | ormat_subagent_overlay 增加 worker 分支：项目 id、改动摘要、未完成、引用路径 |
|（可选）并发闸 | MY_AGENT_PROJECT_WORKER_MAX 之类，默认 2 |

### 1.3 明确不做

- 不重建 LangGraph、TASKS.md 队列、Progress Gate、runaway。
- 不发明第二套项目存储；继续 workspace/<id>/ + junction。
- 不把 UI 拆成「每项目一个主 Agent」。
- 不让 worker 静默切换主会话的 project_id（避免侧栏/Goal/线程串台；见 PROJECT-SWITCH-STATE-ISOLATION）。
- 不把 explore/checker 改成可写；它们保持只读。

---

## 2. 角色分工

`	ext
用户 ←→ 主 Agent（唯一 UI 会话）
              │
              ├─ explore / checker / review …（现行只读子代理）
              │
              └─ project_worker × N
                    ├─ worker@项目A（root=A，可写 A）
                    └─ worker@项目B（root=B，可写 B）
`

| 角色 | 权限 | 对用户可见 |
|------|------|------------|
| 主 Agent | 解释意图、绑项目、高风险确认、汇总、Goal 生命周期 | 聊天主述 |
| explore 等 | 只读调研 | overlay / notice |
| project_worker | 仅本项目读写 + 有限终端/测试 | 进度 notice + 最终 overlay；**不直接当用户确认** |

---

## 3. 流程示例：「同时改两个项目」

用户（同一聊天窗）：「A 项目把登录按钮改蓝，B 项目修一下 README 坏链。」

`	ext
1. 主 Agent 解析为多项目动手意图
   - 若当前是只聊：意图工具建议切动手 → 主界面弹窗确认（见 DAY-JOURNAL §7）
2. 解析/绑定项目
   - 已在 workspace 的用 project_id
   - 缺的用 bind_project_dir（主 Agent 调；可 dry_run → 确认）
   - UI 当前项目可停在其中一个或保持原样；**不要求**来回 switch 才能开第二个 worker
3. Spawn
   - worker_A(task=改登录按钮色, project_root=A)
   - worker_B(task=修 README 坏链, project_root=B)
   - 默认可并行（不同 project_id）；同 project_id 则串行（守 T1）
4. 执行隔离
   - 每个 worker：Session 切片 active_shell=project，project_root 钉死
   - Executor 沿用 foreign_project_write / project_mode 校验
5. 回灌
   - 各 worker → format_subagent_overlay(kind=project_worker)
   - 主 Agent 合并 overlay，用一两段话回复用户；需要确认的发布/外发仍走主链弹窗
6. Goal（若动手已建 Goal）
   - 父 Goal.workspace_scope 可记「多项目」或列表；验收可按项目分别跑，结果写回父 Goal / notice
7. 事后记忆（未实现的日记草案）
   - 当天目录：动过项目名 = [A, B]
   - 各项目按天库：各自完整片段；日记详情只留短提示 + 链接
`

---

## 4. 与现行子代理的关系

| 现行 kind | 变化 |
|-----------|------|
| explore | 不变；多项目时主 Agent 仍可先 explore 再派 worker |
| checker / deliverable_review | 不变；可在**某 worker 所属项目**内由主 Agent 再派 |
| terminal_plan | 不变；仍服务终端规划，不是多项目写码入口 |
| plan（历史） | 不恢复旧 PlanAgent 采纳队列 |

**第一刀实现建议（仍属未实现）：** 只加 project_worker + 串行双项目冒烟；并行与 Goal 多 scope 第二刀。

---

## 5. 会话 / UI 约束（防串台）

1. **主会话**继续最多一个「UI 当前项目」（project_id / 侧栏）。
2. Worker **不得**写主会话的 project_id；钉扎只在 worker 私有执行上下文。
3. 迟到的 project.state 仍按 PROJECT-SWITCH-STATE-ISOLATION 丢弃。
4. 用户问「现在在改哪个」：主 Agent 应能列出 **活跃 worker 及其 project_id**（产品可见性，实现时可先 notice）。
5. Stop：停主回合应取消未完成 worker（复用 cancel_event 模式）。

---

## 6. Goal Runtime 衔接

- 保持 P0：**不**跨进程自动复活 Run；Stop 后不自动续。
- 多项目长任务：优先 **一个父 Goal** + 子步骤文案点名项目；或「一项目一子预算」记在 overlay，而不是新调度器。
- workspace_scope：可扩展为逗号分隔 / JSON 列表（实现时再定 schema）；未实现前文档层只要求「能区分涉及哪些项目」。

---


## 7. 隔离与防污染（一个会话、多项目）

> 用户关心：界面仍是一个会话，怎么同时谈/改多个项目却不串台、不陷入幻觉；若已污染怎么收。

### 7.1 硬隔离（未实现约定）

1. **一个 UI 会话**：用户只面对主 Agent；不因多项目新开主会话窗。
2. **每个 project_worker 钉死** project_id + project_root；执行期内不得改绑、不得写主会话的当前项目字段。
3. **写盘只允许落在本 worker 绑定 root 下**（沿用 / 加强现有 foreign_project_write 等校验）。
4. **主 Agent 只消费带项目标签的 overlay**（project_id / worker_id）；未打标的摘要不得当作「已在某项目完成」的证据。
5. **跨项目断言必须引用来源**：说明来自哪个 worker 回执或哪条已读路径；禁止把 A 的结论安到 B 上。

### 7.2 污染判定与处置

出现串台迹象时（例如 worker 回执写了别的 root、主述把 A/B 事实混成一条、overlay 缺 project_id）：

1. **立刻停止**相关 / 全部活跃 worker（复用 cancel_event）。
2. **丢弃受污染 overlay**（不写入 system 续跑、不升格进日记/久远库）。
3. **按项目重新派工**：每个项目单独 spawn worker，任务写清边界；必要时先只读 explore 再写。
4. 向用户一句说明：已判污染、已停、将按项目重跑——不假装上一轮结果仍有效。

### 7.3 记忆侧打标

- 按天日记「动过哪些项目」只记 **项目名/id**；项目详情链接进「项目→按天」库（见 [DAY-JOURNAL-MEMORY](./DAY-JOURNAL-MEMORY.md)）。
- 久远记忆若沉淀与项目相关的硬事实，**必须带项目标签**；无标签不得当跨项目通则。

---

## 8. 主 Agent 防幻觉

主 Agent 是唯一对用户说话的编排者，幻觉代价最大。约定（未实现）：

1. **只编排 + 汇总，不自己改多个项目**：多项目写码交给对应 project_worker；主 Agent 不在同一回合里对两个 root 直接动刀。
2. **每条事实性声明**须带 **项目标签**，且能指向 **worker 回执**（或明确的工具/文件证据）；无回执不说「已改好」。
3. **吃不准就问，或再派工**：缺项目身份、缺验收、回执矛盾 → 向用户确认，或按项目重新 dispatch；禁止脑补补全。
4. **紧预算 / 紧白名单**：主侧多项目回合限制并行 worker 数与 overlay 体积；主 Agent 工具面不因「方便」扩成可写任意 root。
5. 与 §7 联动：一旦怀疑污染，走停工→丢 overlay→按项目重跑，而不是在混杂上下文里「再编圆一点」。

---

## 9. 分角色系统提示词

需要对不同 Agent 使用 **不同系统提示词**（未实现）：

| 角色 | 提示词重心 |
|------|------------|
| **主 Agent（parent）** | 编排、意图拆分、派工、汇总、防串台；禁止冒充已完成的多项目改动；强调标签与回执 |
| **project_worker** | 只服务钉死的 project_id/project_root；任务边界、可写范围、回执格式（结论 / 已改 / 未完成 / 路径） |
| explore / checker / review 等 | 保持现行只读 / 验收角色提示；不与 worker 混用同一套「可写项目」提示 |

约束：

- **人设可共享**（如小魔王语气）作为薄层前缀。
- **角色职责不得共用一个大 blob**：parent 与 worker 必须是分开的提示模块，避免「编排者」和「施工者」指令互相污染。
- Worker 提示词须显式写入绑定 root；Parent 提示词须显式禁止无回执的跨项目断言。

---

## 10. 风险与开放问题

| 风险 | 说明 | 缓解方向 |
|------|------|----------|
| 同盘并发写 | 两 worker 若误指向同 root | 强制同 project_id 串行（T1） |
| 权限抬升 | worker 可写后比 explore 危险 | 窄白名单；确认上交主链；禁 bind/switch |
| Overlay 膨胀 | 多 worker 摘要塞爆 system | 截断 + 按项目分条；细节按需 
ead_file |
| UI 当前项目误解 | 用户以为侧栏项目 = 唯一在改的 | notice 列出活跃 worker；弹窗文案写清 |
| Goal 验收歧义 | 多项目何谓 done | 父 Goal 明确「全部验收过」或「按项目分状态」 |
| 日记双写 | 与 DAY-JOURNAL 草案打架 | 日记只记项目名；详情链接到项目按天库 |
| 实现范围失控 | 借机重做编排 | 锁死 §1.3；第一刀只串行 worker |
| 主 Agent 幻觉 / 串台叙述 | 一个界面多项目时把 A/B 说成一件事 | §7–§8：标签回执、停工丢 overlay、主侧不自改多 root |

开放问题（实现前要定）：

1. Worker 是否允许 
un_evolved 写工具，还是只暴露一组固定 builtin 写工具？
2. 并行默认开还是默认串、需用户确认后才并？
3. 父 Goal 与「每项目验收命令」的 schema 最小字段集。

---

## 11. 文档索引

- 现行主/子：[AGENT-PARENT-ORCHESTRATION](./AGENT-PARENT-ORCHESTRATION.md)
- 范围提示：[EXPLORE-SCOPE-RAILS](./EXPLORE-SCOPE-RAILS.md)
- 编排边界：[ORCHESTRATION](./ORCHESTRATION.md)
- 项目活线：[PROJECT-THREADS](./PROJECT-THREADS.md)
- 切换隔离：[PROJECT-SWITCH-STATE-ISOLATION](./PROJECT-SWITCH-STATE-ISOLATION.md)
- 一个界面 + 日记 + 弹窗：[DAY-JOURNAL-MEMORY](./DAY-JOURNAL-MEMORY.md)
- 久远记忆：[MEMORY](./MEMORY.md)

---

## 12. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-10-28 | 初稿：兼容地图 + 双项目流程 + 最小扩展；明确未实现 |
| 2026-10-28 | 增补 §7 隔离防污染、§8 主 Agent 防幻觉、§9 分角色系统提示词；仍为草案 |
