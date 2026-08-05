# BUG-024 · 重复 inline_write_max 不熔断（guard 连刷 · 不换策略）

> **日期**：2026-08-05  
> **状态**：**fixed**（T-4242～4243 · IT-98 绿）  
> **关联**：[AGENT-HARNESS.md](../AGENT-HARNESS.md) §7.5 · [EXEC-RELIABILITY.md](../EXEC-RELIABILITY.md) §3.6 · [RUNTIME-GUARDS.md](../RUNTIME-GUARDS.md) G16 · [CONFIRM-PIPELINE.md](../CONFIRM-PIPELINE.md) · `TASKS.md` T-4241～T-4243  
> **触发会话**：`workspace/huiyi` · `20260804-dcef2d2b` · 重写 `DoctorList.vue` / `Layout.vue`  
> **用户决策**：2026-08-05 — 对齐 Cursor 体感；**重复 `inline_write_max` ≥2 → 停 tool + 强制 staging**；文档先行。

---

## 1. 现象

1. 助手用 `write_text` **内联**整文件（例：`DoctorList.vue` ~8943 字符）。
2. executor 拒：`[guard] 内联写入超过 8192 字符（8943），请改用 workspace/_staging + content_workspace_path`。
3. **同一回合内连刷 2～4 条相同 guard**；UI 仍「思考中…」，模型继续 inline `write_text`。
4. 用户预期：**第三次同类错误应换策略**（段内失败预算 / 熔断）— **未触发**。
5. 助手口语：「文件又乱了，直接重写 …」— 仍走 inline 大 `write_text`，再次撞 guard。

与 [BUG-023](./2026-08-05-compact-turn-llm-timeout.md) 可同会话叠加；本 bug 独立：**A 类 validation 不计 P5 预算**。  
上游根因见 [BUG-025](./2026-08-05-patch-file-crlf-corruption.md)（文件先被写乱 → 助手倾向 inline 整文件重写 → 撞本 guard）。

---

## 2. 根因（与 Cursor 对比）

| 层 | my-agent 现状 | Cursor 类行为 |
|----|---------------|---------------|
| 大内容路径 | 9000 字塞进 tool `content` → **8192 guard** | 改 IDE 文件 / patch；少把巨型正文放进 tool JSON |
| 失败分型 | `validation_error` + `inline_write_max` → **A 类** | patch/Apply 失败走短反馈 + 换工具 |
| P5 段内预算 | 仅 **countable** 失败 +1；A 类 **不计** | 无同等 guard；靠轮次上限 + 内部重试 |
| TOOL-RETRY | A 类 **免费重试**，还注入「请修正后重试」 | 用户常看不到每次内部试错 |
| 熔断 G14 | `validation_error` 无 exit_code → **不计指纹** | 同类 patch 失败靠模型换招 |

代码锚点：

- `executor._inline_write_max_guard` → `guard_type: inline_write_max`
- `exec_reliability.classify_failure` → A 类
- `is_circuit_countable_failure` → False
- `agent._is_retryable` → True（继续盲试 inline）

---

## 3. 已决修复（T-4242 · 对齐 Cursor 体感）

### 3.1 规则 **R1** — 重复 inline guard 计数

| 项 | 值 |
|----|-----|
| 计数键 | `guard_type == inline_write_max`（同 execute segment） |
| 阈值 | 默认 **2**（`MY_AGENT_INLINE_WRITE_GUARD_MAX=2`） |
| 第 1 次 | 现有 guard notice + TOOL-RETRY **保留** |
| 第 2 次起 | **不再** free-retry 同一 inline 路径；触发 **R2** |

### 3.2 规则 **R2** — 达阈动作（仿 P5 + 专用文案）

1. `ExecutorSession.inline_write_guard_streak` 达阈 → 置 `inline_write_guard_blocked=True`（本 segment）。
2. agent loop：**`tools_payload = None`**（停 tool，同 P5 `segment_failure_budget_hit`）。
3. 注入 **`role:user` 内核消息**（新常量，**非** core.txt）：

```text
[内核] 内联正文已两次超过 WRITE_INLINE_MAX_CHARS（8192）。
禁止再用 write_text 的 content/content_base64 写大文件。
请：write_text → workspace/_staging/<name>（仅 staging 小文件）→ 再 run_evolved write_text 带 content_workspace_path。
或改用 patch_file 小范围修改。本段请先文字说明再让用户继续。
```

4. `turn.notice`（warn）：`内联写入多次超限，已停止工具；请改用 _staging + content_workspace_path`。
5. **不**把 `inline_write_max` 计入 G14 指纹熔断（避免与 run_command 混指纹）；**独立** streak。

### 3.3 规则 **R3** — TOOL-RETRY 边界

| 场景 | TOOL-RETRY |
|------|------------|
| 第 1 次 `inline_write_max` | **允许**（修正参数 / 改走 staging） |
| 第 2 次同类（streak≥2） | **禁止** free-retry；走 R2 |
| 其它 `validation_error`（非 inline_write_max） | **不变** |

实现：`agent._is_retryable` 或 executor 返回 `details.retry=False` 当 streak 已达阈。

### 3.4 规则 **R4** — UI / notice 去重（可选 · 同 PR 或 T-4244）

- 同 segment 内 **相同** `inline_write_max` guard notice：**主聊只展示 1 条** + 「（又失败 N 次，详见过程）」；或第 2 次起仅 evolve_log。
- 默认：**R2 已停 tool**，spam 自然消失；R4 为 M1  polish。

### 3.5 非目标

- 取消 `WRITE_INLINE_MAX_CHARS=8192`（仍管 messages 饮食）。
- 第一次大 inline 就硬拒（仍靠 guard + 提示；用户可一次改对）。
- 把全部 A 类 validation 计入 P5（范围过大；仅 **inline_write_max 重复**）。

---

## 4. 落点

| 文件 | 改动 |
|------|------|
| `exec_reliability.py` | streak 常量 · `record_inline_write_guard_failure` · `EXEC_INLINE_WRITE_NUDGE_MESSAGE` · `clear_*` 在 `begin_turn` |
| `tools/executor.py` | guard 返回前 bump streak；streak≥阈时 `retry: false` |
| `agent.py` | 达阈后同 P5：`tools_payload=None` + 内核 + notice；`_is_retryable` 尊重 streak |
| `tests/test_inline_write_guard_loop.py` | **IT-98** |
| `AGENT-HARNESS.md` §7.5 | 设计摘要 |
| `EXEC-RELIABILITY.md` §3.6 | 矩阵行 |

**重置**：`executor.begin_turn()` / 新 user 消息（与 P5、G14 一致）。

---

## 5. 验收

| ID | 场景 | 通过标准 |
|----|------|----------|
| **IT-98a** | Mock LLM 连续 2 次 inline 8943 字 `write_text` | 第 2 次后无第 3 次 tool；messages 含内核 staging 文案 |
| **IT-98b** | 第 1 次 inline 失败 → 第 2 次小 inline 或 staging 成功 | streak 清零；不触发 R2 |
| **IT-98c** | 新 user 消息 | streak / blocked 清零 |
| **S-98** | 手工 huiyi · 故意 inline 大 Vue | 第 2 次 guard 后停 tool + notice；不再刷 4 条相同 guard |

---

## 6. 临时规避（实施前）

1. 提示 agent：「大文件只用 `_staging + content_workspace_path`，禁止 inline content」。
2. 连刷 guard 时 **停止** → 发一句：「用 staging 路径写 DoctorList.vue」。
3. 大改前 **git commit**，避免「文件又乱了 → write_text 整文件覆盖」。

---

## 7. 工作留痕

| 日期 | 事项 |
|------|------|
| 2026-08-05 | 用户反馈：8192 guard 连刷 4 次；段内 3 次预算未触发；问 Cursor 做法 |
| 2026-08-05 | 结论：A 类 + TOOL-RETRY 盲区；已决 R1～R3；**文档先行**（本文 · T-4241） |
| 2026-08-05 | **T-4241 done**：`TASKS.md` · `AGENT-HARNESS.md` §7.5 · `EXEC-RELIABILITY.md` §3.6 · `RUNTIME-GUARDS.md` G16 · `BUGS.md` · `CHANGELOG.md` · `MAP.md` · `project-map.mdc` |
| 2026-08-05 | **T-4242～4243 done**：`exec_reliability.py` · `executor.py` · `agent.py` · `tests/test_inline_write_guard_loop.py` · IT-98 绿 |
