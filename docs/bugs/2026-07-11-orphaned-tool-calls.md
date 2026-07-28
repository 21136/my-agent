# BUG-005：会话历史残缺 tool 回复导致 LLM 400

- **日期**：2026-07-11
- **发现于**：桌面壳续聊同一 session
- **状态**：fixed

---

## 现象

```text
llm error: An assistant message with 'tool_calls' must be followed by tool messages responding to each 'tool_call_id'. (insufficient tool messages following tool_calls message)
```

常见于：上一轮 Agent 已保存「带 `tool_calls` 的 assistant 消息」，但工具未跑完（确认中断、sidecar 崩溃、旧 bug）就又发了新消息。

## 根因

`agent.py` 在调用工具**之前**就把 assistant + `tool_calls` 写入 `messages.jsonl`。若后续未写入对应 `role: tool` 消息，历史不符合 OpenAI/DeepSeek API 要求。

示例（`20260711-d3d654c3`）：

1. assistant 请求 `run_evolved` → `write_evolve`（待确认）
2. 未产生 tool 回复
3. 用户又发了多条新 user 消息

## 修复

| 文件 | 改动 |
|------|------|
| `context.py` | `repair_orphaned_tool_calls()`：为缺失的 `tool_call_id` 补占位 tool 消息 |
| `context.py` | `build_llm_messages()` 发送前自动 repair |
| `session.py` | `Session.load()` 时 repair 并回写 `messages.jsonl` |

占位内容：`tool call did not complete (session recovered)`。

## 验证

```powershell
Set-Location agent-core
python context.py
# [PASS] repair_orphaned_tool_calls inserts missing tool replies
```

重启桌面应用后续聊同一 session 应不再报上述错误。

## 预防

- 长期可考虑：仅在工具全部落盘后再持久化 assistant `tool_calls`
- 确认 / sidecar 稳定性减少中断（见 BUG-002、BUG-003）
