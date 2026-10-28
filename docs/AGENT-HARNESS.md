# Agent Harness：当前执行链

> 本文仅给源码导航，不复述历史 Phase 路线。安全判断以当前执行代码和专题安全文档为准。

## 一轮请求

1. `agent-core/turn_intent.py` 解析本轮用户意图；项目普通工作将意图传给 `project_mode.py`。
2. `agent-core/agent.py` 组装可用工具与本轮上下文。Skill Registry 提供按请求加载的工作流指导，不授予工具权限。
3. `agent-core/tools/executor.py` 处理调用：校验参数、项目根路径/目标、工具级风险及需要的用户确认，再调度具体工具。
4. 执行状态由 `agent-core/execution_lifecycle.py` 提供；Goal Runtime 在动手回合接入轻量目标与预算状态，不替代工具权限或验证。
5. 工具结果、失败和实际测试结果回到对话；报告须与实际证据相符。

## 核心安全原则

- 工具可见性不是执行授权。请求意图、绑定项目与根目录范围、目标路径和操作风险分别判断。
- 用户确认对应具体受保护操作；Skill、提示词、项目文档和模型输出均不能绕过 Executor。
- 路径/宿主范围与敏感操作见 [写入范围](./WRITE-SCOPE.md)、[主机范围](./HOST-SCOPE.md)、[运行时安全守卫](./RUNTIME-GUARDS.md) 和 [自托管安全](./SELF-HOSTING-SECURITY.md)。
- 只读子代理限制见 `agent-core/subagent.py`；模型选择和授权边界不可由子代理自行提升。
- `TASKS.md`、Phase 编号、Progress Gate 和旧 runaway 文档不构成当前普通执行的真源或通用授权条件。旧项目/兼容 schema 仍可能识别相关字段。
- Goal Runtime P0 不意味着旧 runaway 自动执行、跨进程 Run 恢复、自动发布或完整验收。

## 当前代码入口

`agent-core/agent.py` · `agent-core/turn_intent.py` · `agent-core/project_mode.py` · `agent-core/tools/executor.py` · `agent-core/write_policy.py` · `agent-core/host_scope.py` · `agent-core/execution_lifecycle.py` · `agent-core/goal_runtime.py` · `agent-core/subagent.py` · `agent-core/skill_registry.py`。
