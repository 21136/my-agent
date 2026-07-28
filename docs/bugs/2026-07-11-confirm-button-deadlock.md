# BUG-002：工具确认「同意 / 拒绝」点击无反应

- **日期**：2026-07-11
- **发现于**：Electron 桌面壳 `grow` shell，工具确认卡片（`write_evolve` 等）
- **状态**：fixed（初版 `_dispatch_inline` 不足；**二次** `asyncio.create_task(_handle_incoming)` 解耦 WS 读循环）

---

## 现象

Agent 请求执行需确认的工具（如 `run_evolved` → `write_evolve`）时，界面弹出「工具确认」卡片，底部有 **同意** / **拒绝** 按钮，状态栏显示「等待确认…」。

用户点击按钮后无任何变化：按钮仍可点，状态不更新，工具不执行也不跳过。

## 根因

WebSocket 消息在 `server.py` 中**串行**处理：

```python
async for raw in websocket:
    await self._dispatch(message, repl, bridge)  # 上一条未完成前不会读下一条
```

用户发消息后，`_dispatch` 在 `TURN_LOCK` 内 `await _run_line()`，工作线程在 `confirm_fn` 里阻塞等待 `confirm.response`。

此时事件循环仍卡在当前 `_dispatch` 上，**无法读取下一条 WebSocket 消息**。用户点击按钮虽发出 `confirm.response`，但服务端永远收不到 → 工作线程永远等 → 界面假死。

这是典型的 **async 事件循环 + 线程内阻塞等待 UI 响应** 死锁，不是 CSS 或按钮未绑定问题。

## 修复

1. **`agent-core/server.py`**（初版）：新增 `_dispatch_inline()` 优先处理 `confirm.response` —— **不足**，读循环仍卡在 `await _dispatch`。
2. **`agent-core/server.py`**（二次）：`asyncio.create_task(_handle_incoming)`，读下一条 WS 消息不再等待回合线程结束。
3. **`desktop/src/shells/grow/index.ts`**：点击后显示「已提交确认，执行中…」。

## 验证

1. 重启桌面应用（`start-desktop.bat`）
2. 触发需确认的工具（如让 Agent 写 evolve 文件）
3. 点击「同意」或「拒绝」→ 应出现「已执行」/「已跳过」，状态回到「就绪」

```powershell
Set-Location agent-core
python server.py --demo
```

## 预防

- 任何「线程阻塞等 UI / 另一条 WS 消息」的路径，都必须在 WebSocket 读循环里走 inline / 优先通道，不能挂在未完成的 `_dispatch` 后面。
- 新增阻塞式 `bridge.*_fn` 时，同步加对应的 inline 消息类型与集成测试。
