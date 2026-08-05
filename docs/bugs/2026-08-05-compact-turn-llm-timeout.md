# BUG-023 · 自动压缩后主循环 LLM 超时（「思考中…」→ 回合超时已停止）

> **日期**：2026-08-05  
> **状态**：**fixed** · 2026-08-05（T-2092～T-2094）  
> **关联**：[RUNTIME.md](../RUNTIME.md) §8.4 · [RUNTIME-GUARDS.md](../RUNTIME-GUARDS.md) G15 · [TURN-CONTROL.md](../TURN-CONTROL.md) §9.3 · `TASKS.md` T-2091～T-2094  
> **触发会话**：`workspace/huiyi` · `20260804-dcef2d2b` · 用户输入「继续」

---

## 1. 现象

1. 长项目编码回合中，工具循环**自动触发** context 压缩（notice：「正在压缩对话摘要…」→「已压缩：摘要 N 条消息…」）。
2. 压缩完成后 UI 进入 **「思考中…」**，可持续 **60～120s+**（截图约 86s）。
3. 最终聊天区只出现 **「回合超时已停止」**；**无** assistant 正文、**无**后续 tool 调用。
4. Token 指示器仍显示远低于上限（例：**49k / 128k**），用户易误判为「不是 context 满」。

与 [BUG-014](./2026-07-13-turn-stall-no-stop.md) 不同：Stop/stall 机制已存在；此处压缩**已成功**，卡死在压缩后的**下一次**主循环 LLM。

---

## 2. 复现条件（高概率）

| 条件 | 说明 |
|------|------|
| 会话 | 项目模式 · 多轮 tool 密集（grep / patch / write / run_command） |
| 阈值 | 估算 `system + messages` ≥ `LLM_CONTEXT_LIMIT × 0.85` → `maybe_auto_compact` |
| 压缩量 | 单次 digest 数十～百余条 message（例：156 条） |
| 模型 | flash + `reasoning_effort=high`（huiyi 会话为 `sophnet-deepseek-v4-flash`） |
| 默认超时 | `LLM_TIMEOUT_SEC=120` |

**临时规避**（实施前）：

1. 压缩完成后若再次卡住 **>2 分钟** → 点 **停止**，再发「继续」（压缩已落盘，通常可续）。
2. 手动 **`压缩`** 或 **`新会话`** 在回合**空闲时**做，避免与长 tool 环叠在同一 turn。
3. 非必要关闭 pro / 高 reasoning；或设 `LLM_TIMEOUT_SEC=300`（治标，见 §6 P2）。

---

## 3. 证据（huiyi · 20260804-dcef2d2b）

| 项 | 值 |
|----|-----|
| `compact_before_index` | **157**（与「摘要 156 条」一致） |
| `digest.md` | §压缩 1 已写入 |
| 压缩后 LLM payload | **193** 条 message · 估 **~55k tokens** |
| tool 正文体积 | 保留 8 轮内 tool content **~106k 字符** |
| 消息尾部 | 停在 `write_text DoctorList` + `read_file InsurancePolicyList` 的 tool 回复；**无**后续 assistant |
| 超时文案 | 仅桌面 **「回合超时已停止」**；**无**墙钟 notice（「回合已超过墙钟限制…」） |

→ 更符合 **`LLMTimeoutError`**（120s httpx 整请求超时），而非 `TURN_WALL_SEC=900` 或 `STALL_WATCHDOG_SEC`。

---

## 4. 根因（因果链）

```text
工具循环每轮 LLM 前
  → should_auto_compact == true
  → turn.notice「正在压缩…」
  → summarize_messages_for_digest（非流式 LLM，可能数十秒）
  → compact 成功 · turn.notice 结果 + 首次压缩教育
  → 立即 build_llm_messages + llm.chat（仍 reasoning_effort=high · 仍带 tools）
  → 高推理 + 大 payload + 120s 整请求超时
  → LLMTimeoutError → finish_reason=timeout
  → UI 统一显示「回合超时已停止」
```

| 层 | 问题 |
|----|------|
| **压缩 × 墙钟** | `TurnWatchdog.pause_wall()` 仅在 `tool.start→tool.end`；**摘要 LLM 不计入暂停**，长压缩吃 turn 墙钟预算 |
| **保留策略** | `CONTEXT_KEEP_TURNS=8` 按**轮数**裁，不按 token；编码回合单轮可含大量 tool 输出 → 压缩后仍 ~55k tokens |
| **超时语义** | `LLMTimeoutError` 与墙钟/stall 均映射 `finish_reason=timeout`；桌面无区分文案 |
| **可取消性** | 摘要走 `llm.chat` 非流式 `post`；用户 Stop 在摘要阶段难以及时打断 |
| **进度事件** | 压缩期间无 `llm.pending` / `assistant.delta`；若启用 stall 看门狗可能误杀（默认关） |

**非根因**：

- 压缩算法失败（digest 已落盘、`compact_before_index` 已前移）
- Token 指示器 bug（49k 为压缩**后** payload，与压缩前 85% 触发不矛盾）
- BUG-014 类 confirm/WS 死锁（无 confirm 卡、sidecar 仍存活）

---

## 5. 代码锚点

| 位置 | 行为 |
|------|------|
| `agent.py` `_run_parent_tool_loop` | 每轮 LLM 前 `maybe_auto_compact` → 再 `llm.chat` |
| `context.py` `summarize_messages_for_digest` | 非流式摘要；无 wall pause |
| `context.py` `compute_compact_split_index` | 仅 `keep_turns` 轮界，不限制 tool 体积 |
| `runtime_guards.py` `pause_wall` | 仅 tool 执行 |
| `agent.py` `except LLMTimeoutError` | `finish_reason=timeout`，无专用 notice |
| `chat-state.ts` `turn.end` | 凡 `timeout` →「回合超时已停止」 |

---

## 6. 修复设计（已决 · 待实施）

### P0 — 必做（T-2092 · T-2093）· **2026-08-05 已落地**

| ID | 内容 | 交付 |
|----|------|------|
| **R1** | 自动/手动压缩的摘要 LLM 期间 **`pause_wall` / `resume_wall`**（与 long tool 同待遇） | `agent.py` 或 `context.compact_context` + `server` 事件桥 |
| **R2** | 摘要 LLM 使用 **独立超时**（建议 `CONTEXT_SUMMARIZE_TIMEOUT_SEC`，默认 **180**）；支持 `cancel_event` | `context.py` · `llm_client.py` |
| **R3** | `LLMTimeoutError` 发 **`turn.notice`**：「LLM 请求超时（Ns）…」；墙钟/stall 保持现有 `notice` 文案 | `agent.py` |
| **R4** | 桌面 **`finish_reason=timeout`** 时：若回合内已有 R3 notice，仍显示「回合超时已停止」；或细分为「LLM 请求超时」/「回合墙钟超时」（二选一，见 T-2093） | `chat-state.ts` · [DESKTOP.md](../DESKTOP.md) |

### P1 — 建议（T-2094）· **2026-08-05 已落地**

| ID | 内容 |
|----|------|
| **R5** | 压缩后若 `estimate_context_tokens` 仍 ≥ 某二级阈值（建议 **70%** limit），对 **tool role 正文** 做 payload 截断或再压一轮 digest（**不**改 `messages.jsonl` 磁盘全文） |
| **R6** | ~~自动压缩后下一跳降 `reasoning_effort`~~ — **不实施**（用户需保持会话 effort，勿静默改档） |
| **R7** | `LLMApiError` 重试耗尽勿映射为 `finish_reason=timeout`（与网络/配额区分） |

### 非目标

- 重写 §8 压缩算法（仍用 digest + K=8）
- 默认启用 `STALL_WATCHDOG_SEC`（慢 pro 误杀风险仍在）
- 拉长 `TURN_WALL_SEC` 替代 task 门（见 [TASK-STOP.md](../TASK-STOP.md) S7）

---

## 7. 验收

| # | 场景 | 通过标准 |
|---|------|----------|
| IT-95 | Mock：compact 摘要 sleep 30s + 主 LLM 正常 | 墙钟不在摘要阶段流逝；turn 不因 900s 误杀 |
| IT-96 | Mock：post-compact 主 LLM 挂起 >120s | `turn.notice` 含「LLM 请求超时」；`turn.end` `finish_reason=timeout` |
| IT-97 | 真实 huiyi 长会话 + 「继续」 | 压缩后能在 120s 内恢复 tool 循环，或给出 R3 明确 notice |
| S-75 | 手工：自动压缩 → 思考中 | 过程块可见「正在压缩…」+ 结果；不应无声 120s 后仅「回合超时已停止」 |

自动化：`tests/test_context_compact_timeout.py`（新建）· 扩 `test_runtime_guards.py`。

---

## 8. 文档同步

- [RUNTIME.md](../RUNTIME.md) §8.4 — 压缩与超时交互  
- [RUNTIME-GUARDS.md](../RUNTIME-GUARDS.md) G15  
- [TURN-CONTROL.md](../TURN-CONTROL.md) §9.3 — `finish_reason=timeout` 分型  
- [BUGS.md](../BUGS.md) 索引 + 速查表  
