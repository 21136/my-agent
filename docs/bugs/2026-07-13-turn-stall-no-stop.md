# BUG-014：无 Stop + 长超时导致「思考中」假死

- **日期**：2026-07-13
- **发现于**：grow 壳 · 会话 `20260712-c369be88` · 搭 `npm_exec`
- **状态**：**fixed**（Phase 15 M0 已落地；稳定化验收 S-05 / S-26 / S-28 pass · T-1808-bug-01；[TURN-CONTROL.md](../TURN-CONTROL.md)）

---

## 现象

- 底部状态 **「思考中…」** 或 **「处理中…」** 持续 **10+ 分钟**
- 确认卡显示 **「提交中…」** 长时间不结束
- `data/sessions/.../messages.jsonl` **长时间无新写入**（sidecar 进程仍在，CPU 极低）
- 用户只能 **托盘退出 / 任务管理器杀 python** 恢复

与 BUG-008（confirm 错 ID 空转）不同：Phase 14 修复后仍复现，主因是 **无法打断** + **等待上限过长** + **pro 模型 + 膨胀上下文**。

---

## 时间线（摘要）

| 时刻 | 事件 |
|------|------|
| 13:53+ | `write_evolve` + `content_base64` → padding 错误（123ms） |
| 13:53+ | 改 `write_text` / staging 脚本；多次 sidecar 重启 → `session recovered` 占位 |
| 14:17 | 重启 sidecar；`write_text` `_test.txt` 成功 |
| 14:20 | `run_python` `_staging_writer.py` → `FileNotFoundError`（`workspace/` 前缀） |
| 14:20+ | 最后一轮 `run_python` confirm；UI「思考中…」；磁盘无更新 |

同期：`npm_exec` 未落入 `evolve/`（后在 Cursor 侧直接脚手架解决）。

---

## 根因

### P0 — 无用户可触发的「停止本轮」

- Cursor：生成时 **Stop** 中断 LLM + 结束 agent loop
- my-agent：仅有杀进程；`turn.end` 依赖回合线程自然退出
- 线程阻塞在 `llm.chat`（流式）或 `confirm_fn`（`queue.get`）时 UI 永久 `isWorking`

### P0 — 确认等待 3600s

`server.py` `confirm_fn`：

```python
resp_id, choice = self._confirm_queue.get(timeout=3600)
```

Phase 14 C2 修了 **超时无 `confirm.done`**，但超时仍为 **1 小时**，与产品预期（60–120s）不符。

### P1 — 上下文膨胀

- 助手反复在 `tool_calls.arguments` 内嵌整份 `main.py`
- 会话挂 **coding** → `deepseek-v4-pro` + reasoning → 「思考中…」可极长
- CONFIRM-PIPELINE C8 仅为 prompt 软约束

### P1 — assistant 先于工具落盘

- 中断后 `repair_orphaned_tool_calls` 补占位失败
- 同会话「继续」→ 重试 → 历史更大（BUG-005 类，已有 repair，无 Stop 时仍痛）

---

## 修复方向（Phase 15）

见 [TURN-CONTROL.md](../TURN-CONTROL.md)：

| 决议 | 内容 |
|------|------|
| R1–R3 | `turn.cancel` + Stop 按钮 + `turn.end`/`confirm.done` cancelled |
| R4 | `CONFIRM_TIMEOUT_SEC` 默认 **90s** |
| R5 | LLM SSE 协作 `cancel_event` |
| R6–R7 | 桌面 `resetTurnActivity` ·「已停止」 |

**临时规避**（实施前）：

1. 卡住 **>2 分钟** → 托盘退出重开 + **`新会话`**
2. 搭工具时说清 **`content_workspace_path`**，不要 base64
3. 非必要不用 coding/pro 长推理

---

## 验证（Phase 15 完成后）

- [x] 思考中点 Stop → 3s 内可再输入 — **S-05**（grow · 0.27s `cancelled`）· **S-28**（project/daily/pet ≤0.5s；`test_turn_cancel` 9 OK）
- [x] 确认等待点 Stop → 不永久「提交中…」 — **S-25**（T-1820-10 · confirm 中 `turn.cancel` → `choice=cancelled`）
- [x] confirm 超时自动结束 — **S-26**（T-1820-11 · 默认 90s；缩略 `CONFIRM_TIMEOUT_SEC` 发 `confirm.done` `timeout`）
- [x] `test_turn_cancel.py` PASS — S-25 / S-28 回归 **9 OK**

> 验收路径为 **WS 协议级 smoke**（与桌面 Stop 同协议）；见 [`stabilization-log.md`](../stabilization-log.md) P0 S-05 · T-1820-11 · T-1820-13。
