# 模式驱动预算（MODE-BUDGET）

> 版本 **0.1.0** · 2026-07-12 · **设计文档**（编排修正，T-907）  
> 状态：**已实现**（2026-07-12）  
> 关联：[ORCHESTRATION.md](./ORCHESTRATION.md) · [RUNTIME.md](./RUNTIME.md) §7 · [TURN-FEEDBACK.md](./TURN-FEEDBACK.md)

---

## 1. 动机

### 1.1 现场（2026-07-12）

用户在建 P1 `repl` 工具：`main.py` 已落地，staging `tool.toml` 在 workspace，用户回复 **「推过去」** 确认推送。

| 轮次 | 用户输入 | 内核 `turn_intent` | 工具预算 | 结果 |
|------|----------|-------------------|----------|------|
| N | 继续 P1 / 造 repl | `execute` | 50/segment + T-705 续跑 | `main.py` 落地 |
| N+1 | 推过去 | `qa`（无「写/改/造」关键词） | **5 轮**（`PARENT_SHORT_MAX`） | 差一步推 `tool.toml` 即被截断 |

用户可见文案：`本轮工具调用已达 5 轮上限…未能得到最终文字回复，且本轮无可见进展`。

### 1.2 根因

设计与实现 **自相矛盾**：

| 文档已决（ORCHESTRATION §1） | 实际代码（`agent.py`） |
|------------------------------|------------------------|
| 「不用 intent → 固定轮次作为任务能否完成的条件」 | `turn_intent == execute` 才走 T-705 多 segment |
| 「Ask / Agent 模式：能力开关，不是轮数表」 | 预算跟 `turn_intent`，`turn_mode` 只管 `run_evolved` 开闭 |
| 对齐 Cursor：Agent 下任务链连续 | 每条 user 消息重新 `classify_turn()`，短句易落 `qa` |

模型在 overlay 里能看到 `turn_intent: qa`，但 **看不到** `tool_loop_max=5`。路线分叉对 LLM 是黑箱。

### 1.3 不采纳的方案

| 方案 | 为何不采纳 |
|------|------------|
| 单纯调大 `PARENT_SHORT_MAX` | 不治本；误分类仍无 segment 续跑 |
| **续接词表**（推过去/继续/好 → `execute`） | 补丁式；Cursor 不靠关键词续跑 |
| 每条消息强制 `execute` | 破坏 qa/plan 纪律；与「先答」冲突 |

---

## 2. 原则（对齐 Cursor）

```text
turn_mode  → 能力开关 + 预算档位（用户显式选择，跨消息稳定）
turn_intent → 行为提示 + explore 触发（软约束，不决定轮次上限）
```

| Cursor | my-agent 对应 |
|--------|----------------|
| Ask 模式：偏只读、不写仓 | `turn_mode=ask`（`只聊`） |
| Agent 模式：完整工具、宽预算、任务链连续 | `turn_mode=agent`（`动手`，grow 默认） |
| 不在每条消息重新「换轨道」 | agent 下每条消息同一预算路径 |
| 防止乱调工具靠 prompt，不靠 5 轮硬掐 | T-701 纪律 + T-704 qa 软提醒（保留） |

**一句话**：模式管预算，意图管提示。

---

## 3. 已决行为

### 3.1 预算解析（核心）

```text
run_turn(user_text)
  → classify_turn → turn_intent（仅：explore 触发、overlay、turn.start）
  → turn_mode（meta.json，用户 `只聊`/`动手` 或 grow 默认 agent）

预算分支：
  recall                    → tool_loop_max = 0（T-905，不变）
  turn_mode == ask            → PARENT_SHORT_MAX（默认 5）
  turn_mode == agent          → PARENT_EXECUTE_SEGMENT_MAX + T-705 多 segment
                                （与 turn_intent 是否为 execute 无关）
```

伪代码（`agent.py` · `_resolve_parent_loop_max` 目标形态）：

```python
def _resolve_parent_loop_max(self, intent: str) -> int:
    if intent == "recall":
        return 0  # 父循环不暴露 tools
    if self.session.meta.turn_mode == "agent":
        return parent_execute_segment_max()
    return min(self.tool_loop_max, parent_short_max())
```

父循环入口（`run_turn` 目标形态）：

```python
if intent == "recall":
    ...  # 无 tools
elif session.meta.turn_mode == "agent":
    return self._run_execute_segments(...)  # 统一宽预算 + 续跑
else:
    return self._run_parent_tool_loop(max_rounds=PARENT_SHORT_MAX, ...)
```

### 3.2 `turn_intent` 保留职责

| 职责 | 说明 |
|------|------|
| `should_spawn_explore` | execute/research + 标记词 / 路径（T-703，不变） |
| `turn.start` 意图条 | 用户可见 L1 反馈（T-905，不变） |
| overlay 纪律提示 | qa/plan/recall/execute 文案（T-701，不变） |
| T-704 qa 软提醒 | **仅** `turn_intent == qa` 时在 max-1 轮注入（agent 模式下仍生效） |

**不再负责**：`tool_loop_max`、是否走 `_run_execute_segments`。

### 3.3 行为矩阵（实现后）

| turn_mode | turn_intent | run_evolved | 父循环预算 | segment 续跑（T-705） | T-704 软提醒 |
|-----------|-------------|-------------|------------|----------------------|--------------|
| ask | qa | 禁 | 5 | 否 | 是（若 intent=qa） |
| ask | plan | 禁 | 5 | 否 | 否 |
| ask | research | 禁 | 5 | 否 | 否 |
| ask | execute | 禁 | 5 | 否 | 否 |
| **agent** | **qa** | 允许 | **50/segment** | **是** | **是** |
| **agent** | plan | 允许 | **50/segment** | **是** | 否 |
| **agent** | research | 允许 | **50/segment** | **是** | 否 |
| **agent** | **execute** | 允许 | **50/segment** | **是** | 否 |
| 任意 | recall | — | 0 tools | — | recall 提醒 |

**关键用例**：agent + 「推过去」→ 意图条可仍显示 `直接回答`（qa），但内核走 **50 轮 + 有进展自动续 segment**；模型若判断只需一次 `write_evolve`，通常 1～2 轮即交付。

### 3.4 ask 模式（不变）

- `build_llm_tools` 继续剔除 `run_evolved`（T-702）
- 短循环 `PARENT_SHORT_MAX`（默认 5）
- explore 子代理仍可用（只读）

### 3.5 recall（不变）

- `tool_loop_max = 0`，不暴露 tools
- 不受 turn_mode 影响（回顾优先于动手）

---

## 4. 可见性（overlay 补充）

在 `format_turn_discipline_overlay` 增加一行（agent 模式）：

```text
tool_budget: agent — 每 segment ≤50 轮，可自动续跑（T-907）
```

ask 模式：

```text
tool_budget: ask — 每轮 ≤5 轮，run_evolved 已禁用
```

目的：模型与用户（dev overlay）知晓预算档位，减少黑箱感。正式桌面 UI **不**强制展示轮次数字（与 TURN-FEEDBACK 克制原则一致）。

---

## 5. 环境变量（语义调整）

| 变量 | 默认 | 实现前 | **实现后** |
|------|------|--------|------------|
| `PARENT_SHORT_MAX` | 5 | qa/plan/research 父循环 | **仅 `turn_mode=ask`** 父循环 |
| `PARENT_EXECUTE_SEGMENT_MAX` | 50 | 仅 `intent=execute` | **`turn_mode=agent`** 每 segment |
| `PARENT_EXECUTE_TOTAL_MAX` | 50 | 仅 execute | **`turn_mode=agent`** 每条用户消息总顶 |
| `MY_AGENT_AUTO_CONTINUE` | 1 | execute segment 续跑 | **agent 模式** segment 续跑 |

`turn_intent` 不再出现在此表的预算列。

---

## 6. 与现有任务的关系

| ID | 关系 |
|----|------|
| T-702 | 保留；ask/agent 能力开关 |
| T-703 | 保留；分类仅触发 explore + 意图条 |
| T-704 | 保留；qa 软提醒在 agent+qa 时仍注入 |
| T-705 | **扩展**：触发条件从 `intent==execute` 改为 `turn_mode==agent` |
| T-706 | 不变 |
| T-905 | 不变；recall 快路径优先 |
| **T-907** | **本设计** — 模式驱动预算解耦 |

---

## 7. 实现清单

| 文件 | 变更 |
|------|------|
| `agent-core/agent.py` | `_resolve_parent_loop_max`；`run_turn` 在 `agent` 下统一 `_run_execute_segments`；recall 分支提前 |
| `agent-core/loader.py` | overlay `tool_budget` 行；`format_tool_loop_user_message` 文案区分 ask/agent |
| `docs/RUNTIME.md` | §7.1 流程图与表格 |
| `docs/ORCHESTRATION.md` | §2 架构图、§8 表、版本 0.3.0 指针 |
| `docs/MAP.md` | T-907 索引 |
| `docs/TASKS.md` | T-907 任务与验收 |

**不改**：

- `turn_intent.py` 关键词表（不加续接词）
- `detect_scaffold_tool_turn`（仍按当轮 user 文本；后续可另开任务做跨轮 scaffold 状态）

---

## 8. 验收

### 8.1 自动化（`agent.py` 自测）

| 用例 | 断言 |
|------|------|
| T-907a | `turn_mode=agent` + mock 用户句分类为 `qa` + 连续 6 次 tool_calls → **不**在 5 轮截断；走 segment 逻辑 |
| T-907b | `turn_mode=ask` + `qa` → 仍在 `PARENT_SHORT_MAX` 截断 |
| T-907c | T-705 现有用例在 `turn_mode=agent` 下仍 pass（回归） |
| T-907d | `recall` + `agent` → 仍 0 tools，不受 agent 宽预算影响 |

### 8.2 手工（对话）

```text
# grow 默认 agent
you> 按 run_demo 造 common/repl 工具
# → explore 可选；write_evolve main.py + tool.toml

you> 推过去
# → turn.start 可能仍显示「直接回答」
# → 但必须能完成 write_evolve，不在 5 轮失败
```

```text
you> 只聊
you> 1+1 等于几
# → 无 run_evolved；≤5 轮；直接文字答
```

### 8.3 通过标准

```powershell
cd D:\my-agent\agent-core
python agent.py          # 含 [PASS] T-907:
python turn_intent.py      # 分类用例不变（无回归）
```

---

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| agent 下纯问答也占 50 轮预算 | 模型通常 0 轮 tool 即答；T-704 仍提醒「直接回答」 |
| agent 下 research 误写 evolve | T-701 纪律 + explore 子代理；非预算问题 |
| token / 成本上升 | 总安全顶仍 `PARENT_EXECUTE_TOTAL_MAX=50`；与现 execute 相同 |
| 文档与代码长期分叉 | T-907 完成标志 = RUNTIME §7.1 与本文 §3 一致 |

---

## 10. 开放问题（实现时定）

| # | 问题 | 倾向 |
|---|------|------|
| 1 | agent+qa 时 `turn.start` 文案是否改为「动手模式 · 可直接答」 | 可选 polish；非阻塞 |
| 2 | `format_tool_loop_user_message` 在 agent 有进展但仍超总顶时的文案 | 复用 T-705 `format_total_cap_message` |
| 3 | 跨轮 `scaffold_tool_turn` 继承 | **另开任务**；不在 T-907 范围 |

---

## 11. 文档历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-12 | 初稿：模式驱动预算；否决续接词方案；T-907 |
| 0.1.1 | 2026-07-12 | **已实现**：`agent.py` + `loader.py` + T-907a/b/c 自测 |
