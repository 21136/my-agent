# 普通模式状态机重构：意图、权限与进度分离

> 版本：0.4.0 · 2026-10-16  
> 状态：**current · authoritative · 已实施并验证**（2026-10-16）；实现证据见 `agent-core/`、`desktop/` 定向测试与构建。
> 范围：Desktop/CLI `project` 普通模式；v1/v2 自动流程保持现有 owner。

## 1. 设计目标

普通模式不再要求项目按 `requirements → documentation → design → implementation → verification → release` 逐阶段获准前进。用户明确要求实现时，即使项目尚在整理文档/设计、计划为 draft/plan_dirty，也能在项目安全边界内开始实现。

本重构将四种概念分开：

1. **工作意图**：本次请求是 `read_only`（问答/查看）、`plan_only`（只规划）还是 `execute`（实现/运行项目工作）。
2. **操作权限**：按项目绑定、目标路径与操作风险确定 `allow / ask / deny`；不按项目“阶段”猜权限。
3. **执行生命周期**：复用已有 `ExecutionLifecycle`，继续负责一次运行的 queued/running/stopping/settled/paused/failed，不再造第二个 run FSM。
4. **项目进度事实**：从 TASKS、项目制品、测试/验收证据推导；只用于状态摘要和解释，不回写权限、不阻止执行。

Plan 是用户可选的工作方式，不是项目必须经过的阶段。普通实现请求不需要先生成或确认 AI 计划，也不需要用户反复选择“下一阶段”。

## 2. 现状与问题

当前普通写入允许性由多路状态共同决定：`project_workflow_stage` 的阶段墙、`project_plan_status` 计划门、`project_entry` 的 plan/direct 路由、light/standard 模板例外，以及 manifest 的 L2 stale 检查。它们既参与提示/UI，也分散影响 `project_mode_block_reason()`、turn-intent 路由和计划确认函数。

由此产生三个耦合问题：

- 一个展示性阶段值（例如旧 session 留下的 `documentation`）可能变成代码写入拒绝原因；
- `project_entry=plan`、draft/plan_dirty 等旧状态可能压过用户当前明确的实现意图；
- `execution_stage` 是从磁盘事实和持久化上下文混算的快照，却与权限门并列显示，容易让状态计算误差升级成“不能继续”。

本设计不把项目完成、发布审批或危险操作确认取消；要取消的是“阶段标签本身”作为普通写码许可条件。

## 3. 备选方案

| 方案 | 内容 | 评价 |
|---|---|---|
| A. 保留阶段 FSM，只允许 direct 跨阶段 | 继续保留 stage 与 plan gate；direct 是一个绕过开关 | 改动较小，但会继续保留双轨状态、sticky `project_entry` 和多处分支，无法消除阶段误判卡住 |
| B. 意图 + 操作权限 + 派生进度（推荐） | 普通工作由 request-scoped intent 决定；工具权限由路径/风险策略决定；项目进度从真实制品推导 | 与“阶段不再卡写码”目标一致；需改权限判断和兼容旧 SessionMeta，但权责最清楚 |
| C. 从制品自动推断阶段，再保留软/硬阶段出口 | 删除人工阶段写入，按文件和任务推导阶段，部分阶段仍阻止写入 | 可减轻陈旧 stage，但文档不完整/manifest 误判仍可能阻塞，且仍要求用户理解阶段 |

推荐 **B**，因为它直接满足本次目标：去掉阶段误判造成的写码卡点，同时保留明确的项目路径边界、风险审批与运行生命周期。

## 4. 推荐状态模型

### 4.1 工作意图（请求级，不做粘滞项目阶段）

- `read_only`：问题、回忆、状态查询等；允许读取，但不允许副作用。
- `execute`：用户提出明确的项目实现/修复/修改/测试请求时使用。即使请求同时要求先整理方案，只要目标是交付/执行，也不自动变成“只规划”。
- `plan_only`：仅在用户明确选择 Plan 按钮，或明确要求“只给方案、先不要改代码”时使用。允许读取项目并更新规划制品；不允许写业务源码或执行实现操作。

- 意图属于请求级临时值，不写入持久化 `SessionMeta`。新请求重新解析；`plan_only` 不能粘滞到下一条实现请求，旧 `project_entry=plan` 也不能永久锁住后续回合。
- 请求意图可以带可选 `task_scope`（例如用户明确选择“开始 T-001”）；它只约束当前请求的提示/目标，不构成文件级权限墙。session 中遗留的 `project_active_task_id` 不得静默缩小新请求范围。
- 现有 `turn_intent=plan` 或文档生成分类不能单独触发写码禁令；`plan_only` 必须来自明确的只规划请求/Plan 操作。实现请求中顺带提到计划、设计或文档，不应被误判为 plan_only。
- 只有在“只规划”与“开始实现”确实无法从用户要求中判断时才澄清；不能让 LLM 推测的 `workflow_stage` 或历史字段替用户作出“禁止实现”的决定。

| 输入事件 | 请求意图 | 生命周期 |
|---|---|---|
| 明确要求实现/修复，或点“开始实现” | `execute` | 只作用于本次请求/运行 |
| 明确要求只规划/不改代码，或点 Plan | `plan_only` | 只作用于本次请求/运行 |
| 用户在当前请求明确选择 T-ID/“开始任务” | `execute` + 当前请求 `task_scope=T-ID` | 任务范围只作用于本次请求，不产生阶段墙 |
| 提问、回忆、状态查询 | 既有 `qa/recall/status` 等只读意图 | 不因普通模式默认偏向 execute 而获得写权限 |
| 上一请求是 `plan_only`，新请求明确要求实现 | 切换到 `execute` | 不需要先清理项目阶段或改写旧字段 |
| 本次请求结束 | 清除请求级模式；保留项目制品事实 | 不留下项目级计划锁 |

### 4.2 操作权限（路径/风险硬门，不读取 project stage）

绑定、规范化路径与操作风险属于可验证硬门；当前请求目标范围由 Agent 遵循，若需要显著扩大范围则询问用户。普通工具策略返回 `allow / ask / deny`；intent=`read_only` 只允许读取，表中 `execute` 规则仅适用于明确的实现/运行请求：

| 条件 | 结果原则 |
|---|---|
| 无有效 project 绑定/root 的项目写入 | deny；先打开/新建项目 |
| intent=`read_only` | allow reads；拒绝文件写入/命令副作用 |
| 目标在当前项目 root 内，且 intent=`execute` | allow 与当前请求目标相关的普通文件编辑和项目内测试/质量命令；project root 不是“任意改全项目”的 blanket approval |
| intent=`plan_only` | 允许读取和规划制品操作；阻止业务源码编辑/实现命令 |
| 修改与当前请求目标明显无关，或会显著扩大范围 | 不因处于同一 project root 自动放行；向用户澄清/请求授权 |
| 写入目标逃出 project root | deny 或按现有 host/path policy ask；不能因 stage/direct 标记扩大范围 |
| 删除、安装、网络写入、发布、`git push` 等危险/外部副作用，或无法分类副作用的命令 | 按现有 action-risk approval 规则 ask；不因阶段为 implementation 自动放行 |
| plan draft/plan_dirty、缺少 DESIGN/MAP、L2 stale | 普通执行中改为 advisory，不单独阻止项目内实现；若用户明确要求确认计划，则走 plan_only/显式确认路径 |

模板 `light/standard` 表示制品与进度要求，不再决定能否越过阶段墙；普通权限与制品完整度解耦。runaway v1/v2 仍走原权限规则，不受本策略改写。

### 4.3 执行生命周期（沿用）

沿用 `ExecutionLifecycle` 的 run_id、取消/停止、终态和事件序列。普通项目重构不新增 `ProjectRunState` 或另一套 queued/running/failed 模型。若审批等待目前无法被生命周期准确呈现，先作为独立可观测性问题，不借项目阶段字段伪装。

### 4.4 项目进度（只读事实投影）

项目状态摘要使用 TASKS 完成/待办、规划制品存在/新鲜度、最近测试结果和验收证据等可核验事实；可以给出下一步建议，但建议不是必经 CTA。不要为了展示而将不确定事实硬归类为 requirements/documentation/design/implementation，也不要求用户逐阶段确认。Draft/plan_dirty/L2 stale 不能再占据强制确认条或“不能继续”的主状态；详情中可查看具体事实。快照不提供 allow/deny，也不自动改 session 权限字段。普通代码编辑不因验证未运行/未通过而被阶段状态禁止；但“验证通过/可发布/项目完成”的表述及发布动作，仍需真实验证证据和现有人工发布确认。

## 5. 现有字段兼容策略

| 现有字段 | 普通模式新职责 | 兼容处理 |
|---|---|---|
| `project_workflow_stage` | 普通模式不再读写或作为权限输入 | 旧值保留以兼容历史 session 和 runaway v1/v2；普通 UI 不把它渲染成必经阶段 |
| `project_plan_status` | 只记录计划是否 draft/confirmed/dirty，不是普通源码写入门 | `execute` 不自动改为 confirmed、不伪造确认时间；plan_dirty 可提示但不阻止实现 |
| `project_entry` | 旧的项目入口记录，不是跨回合的强制 permission mode | 旧值只兼容/显示；新请求的 request-scoped intent 优先，不因旧 `plan` 锁住 execute |
| `project_active_task_id` | 可作为当前任务上下文/提示 | 不做硬权限；新的明确用户请求可改变任务目标，但仍受当前请求范围约束 |
| `project.template` | 规定要维护/展示哪些制品 | 不改变普通源码写入许可 |
| `execution_stage` | 可保留 API 兼容字段，但降为只读进度摘要 | 不能用其 blockers 回写普通权限拒绝 |

`SessionMeta` 的历史字段不在第一步删除，也不靠启动时自动改写旧 session。先切断普通权限对这些历史阶段值的依赖，再决定字段/协议版本的后续清理。

## 6. 转换与权限判定流程

```text
new user request
  → resolve request intent + optional task_scope (read_only / explicit plan_only / execute; ambiguous intent asks)
  → check project binding and project-root scope
  → evaluate each tool action by intent + path + risk (allow / ask / deny)
  → run using existing ExecutionLifecycle
  → update project artifacts/evidence
  → derive read-only progress summary
```

状态变更规则：

1. `execute_request` 可从任意旧 `project_workflow_stage` 开始；不读写普通 stage，也不要求 direct skip-stage 标志。普通代码执行本身不等于用户确认 AI 计划，因此不会自动把 `project_plan_status` / 确认时间改成 confirmed。
2. `plan_only_request` 是当前请求意图，不是把整个项目永久推进到 documentation/design；结束后下一条请求重新解析。
3. tool policy 的拒绝原因必须指向具体原因（未绑定、越界路径、plan-only 限制、危险动作待审批）；不再返回“当前阶段不允许”。
4. 计划制品变更和 plan_dirty 检测继续用于进度/提醒。对普通模式，它们不自动锁住源码；用户显式点击“确认计划”时，只记录计划确认事实，不因此跳转项目阶段。
5. `confirm_direct_implement` 变为当前请求的 intent adapter，不再通过改写 `project_entry`、`project_plan_status` 或 `project_workflow_stage` 来打开写码门。
6. `organize_project_plan`、`confirm_project_design`、`start_project_task` 可继续维护文档/任务/确认记录，但不能以 workflow stage 限制后续普通 execute。
7. 阶段计算错误最多使摘要不准，不得阻断写操作；执行生命周期错误仍按 ExecutionLifecycle/cancel/recovery 规则处理。

## 7. 兼容边界与行为变化

这是有意的普通模式行为变更，需更新当前测试与权威文档：

- documentation/design/verification/release 下的 explicit execute 一律可进入实现；`test_direct_implement_from_documentation_and_design` 扩成全旧 stage 矩阵。
- plan entry、draft/plan_dirty、阶段快照、缺少 DESIGN/MAP、L2 stale 与 light/standard 模板档位不得单独阻止普通项目内实现；相应旧阻断测试改为权限矩阵测试。
- 计划确认、确认设计、开始任务可保留为显式记录/任务上下文操作，但不设置普通源码写入许可；execute 不伪造计划确认记录。
- 普通模式仍保留验证证据、发布审批与“已完成”结论的真实性要求；它们不阻止用户继续改码，但不能被直接实现意图伪造或跳过。
- 普通 run intent 通过当前 request/turn 传递，不持久化为 direct/plan sticky gate；明确的只读问答和 plan_only 仍不能写业务源码。
- 仍强制 project root/path scope、危险动作审批、未知副作用命令确认。
- `project_runaway_enabled=true` 的 v1/v2、其阶段/检查点/暂停状态不变；需分别跑其回归。
- `context_switch._bind_ordinary_light_entry()` 任意异常后仍强写 confirmed 是已知缺陷；建议修复为失败时不改写授权状态。该项是额外行为修正，是否并入本次实施需用户单独确认，不改变成功 bootstrap 行为。

## 8. 实施切片与验收

1. **Characterization**：建立 request-intent × 历史 stage × plan_status × template × action/risk 的权限矩阵；单列 v1/v2 基线。
2. **Intent/policy seam**：为当前 run 传递 request-scoped intent；让 `project_mode_block_reason()` 的 ordinary 分支调用确定性 action policy，runaway 分支保持原样。
3. **拆除普通阶段门**：迁移 `ordinary_skip_stage_walls`、`ordinary_direct_implement_draft_allowed`、`should_treat_ordinary_direct_implement` 和 `maybe_auto_confirm_plan_for_direct_implement`；普通 plan/status/stage 不再控制 `ToolExecutor` 权限。调整 `format_project_overlay()`：只有当前请求显式选定的 `task_scope` 才产生任务范围提示，旧 `project_active_task_id` 不得自动让新请求停在旧任务。
4. **停止普通阶段写入**：CLI 的 organize/confirm-design/start-task/direct/confirm 不再推进 `project_workflow_stage`；计划确认时间只在真实显式确认时更新；task id 仍可作为上下文。
5. **API/UI 迁移**：`project_api.py` 的 `needs_plan_confirm/needs_design_confirm/needs_documentation` 不再充当硬 CTA；`project-panel.ts` 移除阶段卡/阶段 blocker 主叙事，展示任务、文件、测试和风险事实，保留 optional Plan-only 入口。
6. **兼容与审计**：`project_workflow_stage` 读写只留历史/自动 owner；普通 `execution_stage` 不反向授权；审计 Agent prompt、CLI/WS、context-switch 和所有 tool gate，跑 ordinary 与 v1/v2 定向回归。

验收：

- 同一 safe project-root execute 操作对所有历史 `project_workflow_stage` 返回相同授权结果。
- `draft/plan_dirty`、L2 stale 和模板档位不再单独阻止普通项目内 execute。
- plan_only 只限制当前请求，不能意外把后续 execute 卡住；明确 execute 可以覆盖旧 `project_entry=plan`。遗留 active task 不得缩小新请求范围，除非本次用户明确选择该任务。
- 所有越界路径和高风险动作仍按 action policy 拒绝/请求审批。
- 派生进度快照或模型阶段判断错误不影响权限决定。
- ordinary tests 通过且 v1/v2 阶段/权限回归不变。

## 9. 审阅确认

- 是否确认 plan_only 是**请求级、非粘滞**，普通明确实现请求默认 execute？本稿建议是。
- 是否确认 `draft/plan_dirty`、L2 stale、缺文档只作普通模式提示，不再拦源码写入？本稿建议是。
- 是否确认项目模板只影响制品要求，不影响普通写入许可？本稿建议是。
- `context_switch._bind_ordinary_light_entry()` 失败不再强制 confirmed 的安全修正：建议与本次一并做，但它是独立行为修正，是否并入本次实现？

---

**审阅门**：本稿是架构设计草案，未编写实现计划或生产代码。待用户审阅并确认 §9，再进入写计划/TDD。