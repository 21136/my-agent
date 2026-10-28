# 执行可观测

桌面/Terminal 的执行态是后端执行生命周期及工具事件的视图，不是独立权限或项目进度真源。

## 实现入口

- `agent-core/execution_lifecycle.py`：一次进程内执行的 run id、状态序列、停止请求和收口结果。
- `agent-core/server.py`：执行桥接与事件发布。
- `desktop/src/shells/chat-state.ts`、`desktop/src/api/ws.ts`：前端事件消费与展示。
- 长驻服务由 `agent-core/services_api.py` 管理，其服务状态与单回合结束状态不可混为一谈。

事件表示可观察到的状态，不自动证明项目测试、发布或功能验收通过。旧的 armed-task/Progress Gate 面板不是当前普通项目执行的必需 UI。
