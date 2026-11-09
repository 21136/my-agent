# Workbench UI 当前入口

> 当前实现导航，不是待办路线图或旧阶段计划。

工作台的主要 UI 由 Desktop unified shell 提供。页面布局、会话交互、项目材料入口和执行态展示以 `desktop/src/shells/unified/` 为准。后端授权仍由 Agent/Executor 执行链负责，界面卡片和提案本身不是授权。

- 主入口与布局：`desktop/src/shells/unified/index.ts`
- 项目导航/材料面板：`desktop/src/shells/unified/project-panel.ts`
- 文档阅读：`desktop/src/shells/unified/doc-reading.ts`
- 会话与执行状态：`desktop/src/shells/chat-state.ts`
- shell/API 协议：`desktop/src/api/ws.ts`
- 当前工作概况与事项控制：`desktop/src/shells/unified/current-work-overview.ts` · `agent-core/work_records_overview_api.py` · `agent-core/work_records.py`

**当前工作 UI**：顶栏 pill 在主聊天壳内以**右侧浮层抽屉**展开（概况 / 进行中 / 调度），叠在完整宽度对话之上（轻微遮罩），不 50/50 挤窄主聊区、不替换整页、不隐藏 composer；对话仍为一级表面。**调度**分区是「选择项目来源 → 逐项只读授权 → 只读 `schedule_preview` → **按预览启动**（`work-records.schedule.dispatch` · 至多两个不同项目并行）」；候选预填任务与建议相对路径，仍须逐项确认。不是把多项目工作塞回排队/等待上下文，也不是宣称全自动调度器。

“当前工作”仍是当前会话的概况，不是跨所有项目会话的全局视图。所选项目统一历史是逐页、逐次授权的有限来源视图，不应解读为全局完整历史。桌面端会核验分页连续性、来源会话/修订和结果身份；后端跨 Session 检查属于乐观校验，不是跨 Session 原子快照，因此并发来源变更仍可能造成页面间不一致，不保证多页处于同一时刻快照。事项控制会写入带 CAS/幂等身份的当前会话记录；排队/等待/停止等控制元数据不会自动启动 worker，运行中的任务必须通过 host 的实际取消链结束。当前尚不构成完整的跨工作区调度器或重启恢复工作台。

稳定与实验 lane 见 [SANDBOX-EVOLUTION](./SANDBOX-EVOLUTION.md)。本页不保留旧 PlanAgent 队列、TASKS 采纳流程或历史 UI milestone。

## 小魔王空间 · 该显 / 该藏（Companion home SHOW/HIDE）

主界面 **小魔王空间** = unified shell、`mainFocus=chat`、无单项目写租约（`companion_home_unbound_desktop`）。多项目编排见 [MULTI-PROJECT-ORCHESTRATION](./MULTI-PROJECT-ORCHESTRATION.md)；本节是 **UI 表面规则**，实现与测试须一致，勿另立冲突产品文案。

| 规则 | 该显（SHOW） | 该藏（HIDE） |
|------|-------------|-------------|
| 普通授权确认 | **唯一**紧凑确认条：输入区 composer dock（标题 + 短动作 + 同意/拒绝；详情折叠「查看详情 / 查看完整披露」） | 聊天内第二张可点确认卡（`需要确认` 大卡）、全屏技术披露弹窗 |
| 进度与失败 | **当前工作 / 工作动态 / 状态栏**（等待确认…、进行中、可读失败） | 把进度刷进聊天流、系统「已保存」、编排 preamble、`turn.notice` 确认引导 |
| 回合收口 | 对用户有用的一两句摘要（做了什么、该开什么） | 「已在主界面创建项目 …(未换线)」「需要在当前小魔王空间确认…」「\[Goal\] 多项目…」 |
| Worker 证据 | 当前工作卡里可读主机证据（回合已结束 + 写/读回执；合法 JSON 不得落成 opaque「机器回执未通过校验」） | 直播多项目/失败时 spontaneous「历史快照」`work-card.present` 进主聊（仅 registry / 当前工作） |
| 确认反馈 | 终端反馈「已同意 / 已拒绝」后 **收起** composer 确认条 | 原始内部 id、工具名、write-lease/session 黑话；点确认却只提示「请点最新一张工具确认卡」 |
| 非目标 | — | 不伪造 worker 验收层「已完成」；不把伴侣空间绑死到单项目 |

**代码锚点（改规则时同步改测试）**

- Desktop 过滤与 composer 独占：`desktop/src/shells/unified/system-notice-copy.ts`、`desktop/src/shells/chat-state.ts`（`companionHomeComposerOwnsOrdinaryConfirm`）、`desktop/src/shells/unified/index.ts`（`syncPendingConfirmComposerHint`）
- 工作卡不进主聊：`desktop/src/shells/unified/work-card-presentation.ts`（`companionHomeDefersWorkCardChatPresentation`）
- Server 侧 HIDE 与忙碌文案：`agent-core/companion_home_surface.py`、`agent-core/companion_home_chat.py`
- 回归：`desktop/tests/companion-home-chat-surface.test.mjs`、`desktop/tests/chat-confirm-surface.test.mjs`、`agent-core/tests/test_companion_home_multi_project_flow_integration.py`

