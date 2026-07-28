# BUG-006：发消息后一直「处理中…」无响应

- **日期**：2026-07-11
- **发现于**：Electron 桌面壳 `grow` shell
- **状态**：fixed

## 现象

用户发送消息后，状态栏一直显示「处理中…」，数十分钟无任何 `turn.start`、工具进度或回复。后端 Python 进程仍在，但 WebSocket 不再推送事件。

## 根因

BUG-002 二次修复在 `_handle_incoming` 外包了一层 `TURN_LOCK`，而 `_dispatch` 处理 `user.message` / `command` 时再次 `async with TURN_LOCK`。`asyncio.Lock` 不可重入，同一条协程二次获取即永久死锁。

## 修复

`agent-core/server.py`：`_dispatch` 内对 `user.message` / `command` 去掉内层 `TURN_LOCK`（由 `_handle_incoming` 统一串行化回合）。

## 验证

```powershell
Set-Location agent-core
python server.py --demo
```

重启 `start-desktop.bat` 后发消息 → 状态应变为意图标签（如「先只读探索」）并最终「就绪」。

## 预防

`TURN_LOCK` 只在一处获取；新增 `create_task` 并发路径时检查嵌套锁。
