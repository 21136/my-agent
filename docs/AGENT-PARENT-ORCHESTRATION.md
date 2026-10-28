# 主 Agent 与子代理

> 当前实现导航；不包含已移除的 plan/adoption 队列或旧 PlanAgent 权限模型。

`agent-core/subagent.py` 实现请求内的子代理调度与隔离上下文。当前类型包括 explore、checker、plan、review 与 terminal_plan；具体可用角色随调用入口和配置而定。只读角色使用受限工具集，子代理结果是供主 Agent 判断的报告，不直接构成用户确认、发布批准或测试通过证据。

## 执行边界

- 主 Agent 负责解释用户请求、调用工具并向用户报告结果。
- 子代理的提示词与工具集限制只收窄能力，不提升主会话权限。
- 项目根路径、工具风险、确认及宿主范围仍由主执行链检查。
- 旧的 `plan_partner` 采纳队列、`PlanAgent` 写盘流程与 task adoption 约定不是当前入口；不要据历史文档恢复。

## 项目 explore 的当前范围

项目模式不再一律禁止内核 auto explore。内核预调研和父 Agent 显式调用的 explore 均通过 `build_kernel_auto_explore_task` 包装：已绑定项目默认查看该项目目录与制品，不默认翻 Agent 根目录文档。触发条件和环境开关仍生效，允许触发不等于每次都启动。

`scope_rail` 是任务范围提示，不是操作系统沙箱或额外授权；实际工具权限仍由执行器检查。完整范围说明见 [EXPLORE-SCOPE-RAILS](./EXPLORE-SCOPE-RAILS.md)。

## 源码

- `agent-core/subagent.py`：角色、调度、工具集、结果汇总。
- `agent-core/agent.py`、`agent-core/turn_intent.py`：请求侧触发与主回合集成。
- `agent-core/explore_scope.py`：自动和显式 explore 的任务范围包装。
- `agent-core/tools/executor.py`、`agent-core/project_mode.py`：工具执行与项目范围策略。
- [子代理预算说明](./SUBAGENT-BUDGET.md) 仅作辅助；当前限制值以源码/配置为准。

## 相关草案

- 多项目、主 Agent 调度按项目 worker（**未实现**）：[MULTI-PROJECT-ORCHESTRATION](./MULTI-PROJECT-ORCHESTRATION.md)
