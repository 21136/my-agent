# BUG-007：聊天框输入「新会话」后一直「处理中…」

- **日期**：2026-07-13
- **发现于**：Electron 桌面壳 daily / grow / pet（聊天输入 `user.message`）
- **状态**：fixed

## 现象

用户在聊天框输入 `新会话`（或 `new`）后，状态栏一直显示「处理中…」，可持续数十分钟。后端 sidecar 进程正常，但聊天区不刷新为新会话，状态不回到「就绪」。

与 BUG-006 症状相似，但 **WebSocket 未被死锁**：`TURN_LOCK` 已释放，后端 `create_new()` 已执行完毕。

## 根因

1. **前端**：`composer-attachments.ts` 发送时 `pushUserMessage()` → `beginTurnActivity()`，将 `turnActive` 设为 `true`。
2. **后端**：`新会话` 是 REPL 元命令，`handle_line` 瞬间完成；`_print_session_banner()` 输出以 `--- session ` 开头，被 `WsBridge.output_fn` 静默丢弃。
3. **协议缺口**：`user.message` 路径在 `_run_line` 后 **未** 调用 `emit_session_state()`（`command` 类型才有）。无 `session.banner` / `session.history` / `assistant.done` 推送。
4. **前端状态**：`chat-state.ts` 收到 `session.banner` / `session.history` 时未 `resetTurnActivity()`，即使未来有推送也无法清除忙碌态。

## 修复

- **`agent-core/server.py`**：`_repl_refreshes_session_state()`；`user.message` 处理完 `新会话` / `换主题` / `压缩` 等元命令后补 `emit_session_state()`。
- **`desktop/src/shells/chat-state.ts`**：`session.banner` / `session.history` 时 `resetTurnActivity()` 并清除 confirm 叠加层。

## 验证

```powershell
Set-Location agent-core
python server.py --demo
```

应含 `[PASS] T-904a: user.message 新会话 emits session.banner + session.history`。

桌面：重启 `start-desktop.bat` → 聊天框输入 `新会话` → 1 秒内状态回「就绪」，聊天区清空。

## 预防

- REPL 元命令若会替换会话/历史，**必须** `emit_session_state`，不能依赖 `_print_session_banner` 文本。
- 凡 `pushUserMessage` 开启的回合，须有对称的结束事件（`assistant.done` / `session.history` / `error` / `resetTurnActivity`）。
- `DESKTOP.md` §5：`user.message` 与 `command` 对元命令的行为须一致。
