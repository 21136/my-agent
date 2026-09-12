# 交互终端会话 UI

> 版本 0.2.0 · 2026-09-11 · 状态：M0/M1 已实现，M2 待评估
> 关联：`interactive_terminal` · [AGENT-TOOL-EXPERIENCE.md](./AGENT-TOOL-EXPERIENCE.md) · [DESKTOP-CHROME.md](./DESKTOP-CHROME.md) · [TASKS.md](./TASKS.md)

## 0. 结论

`interactive_terminal` 需要专门的 UI 可见性入口。这不是装饰性优化，而是持久进程的基本信任条件：用户必须能够确认是否仍有终端在运行、它属于哪个目录、最近是否有活动，以及进程退出或丢失的原因。

现有 `Services` 面板不能直接承担这个职责。它消费 `services.*` 消息并读取 `data/services/`，面向 `run_service` 的服务登记、端口和日志；交互终端使用独立的 `data/interactive-terminals/<session_id>/` 状态与 PTY 输出。两者可以共享侧栏的视觉语言，但不能混用数据模型或把终端状态伪装成服务状态。

## 1. 目标与边界

### 1.1 目标

- 应用启动、重连、切换项目后，用户都能看到当前终端会话总数。
- 每个会话至少显示命令、工作目录、状态、最后活动时间和退出原因。
- 用户可以查看最近输出，并知道输出是否被截断或游标已重置。
- 会话退出、关闭、丢失和宿主不可用必须使用不同状态表达。
- 用户可以从 UI 发起关闭；关闭必须经过现有 executor/确认管线，不绕过权限边界。
- UI 不依赖当前聊天是否仍在显示对应的工具调用。

### 1.2 非目标

- 第一版不实现完整 xterm、鼠标协议、全屏 TUI 重绘或键盘映射。
- 第一版不允许 UI 直接写 PTY stdin；输入仍由 Agent 通过 `interactive_terminal.input` 完成。
- 不把终端会话合并进 `run_service` 的 PID、端口或 ready 语义。
- 不因为 UI 断线就关闭终端，也不因为刷新失败就把运行中的会话标成已退出。

## 2. 用户心智模型

用户看到的是“终端会话”，不是工具调用记录：

```text
项目 / 工作台
  ├─ 当前任务与进度
  ├─ 终端会话  2 个运行中
  │    ├─ running  python -i       workspace/demo
  │    └─ exited   npm test        workspace/demo
  └─ Services    1 个运行中
```

终端会话属于 agent 运行时，不属于某一条聊天消息。聊天消息可以提供“由哪个工具启动”的来源链接，但不能作为会话唯一入口。

## 3. 状态契约

### 3.1 状态来源

状态真源仍是 `interactive_terminal` 的 `state.json`，但 Desktop 不直接读文件。Server 侧增加只读查询适配层，由同一套路径解析和进程存活检查生成 UI snapshot。状态判断顺序如下：

1. 读取结构化 state；
2. 对 `starting/running` 校验 worker PID；
3. 必要时执行已有的 orphan refresh 和 PTY PID 清理；
4. 返回明确的终态和 `reason`；
5. 输出读取使用逻辑 byte cursor，不用时间戳猜测增量。

### 3.2 UI 状态映射

| 后端状态 | UI 文案 | 视觉 | 可用动作 |
|---|---|---|---|
| `starting` | 正在启动 | 中性进行中 | 查看详情、关闭 |
| `running` | 运行中 | 绿色状态点 | 查看输出、关闭 |
| `closed` | 已关闭 | 中性 | 查看输出、清除记录 |
| `exited` | 已退出 | 中性 | 查看输出、清除记录 |
| `lost` | 会话已丢失 | 警示色 | 查看原因、清除记录 |
| `orphaned` | 宿主已断开 | 警示色 | 查看原因、清除记录 |
| `unsupported` | 当前环境不支持 | 警示色 | 查看安装/环境提示 |
| `failed` | 启动失败 | 错误色 | 查看原因、重试 |

`alive=true` 只允许在 `starting/running` 时显示。UI 不得根据“最后一次工具调用成功”继续显示运行中。

### 3.3 建议的传输对象

```json
{
  "session_id": "it-0123456789abcdef",
  "command": "python -i",
  "cwd": "workspace/demo",
  "state": "running",
  "alive": true,
  "exit_code": null,
  "signal": null,
  "reason": null,
  "created_at": "2026-09-08T08:00:00+00:00",
  "last_activity_at": "2026-09-08T08:01:00+00:00",
  "output_bytes": 128,
  "output_start": 0
}
```

建议增加以下 Server 消息，命名可在编码时与现有 `services.*` 风格统一：

- `terminal.list` -> `terminal.list.done`：返回所有会话 snapshot；
- `terminal.output` -> `terminal.output.done`：按 `session_id + cursor + max_chars` 返回增量输出；
- `terminal.state`：工具调用、定时刷新或 orphan refresh 后推送变化；
- `terminal.close` -> `terminal.close.done`：只负责转发关闭请求，实际执行仍走工具/确认管线。

输出响应必须保留 `cursor`、`next_cursor`、`cursor_reset`、`truncated`，不能只返回一段不可续读的字符串。

### 3.4 Agent 回合与终端会话不是同一个生命周期

侧栏同时可能观察两类不同对象，二者不能互相推断：

| 对象 | 事件 / 真源 | 结束含义 |
|---|---|---|
| Agent 回合或狂奔续接 | `execution.state`，由 `WsBridge` 内存生命周期拥有 | `settled`、`paused` 或 `failed` 只表示本次 Agent 执行已收口 |
| 持久交互终端 | `terminal.list.done` / `terminal.state`，真源是 `state.json` 和 worker 存活检查 | `closed`、`exited`、`lost` 或 `orphaned` 才表示终端会话本身结束或失联 |

`execution.state` 的 `queued`、`running`、`stopping` 必须让聊天和回合状态保持可见；其中 `stopping` 不是终态，只有收到终态快照后才允许恢复“可继续输入”。`run_id` 标识一次 Agent 执行，`sequence` 单调递增，用于丢弃乱序或过期事件。它们不替代交互终端的 `session_id`。

因此，Agent 回合结束后，用户启动的 `python -i`、开发服务器或 REPL 仍可以继续运行；相反，终端列表刷新失败也不能把当前 Agent 回合标成已停止。详情抽屉必须分别显示两套状态，并在来源处标明“回合执行”或“终端会话”。

## 4. Desktop 信息架构

### 4.1 侧栏入口

在项目侧栏中将终端作为独立折叠区，位置建议在当前态势之后、Services 之前：

```text
当前态势
本回合
终端会话 · 2 个运行中 · 1 个已退出       [展开]
Services · 1 个运行中                    [展开]
```

入口在有会话时必须保留，即使当前没有项目绑定。无会话时显示一行空态，不制造大卡片：`暂无终端会话`。

标题栏只显示运行中数量；存在 `lost/orphaned/failed` 时追加一个警示计数。已退出记录可以保留在展开列表中，但不能混入运行中计数。

### 4.2 会话行

每行固定三层信息，保证窄侧栏不发生布局跳动：

```text
● 运行中   python -i
            workspace/demo · 刚刚活动
```

- 第一行：状态点、状态文案、命令摘要；命令过长中间截断。
- 第二行：相对工作目录、最后活动时间。
- 右侧：详情入口和关闭图标按钮；关闭按钮提供 tooltip 和无障碍名称。
- 不在列表中显示完整环境变量、秘密参数或整段输出。

### 4.3 详情抽屉

点击会话行打开右侧抽屉或主区覆盖面板，内容顺序固定为：

1. 状态标题、命令、工作目录；
2. 创建时间、最后活动、退出码/信号/原因；
3. 最近输出，使用等宽字体和独立滚动区；
4. 输出刷新、复制可见输出、关闭会话；
5. `lost/orphaned` 的恢复说明。

第一版只读输出。若未来加入 UI 输入，必须增加输入焦点、回显、Ctrl-C、EOF、粘贴敏感内容提示和并发写入顺序设计，不能把普通文本框直接接到 PTY。

## 5. 刷新与断线策略

### 5.1 首版策略

- WebSocket 建立后立即请求一次 `terminal.list`。
- WebSocket 建立或重连时，先同步终端列表；回合侧的 `execution.state` 也必须按 `run_id + sequence` 处理，但不参与终端 `alive` 判断。
- 收到 `tool.end`、`tool.progress` 或 `terminal.state` 后触发轻量刷新。
- 侧栏展开时每 2 秒刷新一次；折叠时只保留较低频的总数刷新。
- WebSocket 重连成功后先拉全量 snapshot，再恢复当前详情抽屉的输出 cursor。
- 输出拉取失败只显示“输出暂时不可用”，不改变会话状态。

后续可以改为 Server 主动推送，但不能省略重连后的全量同步。UI 状态必须能从零恢复。

### 5.2 过期与冲突

- snapshot 带 `last_activity_at`，前端按服务端时间解析，不用本机时间直接判断存活。
- 新 snapshot 的 `session_id` 相同但状态更晚时覆盖旧状态；旧响应不得回写新状态。
- 详情输出响应必须校验 `session_id` 和请求 cursor，防止切换会话时串流。
- `lost` 不是“暂时没刷新到”，不能自动改回 `running`；只有新的合法 start 才产生新的 session id。

## 6. 关闭与权限

- 关闭运行中会话是有副作用的操作，使用确认卡，文案包含命令和工作目录。
- `close(force=false)` 是默认动作；强制关闭必须是二次明确动作。
- 狂奔模式不得覆盖终端关闭确认，除非已有明确的停止授权语义；暂停狂奔时应可从同一入口关闭当前终端。
- UI 只发送受支持的 session action，不直接操作 `state.json`、`output.log` 或 PID。
- 终端输出按现有上限返回；界面不得为了“显示完整”自行读取无限日志。

## 7. 实施顺序

### M0：可见性

1. Server 增加 `terminal.list`，复用 `interactive_terminal` 的状态刷新逻辑。
2. Desktop `ws.ts` 增加类型、请求方法和事件分支。
3. `project-panel.ts` 增加独立终端会话折叠区、运行数和状态行。
4. `index.ts` 接入初始化刷新、重连刷新和工具事件触发刷新。
5. 增加状态映射、空态、异常态和窄窗口布局测试。

当前实现：服务端通过 `terminal_api` 读取 `interactive_terminal` 的列表快照；Desktop 在项目侧栏显示独立的终端会话区，并在连接、重连、工具结束和定时轮询时同步。展开时每 2 秒轮询，折叠时每 10 秒轮询；列表请求带 `request_id`，旧响应不能覆盖新请求。Windows worker 存活检查使用原生进程退出码查询，避免 `os.kill(pid, 0)` 的 WinError 87 将真实会话误报为 `lost`。

M1 已实现：点击会话可打开详情，按逻辑 byte cursor 拉取增量输出，显示 cursor 重置/输出截断/退出原因，支持复制当前可见输出；关闭操作经现有 `WsBridge.confirm_fn` 确认管线执行，完成后自动刷新列表。`terminal.output`、`terminal.close` 均带 `request_id`，旧详情响应不能覆盖当前会话。

### M1：可检查性（已实现）

1. 增加详情抽屉和输出 cursor 拉取。
2. 增加输出刷新、复制可见输出和退出原因展示。
3. 增加关闭确认和关闭后状态刷新。
4. 已增加 server restart、worker lost、输出截断和多会话隔离的协议/回归覆盖；真实 PTY 已验收输出读取、确认关闭和 `closed/alive=false` 终态。

### M2：深度交互

只有 M0/M1 稳定后才评估 UI 直接输入、终端尺寸、ANSI 处理和全屏 TUI。此阶段不应阻塞“用户知道是否还有终端在跑”这一核心需求。

## 8. 验收标准

| ID | 场景 | 通过标准 |
|---|---|---|
| S-6224-a | 启动一个终端 | 2 秒内侧栏出现会话和 `running` 状态，不依赖聊天滚动位置 |
| S-6224-b | 同时启动多个终端 | 运行数准确；命令、目录和行内容不串；列表不跳动 |
| S-6224-c | 终端自然退出 | 变为 `exited`，显示退出码，最近输出仍可查 |
| S-6224-d | worker 丢失 | 显示 `lost/orphaned` 和原因，不能继续显示运行中 |
| S-6224-e | WebSocket 重连 | 全量列表恢复；详情输出从正确 cursor 继续，不重复或丢失已保存内容 |
| S-6224-f | 关闭终端 | 有确认；关闭后变为 `closed`，不会误杀旁邻会话 |
| S-6224-g | 无会话空态 | 侧栏显示轻量空态，不留下旧会话幽灵记录 |
| S-6224-h | 权限回归 | UI 关闭操作不绕过 executor、项目范围、宿主目录和狂奔确认规则 |

完成 M0 后，用户至少能够回答三个问题：现在有没有终端在跑、它跑在哪里、它最近是否有活动。完成 M1 后，还能回答它为什么退出以及最近输出是什么。

## 9. 关联改动

- 后端：`agent-core/server.py`、新增终端状态 API 适配模块；
- 工具：`evolve/tools/common/interactive_terminal/main.py` 的状态和输出协议保持兼容；
- Desktop：`desktop/src/api/ws.ts`、`desktop/src/shells/unified/index.ts`、`desktop/src/shells/unified/project-panel.ts`、`desktop/src/shells/unified/unified.css`；
- 不修改：`run_service` 的 Services 数据模型，不新增 flat tool proxy。
