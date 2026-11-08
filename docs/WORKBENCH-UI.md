# Workbench UI 当前入口

> 当前实现导航，不是待办路线图或旧阶段计划。

工作台的主要 UI 由 Desktop unified shell 提供。页面布局、会话交互、项目材料入口和执行态展示以 `desktop/src/shells/unified/` 为准。后端授权仍由 Agent/Executor 执行链负责，界面卡片和提案本身不是授权。

- 主入口与布局：`desktop/src/shells/unified/index.ts`
- 项目导航/材料面板：`desktop/src/shells/unified/project-panel.ts`
- 文档阅读：`desktop/src/shells/unified/doc-reading.ts`
- 会话与执行状态：`desktop/src/shells/chat-state.ts`
- shell/API 协议：`desktop/src/api/ws.ts`
- 当前工作概况与事项控制：`desktop/src/shells/unified/current-work-overview.ts` · `agent-core/work_records_overview_api.py` · `agent-core/work_records.py`

“当前工作”仍是当前会话的概况，不是跨所有项目会话的全局视图。所选项目统一历史是逐页、逐次授权的有限来源视图，不应解读为全局完整历史。桌面端会核验分页连续性、来源会话/修订和结果身份；后端跨 Session 检查属于乐观校验，不是跨 Session 原子快照，因此并发来源变更仍可能造成页面间不一致，不保证多页处于同一时刻快照。事项控制会写入带 CAS/幂等身份的当前会话记录；排队/等待/停止等控制元数据不会自动启动 worker，运行中的任务必须通过 host 的实际取消链结束。当前尚不构成完整的跨工作区调度器或重启恢复工作台。

稳定与实验 lane 见 [SANDBOX-EVOLUTION](./SANDBOX-EVOLUTION.md)。本页不保留旧 PlanAgent 队列、TASKS 采纳流程或历史 UI milestone。
