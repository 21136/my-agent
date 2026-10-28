# Workbench UI 当前入口

> 当前实现导航，不是待办路线图或旧阶段计划。

工作台的主要 UI 由 Desktop unified shell 提供。页面布局、会话交互、项目材料入口和执行态展示以 `desktop/src/shells/unified/` 为准。后端授权仍由 Agent/Executor 执行链负责，界面卡片和提案本身不是授权。

- 主入口与布局：`desktop/src/shells/unified/index.ts`
- 项目导航/材料面板：`desktop/src/shells/unified/project-panel.ts`
- 文档阅读：`desktop/src/shells/unified/doc-reading.ts`
- 会话与执行状态：`desktop/src/shells/chat-state.ts`
- shell/API 协议：`desktop/src/api/ws.ts`

稳定与实验 lane 见 [SANDBOX-EVOLUTION](./SANDBOX-EVOLUTION.md)。本页不保留旧 PlanAgent 队列、TASKS 采纳流程或历史 UI milestone。
