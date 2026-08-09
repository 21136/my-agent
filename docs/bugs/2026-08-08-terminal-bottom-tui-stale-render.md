# BUG-028 · Terminal Bottom TUI 半截渲染 / 无法滚动复制

- **日期**：2026-08-08
- **状态**：fixed（代码已合入工作树；S-572 手工待复验）
- **面**：Terminal Bottom TUI（`terminal_app.py`）

## 现象

- 思考/答案分段出现，有时停在半截
- 须再发一条消息后，上一轮才刷完整、才能滚动
- 无法顺畅复制；方向键不滚动 transcript

## 根因

1. Agent 在 worker 线程直接改 `TextArea` Buffer；先前误用 **不存在的** `Application.call_from_executor`（prompt_toolkit 3.x），更新仍在 worker 上跑 → 半截画面直到下次按键
2. `wrap_lines=True` 时 Window **强制光标行可见**，且 `vertical_scroll` 是文档行号；只改 scroll 会被下一帧覆盖 → 只能滑到一半
3. **回合结束时序错误**：CLI 在 `_run_agent_turn()` 的 `finally` 中先触发 `console.end_turn()`，而最终助手块是在返回后才由 `assistant_output_fn()` 写入；贴底/flush 发生在最后一块之前，下一次输入触发重绘才显示完整
4. 复制依赖冷门快捷键；`Ctrl+C` 一律退出/打断

## 修复

- `_schedule_ui` → `app.loop.call_soon_threadsafe`；loop 不可用时 deferred，禁止 worker 直改 Buffer
- 回合 `working`：`begin_turn_output` → 默认跟尾，但允许用户上滑暂停跟尾
- 回合 `idle`：在最终助手块写入后调用 `end_turn_output` → flush + 仅在跟尾时贴底
- CLI 将 `console.end_turn()` 移到 `assistant_output_fn()` 之后，保证最终助手块先进入 transcript
- 滚动改为移动 transcript **cursor 行**；有选区时 `Ctrl+C` 复制；`Ctrl+O` 复制全文
- 默认保持 `LAYOUT=bottom`（欢迎 + 钉底输入）

## 验证

- `pytest tests/test_terminal_app.py …` 
- 手工：关旧窗 → `start-terminal.bat` → 应见「原生」提示；输出中可滚轮、拖选、Ctrl+C
