# 确认与副作用

具体工具的确认预览、用户决定及执行结果由 Agent/Executor 链路承载。确认策略必须落实在执行层，而不能只依赖提示词、项目文档或 UI 状态。

## 当前权威

- 工具注册与执行：`agent-core/agent.py`、`agent-core/tools/executor.py`
- 普通项目意图/根路径：`agent-core/turn_intent.py`、`agent-core/project_mode.py`
- 文件写入策略：`agent-core/write_policy.py`
- 命令策略：`agent-core/run_command_policy.py`
- 宿主范围：`agent-core/host_scope.py`

工具需要的确认以目标操作与风险为准。用户同意一个具体操作，不自动授权后续、扩大路径或跳过其他保护。UI 收到执行请求或显示完成文案，不代替真实工具结果。

旧的 Phase 门、TASKS 权限、Progress Gate/report_progress 勾选证据与 PlanAgent 采纳队列不是当前普通执行的通用授权链。兼容数据和历史项目文件仍可能存在。
