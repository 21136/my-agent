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
3. 复制依赖冷门快捷键；`Ctrl+C` 一律退出/打断

## 修复

- `_schedule_ui` → `app.loop.call_soon_threadsafe`（对齐官方 `dialogs.py` log_text）
- 滚动改为移动 transcript **cursor 行**；回合结束 `flush_pending`
- 有选区时 `Ctrl+C` 复制；`Ctrl+O` 复制全文；Windows 默认关 `mouse_support`
- 默认保持 `LAYOUT=bottom`（欢迎 + 钉底输入）

## 验证

- `pytest tests/test_terminal_app.py …` 
- 手工：关旧窗 → `start-terminal.bat` → 应见「原生」提示；输出中可滚轮、拖选、Ctrl+C
