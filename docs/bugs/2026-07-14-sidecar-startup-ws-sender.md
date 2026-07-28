# BUG-015 / BUG-016：sidecar 启动失败与 WS 断开刷 traceback

- **日期**：2026-07-14
- **发现于**：Electron 桌面壳；`start-desktop.bat` 启动 Python sidecar
- **状态**：**fixed**

---

## BUG-015：sidecar 无法启动（`AgentPaths` 未定义）

### 现象

终端 / `[sidecar]` 输出：

```text
NameError: name 'AgentPaths' is not defined
  File "agent-core/server.py", line 797, in run_server
    paths = AgentPaths.discover()
```

Electron 报：`Failed to start Python sidecar: sidecar exited before ready (code 1)`。

### 根因

`server.py` 在 `run_server()` 与类型标注中大量使用 `AgentPaths`，但顶部缺少 `from paths import AgentPaths`。

文件有 `from __future__ import annotations`，类型标注里的 `AgentPaths` **不会在 import 阶段报错**；只有运行到 `AgentPaths.discover()` 才暴露。

### 修复

`server.py` 增加 `from paths import AgentPaths`（与 `main.py` / `session.py` 等项目约定一致）。

### 验证

```powershell
Set-Location agent-core
python server.py --help
python -c "import server; print('ok')"
```

---

## BUG-016：客户端断开后 `[sidecar] connection handler failed`

### 现象

sidecar 已启动，但控制台周期性出现：

```text
websockets.exceptions.ConnectionClosedOK: received 1005 (no status received [internal])
  File "agent-core/server.py", line 493, in _sender
    await websocket.send(...)
```

常见于 pet ↔ 工作台切换、页面重载、WS 自动重连。

### 根因

1. **`_sender` 未捕获 `ConnectionClosed`**：客户端已断开时仍向 socket 发队列中的 `session.banner` 等事件，异常冒泡为 `connection handler failed`。
2. **多连接共用实例级 `_outbox`**：`WsSessionHandler` 所有连接共享同一队列与 `_sender_task`，pet 断开 + 工作台重连时易竞态。

### 修复

- `_sender` 捕获 `websockets.exceptions.ConnectionClosed` 后安静退出。
- `outbox` / `sender_task` 改为 **每连接局部变量**（`handle()` 内创建），不再挂在 handler 实例上。

### 验证

```powershell
Set-Location agent-core
python -m unittest tests.test_turn_cancel -q
```

手工：启动桌面 → pet 与工作台来回切换 → `[sidecar]` 不应再刷 `connection handler failed` traceback（`libpng iCCP` 警告可忽略）。

---

## 预防

- `server.py` 改动 import 区块时对照项目约定：`from paths import AgentPaths` 不可漏。
- WS 发送循环必须假定 **对端随时断开**；outbox 与 sender 生命周期绑定单连接。
- `from __future__ import annotations` 不能替代运行时 import 检查；涉及 `discover()` 等运行时符号应跑 `python -c "import server"` 或 `server.py --help` 冒烟。
