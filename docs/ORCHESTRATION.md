# 当前编排与执行边界

普通用户请求由 `agent-core/agent.py` 协调模型回合、内置/扁平/evolved 工具与可选子代理。请求意图由 `turn_intent.py` 解析；工具授权、路径与风险确认由 Executor 和专门策略模块判定。

`ExecutionLifecycle` 表示单次进程内运行状态。Goal Runtime 为 P0 轻量目标/预算/状态记录，不是旧 runaway 子系统的恢复或替代；重启不自动复活原 Run。

子代理入口与受限工具集见 `agent-core/subagent.py`。子代理分析结果不等于执行授权、用户确认或真实测试证据。普通项目执行不依赖历史 PlanAgent 采纳队列、Progress Gate 或仓库 `docs/TASKS.md`。

源码锚点：`agent-core/agent.py`、`agent-core/turn_intent.py`、`agent-core/project_mode.py`、`agent-core/tools/executor.py`、`agent-core/execution_lifecycle.py`、`agent-core/goal_runtime.py`、`agent-core/subagent.py`。
