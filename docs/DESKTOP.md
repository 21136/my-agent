# Desktop 当前界面入口

> 当前实现导航；UI 细节以源码为准。本页不再保留早期阶段卡、任务侧栏或教科书式项目流程路线。

桌面主入口使用 unified shell。主要职责包括会话与项目切换、聊天和工具过程展示、确认交互、项目材料查看、终端/服务入口，以及稳定/实验运行车道的清晰区分。具体功能是否可用需核对当前构建与代码。

## 代码入口

- Electron 生命周期与窗口：`desktop/electron/main.ts`、`desktop/electron/preload.ts`
- WebSocket/API：`desktop/src/api/ws.ts`
- unified shell：`desktop/src/shells/unified/`
- 项目面板与文档阅读：`desktop/src/shells/unified/project-panel.ts`、`desktop/src/shells/unified/doc-reading.ts`
- 执行状态展示：`desktop/src/shells/chat-state.ts`

## UI 与安全边界

- 工具确认和执行状态展示必须反映后端实际事件；界面提示不能代替 Executor 的权限判断。
- `PROJECT.md`、`DESIGN.md`、`VERIFY.md` 等是项目材料入口；旧文件可以继续显示，但其状态不证明功能已实现或验证通过。
- 稳定/实验运行规则见 [SANDBOX-EVOLUTION](./SANDBOX-EVOLUTION.md)；发布与恢复约束见 [RELEASE-DEPLOY-ROLLBACK](./RELEASE-DEPLOY-ROLLBACK.md)。
