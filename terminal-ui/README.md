# terminal-ui

Terminal Ink UI for my-agent — **Scheme B** semantic colors.

设计真源：[TERMINAL-MODE.md §6.4.9 / §6.6](../docs/TERMINAL-MODE.md) · 浏览器对照：[terminal-color-preview.html](../docs/demos/terminal-color-preview.html)

## Quick start

```powershell
cd terminal-ui
npm install
npm run build    # 可选 · tsc → dist/
npm run demo     # Windows Terminal · true-color TTY
npm run test     # IT-590b reducer 单测
npm run fixture  # fixture JSONL → Ink 渲染（demo-turn.jsonl）
```

`npm run demo` 渲染静态两轮对话（用户 / 思考 / 助手 / 提醒 + 底栏 `◐ run_command`），用于对照 preview 配色。**尚未**连接 Python agent。

## Structure

```text
src/
  theme/tokens.ts       # Scheme B 色值真源（Ink + 文档引用）
  types.ts              # TerminalBlock union
  reduce/events.ts      # JSONL event → TerminalBlock[] reducer（T-5720b）
  blocks/               # User · Thinking · Assistant · Notice
  components/           # WelcomeCompact · StatusBar · TurnSep
  repl.tsx              # <Repl> 布局
  demo.tsx              # npm run demo
  cli.tsx               # JSONL 入口 · fixture 回放
fixtures/               # *.jsonl 验收夹具（IT-590b）
tests/                  # reducer 单测
```

## Ink 约束（必读）

见 [TERMINAL-MODE §6.6.1](../docs/TERMINAL-MODE.md#661-ink-落地约束已验证--wt)：

- 前景色：`<Text color="#RRGGBB">` ✅
- 行内 code 底：`backgroundColor="#422006"`（6 位 hex）✅
- **禁止** `backgroundColor` 使用 8 位 alpha（如 `#fb923c1a`）— 终端会当成实心色块
- 用户 / 提醒块：用 `│` 左边线 + 前景色，不用浅底
- `Box` 无 `backgroundColor` prop

`*Bg` token（`userBg` / `noticeBg` / `thinkingBg`）保留给 HTML preview，Ink 实现勿引用。

## 编码分期

见 [TERMINAL-MODE §6.6.2](../docs/TERMINAL-MODE.md#662-编码分期m2--一动一停)：

| 阶段 | 状态 | 任务 |
|------|------|------|
| 0 脚手架 + demo | **done** | T-5720 · T-5721 |
| 1 JSONL + reducer | **done** | T-5720b |
| 2 Pipe 单向 | **done** | T-5722 M0 |
| 3 交互闭环 | **done** | T-5722 M1 · IT-592b |
| 4 Markdown + chat-state | todo | T-5723 |
| 5 60fps | **done** | T-5724 · IT-594 |
| 6 行级 diff | defer | T-5725 |

## 纪律

- **TM-Q17**：transcript **无**工具行；工具活动仅 `<StatusBar>`（`◐` + 紫工具名）
- Python 集成（T-5722 M0 **done**）：`cli_terminal.py` spawn `node terminal-ui/dist/cli.js`；`MY_AGENT_TERMINAL_UI=legacy` 保留 prompt_toolkit

## 任务对照

| ID | 状态 |
|----|------|
| T-5720 M0 | done — 脚手架 + 静态 demo |
| T-5720b | **done** — JSONL + 流式 reducer |
| T-5721 | done — 块组件 + theme |
| T-5722 M0 | **done** — Python 单向 pipe |
| T-5722 M1 | **done** — Ink 输入回传、confirm 往返、`/clear` |
| T-5724 | **done** — 60fps perf (throttle · hot path · bounded transcript window) |
