# Explore 的任务范围

> 当前实现说明。范围提示不授予文件权限，也不等于系统级隔离。

## 默认范围

`agent-core/explore_scope.py::explore_scope_rail` 按当前会话选择任务提示：

| 条件 | rail | 默认调查对象 |
|---|---|---|
| 绑定项目且 `active_shell=project` | `project` | 已绑定的 workspace 项目目录与制品；除非用户点名，不默认阅读 Agent 根目录 `docs/` |
| grow 或工具脚手架回合 | `grow` | `evolve/tools/` 范例、`tool.toml` 与工具源码 |
| 其他普通会话 | `general` | Agent 仓库的 `docs/`、`agent-core/`、`evolve/`；未绑定时不自行调查其他用户项目 |

路径和操作权限仍由工具执行层检查；不能把任务提示当成技术沙箱或越界授权。

## 自动与显式调用

- `agent-core/turn_intent.py::should_spawn_explore_for_turn` 决定内核预调研是否触发。项目模式的历史硬禁用已撤除；`project_explore_autospawn_disabled` 保留兼容签名并返回 False。
- 内核触发经 `agent-core/agent.py` 调用 `build_kernel_auto_explore_task`，不裸传缺少项目范围的用户请求。
- 父 Agent 显式 explore 经 `agent-core/tools/executor.py` 使用同一包装函数，再交给子代理。
- `MY_AGENT_AUTO_EXPLORE=0` 可关闭内核自动触发；这与显式调用、工具权限是不同层次。
- 满预算后的有限续跑与摘要汇总由 `SubagentRunner.run_explore_with_continue` 处理；`build_explore_continue_task` 提醒避开已读路径，不构成无限续跑承诺。

## 主 Agent 如何使用结果

子代理摘要不是父 Agent 已读源码的证明。摘要截断或缺少关键事实时，按报告给出的具体路径补读，再决定修改或验证，不把研究报告当成验收通过。

相关入口：[主 Agent 与子代理](./AGENT-PARENT-ORCHESTRATION.md)、[执行链](./AGENT-HARNESS.md)、[源码地图](./MAP.md)。
