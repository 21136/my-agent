# 轮次反馈与友好提醒（TURN-FEEDBACK）

> 版本 **0.2.1** · 2026-07-11  
> 状态：**已实现**（T-905a～d；见 `TASKS.md`）
> 关联：[RUNTIME.md](./RUNTIME.md) §7–8 · [DESKTOP.md](./DESKTOP.md) §3.2.2 · [ORCHESTRATION.md](./ORCHESTRATION.md) §8–9 · [MEMORY.md](./MEMORY.md)

---

## 1. 动机

### 1.1 用户侧现象

| 现象 | 用户理解 | 实际发生 |
|------|----------|----------|
| 状态栏长期「思考中…」 | 系统在「回忆」过去对话 | 整轮等待：LLM + 多轮 tool + confirm；**无单独回忆阶段** |
| 问「刚刚我们说了什么」很慢 | 应秒回最近 k 轮 | 历史已在 context；可能被分成 `research` 先去调 `read_file` / `grep` |
| 设计里有 k 轮记忆 | 每条消息只带最近 8 轮 | `CONTEXT_KEEP_TURNS=8` **仅在压缩后**生效；未压缩时 **全量** `messages.jsonl` 进 payload |

结论：**能力大体在，可解释性不足**——用户不知道系统在干什么、记忆处于什么状态、这类问题本该怎么走。

### 1.2 与已有设计的关系

| 已有 | 缺口 |
|------|------|
| DESKTOP §3.2.2：B 层无 reasoning 时 **过程块内** 不显示「思考中」假文案 | **状态栏**仍一律 `思考中…`（实现未对齐文档） |
| T-704：仅 `qa` 在 tool max-1 轮注入软提醒 | 「刚才说了啥」常被分成 `research`，**吃不到** qa 提醒 |
| RUNTIME §8：压缩后 k=8 + digest | **无 UI** 暴露「未压缩 / 已压缩 / 摘要节数」 |
| `turn_intent` 写入 overlay | **用户不可见**（仅 LLM 侧） |

本文档补：**轮次反馈（turn feedback）**——把内核判断诚实地、克制地告诉用户。

### 1.3 已决摘要（2026-07-11 评审）

| ID | 决议 |
|----|------|
| **A1** | **默认显示** L1 意图条（每轮 `turn.start` → 顶栏或过程块首行，muted） |
| **A2** | 状态栏 **短文案**，过程块 **详**；不同步长句 |
| **A3** | 压缩耗时 > **3s** 可触发 L3 偏航式 `notice`（实现时若难测则先做压缩开始/结束两句） |
| **A4** | 偏航条 **仅** `turn.notice` 事件，**不落盘** `messages.jsonl` |
| **B1** | 指代上文（「你刚才推荐的 X」）**并入** `recall` |
| **B3** | `recall` **独立** intent（比 `qa` 更严，非子类） |
| **C1** | 记忆状态：**顶栏 session banner 右侧** |
| **C2** | `est_tokens` **仅 dev**；正式 UI 只 `message_count` + `memory_mode` |
| **C3** | **要** 首次自动压缩后一句用户教育 |
| **D1** | 内核软提醒 **不** 转发给用户（仅 overlay / dev） |
| **E1** | 泛问句（含「什么/哪些」但无查读路径）**降级 `qa`**，不进 `research` |
| **F1** | 任务编号：**T-905** 系列（挂 Phase 9 polish） |
| **B2** | **`tool_loop_max = 0`** — 父循环不暴露 tools（见 §4.4） |
| **—** | **T-905d** 续接 `session.history` 灌入 grow 聊天区（见 [DESKTOP.md](./DESKTOP.md) §5.2.1） |

## 2. 目标与非目标

### 2.1 目标

1. **状态诚实**：用户始终知道「在等什么」（直接答 / 调工具 / 等确认 / 子代理 / 压缩）。
2. **回顾类快路径**：会话内回顾（「刚才说了啥」）优先 **直接答**，少绕 tool。
3. **记忆可见（轻量）**：一眼可知本 thread 记忆形态（条数、是否已 digest），不造仪表盘。
4. **CLI 与桌面一致**：同一套 `turn.notice` 事件；桌面多一层呈现。

### 2.2 非目标

| 非目标 | 理由 |
|--------|------|
| 新 Builtin / 新 evolved tool | 纯编排 + 呈现 |
| 改造 context 压缩算法 | 仍用 RUNTIME §8；只加 **说明** |
| 每条消息弹 toast | 克制；顶栏 + 过程块够用 |
| 自动修 duplicate user messages | 另开 bug/任务；本文仅 **提醒** 可选手动 `压缩` |

---

## 3. 三层反馈模型（草案）

```text
用户发消息
  → [L1 意图条]  本轮打算怎么走（**默认显示**，<1 行，muted）
  → [L2 进行条]  当前在等什么（状态栏 + 过程块 A 层）
  → [L3 偏航条]  发现绕路时主动说明（可选，稀疏触发）
  → 正式 assistant 回复
```

### 3.1 L1 — 意图条（发消息后、首 token 前）

**作用**：回答「你这轮我会怎么处理」。

| `turn_intent`（拟） | 意图条文案（草案） |
|---------------------|-------------------|
| `recall`（**新增**，见 §4） | `根据上文直接回顾，不调工具` |
| `qa` | `直接回答` |
| `plan` | `整理方案，少动手` |
| `research` | `先查阅再回答`（若 spawn explore：`先只读探索`） |
| `execute` | `可动手执行` |

**已决 A1**：L1 **默认开**。

| 项 | 约定 |
|----|------|
| 时机 | `run_turn` 分类后、首 LLM 前，发 `turn.start` |
| 位置 | 顶栏状态区 **或** 当轮过程块首行（二选一实现，**待实现时定**；倾向顶栏右侧、与 §5 记忆条并列） |
| 样式 | `text-muted`、一行、不抢正式回答 |
| 设置（P2） | 可选 `显示轮次意图` 默认 **开**；关则仅 dev 或完全静默 |

### 3.2 L2 — 进行条（进行中）

对齐 DESKTOP §3.2.2，但 **状态栏与过程块规则统一**：

| 阶段 | 状态栏（草案） | 过程块 |
|------|----------------|--------|
| 等 LLM 首 token，且无 reasoning | `处理中…` | 不显示「思考中」标题 |
| 有 `reasoning.delta` | `思考中…` | B 层流式 |
| `tool.start` | `· {tool} {summary}` | A 层一行 |
| `confirm` 待点 | `等待确认…` | §3.2.1 按钮块 |
| explore 子代理 | `探索中 {n}/{max}…` | A 层 |
| context 自动压缩 | `正在压缩对话摘要…` | A 层 + 结束后 L1 式一句结果 |

**已决 A2**：状态栏 **短**、过程块 **详**（不同文案）。

### 3.3 L3 — 偏航条（稀疏）

**触发条件（草案，满足任一）**：

1. `turn_intent == recall` 但第 1 轮 LLM 仍返回 `tool_calls`
2. `qa` / `recall` 下 tool 轮数 ≥ 2
3. 自动压缩耗时 > **3s**（已决 A3；若难精确计时，退化为「压缩开始 + 结束」两句 `notice`）

**文案（草案）**：

```text
[提醒] 这类问题可以直接根据上文回答；若仍在查文件，可回复「别查了直接说」。
```

**已决 A4**：偏航条 **仅** `turn.notice` 事件；CLI 打印一行 muted，**不写** `messages.jsonl`。

---

## 4. 回顾类意图 `recall`（草案）

### 4.1 定义

**会话内回顾**：用户只想根据 **当前 thread 已有 messages** 复述、总结、对齐理解；**不应**为此去读 `messages.jsonl` 文件或 grep 磁盘。

### 4.2 识别（启发式，待扩）

优先于 `research` 命中：

| 模式 | 示例 |
|------|------|
| 时间指代 + 问内容 | `刚刚我们说了什么`、`刚才聊到哪了`、`上一轮你说` |
| 显式回顾 | `总结一下我们刚才的对话`、`recap` |
| 指代上文 | `你刚才推荐的那个工具叫什么`（**已决 B1**：并入 `recall`） |

**实现落点**：`turn_intent.py` 新增 `recall`，或 `classify_turn` 前先跑 `is_recall_turn(text)`。

### 4.3 行为（草案）

| 项 | 提议 |
|----|------|
| `spawn_explore` | **否** |
| 父循环 `tools` | **已决 B2**：**不暴露**（`tools=[]` / 不传 tools 参数）；`tool_loop_max` 等效 **0** |
| 软提醒 | **首轮前** 注入（比 T-704 更早）：`[内核] 根据上文直接回顾，勿 read_file/grep messages.jsonl` |
| overlay | `turn_intent: recall` |

**已决 B3**：`recall` 为 **独立** intent（非 `qa` 子类）；L1 文案与 tool 限制均严于 `qa`。

### 4.4 已决 B2：`recall` 不暴露 tools

**决议（2026-07-11）**：`recall` 轮父循环 **不向 LLM 传 tools**（等效 `tool_loop_max = 0`）。

| 项 | 约定 |
|----|------|
| 实现 | `agent._run_parent_tool_loop(tools=[])` 或 `llm.chat(..., tools=None)` 分支 |
| L1 文案 | `根据上文直接回顾，不调工具`（与行为一致） |
| 上文截断 | 模型 **说明** 上文已 truncate，引导用户下一句用 `research`（如「读 TOOLS.md」） |
| 混需求 | 句中夹带新调研（「顺便查 evolve…」）→ **不应** 命中 `recall`；分类器需排除或整句降级 `research` |

**边界用例**：

| 用户说 | 行为 |
|--------|------|
| `刚刚我们说了什么` | 纯总结，无 tool |
| `你刚才推荐的第三个工具叫什么` | 从上文 assistant 提取 |
| `刚才 TOOLS.md 里 Builtin 那段原文再贴一次` | 若上文已截断 → 文字说明 + 建议下一句显式 `读 docs/TOOLS.md` |
| `刚才说到哪了，顺便查 evolve 有没有 dep_check` | 分类为 `research`，非 `recall` |

---

## 5. 记忆状态可见（轻量）

### 5.1 展示位置

**已决 C1**：**顶栏 session banner 右侧**，示例 `26 条 · 未压缩`（与 L1 意图条可同一行：`直接回顾 · 26 条 · 未压缩`）。

> **注意（T-905d）**：顶栏条数只是 **记忆元数据**；用户要「看见之前说了啥」靠 `session.history` 在连接时灌入聊天区（见 `DESKTOP.md` §5.2）。

### 5.2 展示字段（草案）

| 字段 | 来源 |
|------|------|
| `message_count` | `len(session.messages)` |
| `memory_mode` | `未压缩` / `已压缩`（`compact_before_index > 1` 或 `digest.md` 存在） |
| `digest_sections` | `count_digest_sections(digest.md)`，无则省略 |
| `keep_turns` | `CONTEXT_KEEP_TURNS`（仅 `已压缩` 时显示「保留最近 K 轮」） |
| `est_tokens` | `estimate_context_tokens`（**已决 C2**：仅 dev / 调试开关） |

正式 UI：**message_count + memory_mode**（+ 已压缩时的 `digest_sections` / `保留最近 K 轮`）。

### 5.3 用户教育（一次性？）

**已决 C3**：首次 **自动**压缩完成后发 `turn.notice`（或顶栏一句）：

```text
较早对话已写入 digest.md；最近 8 轮仍完整保留。可说「压缩」手动触发。
```

---

## 6. 协议草案（server → desktop）

在现有 WebSocket 事件上 **增量**（名称待决）：

```json
{ "type": "turn.start", "intent": "recall", "intent_label": "根据上文直接回顾" }
{ "type": "turn.notice", "level": "info|warn", "text": "…" }
{ "type": "session.memory", "message_count": 26, "memory_mode": "full", "digest_sections": 0 }
```

| 事件 | 时机 |
|------|------|
| `turn.start` | `run_turn` 分类后、首 LLM 前 |
| `turn.notice` | L3 偏航、压缩完成、首次压缩教育（**已决 D1**：不转发 T-704 内核软提醒） |
| `session.memory` | 连接 / 每轮结束 / 压缩后 |

CLI：`turn.start` → 打印 `[本轮·recall] …`；`turn.notice` → `print` 一行 muted。

---

## 7. 与 `research` 误分类的修复

**现状**：`什么` 在 `_RESEARCH_KEYWORDS` 且匹配问句 → `刚刚我们说了什么` → `research`。

**草案**：

1. §4 `recall` 规则 **优先**
2. 问句含 `什么/哪些` 但 **无** 路径/markers/「查/读/探索」→ **已决 E1**：降级 `qa` 而非 `research`（在 `recall` 规则之后判断）

---

## 8. 实施分期（粗糙）

| 阶段 | 范围 | 文件（预期） |
|------|------|----------------|
| **P0** | `recall` 分类 + 更早软提醒 + `turn.start` | `turn_intent.py`, `agent.py`, `server.py` |
| **P0** | 状态栏：`思考中` → 分场景文案 | `desktop/src/shells/grow/index.ts` |
| **P1** | `session.memory` + 顶栏展示 | `server.py`, `grow/index.ts`, `context.py` |
| **P1** | L3 偏航 `turn.notice` | `agent.py` |
| **P1** | **`session.history` 续接灌聊天区** | `session.py`, `server.py`, grow（T-905d） |
| **P2** | 设置项、CLI 对齐、TASKS 条目 | `settings.ts`, `main.py` |

**已决 F1**：**T-905** 系列（Phase 9 polish 子项，见 `TASKS.md` 待补）。

---

## 9. 验收（草案）

- [ ] 输入「刚刚我们说了什么」→ 意图为 `recall`（或 `qa` + 等效限制）；**无** `read_file messages.jsonl`（除非用户明确要求查文件）
- [ ] 无 reasoning 流时，状态栏 **不出现**「思考中…」
- [ ] 有 `tool.start` 时，状态栏显示工具名
- [ ] 压缩触发时，用户看到「正在压缩…」及结果一句
- [ ] 顶栏（或选定位置）可见 `N 条 · 未压缩|已压缩`
- [ ] CLI 同轮可见 `[本轮·…]` 行
- [ ] 重开 grow → `session.history` 灌入可见 user/assistant（T-905d）

---

## 10. 决议清单（评审收口 2026-07-11）

| ID | 决议 |
|----|------|
| A1 | L1 **默认显示** |
| A2 | 状态栏短 / 过程块详 |
| A3 | 压缩 >3s 可 notice |
| A4 | 偏航仅 `notice`，不落盘 |
| B1 | 指代上文 → `recall` |
| **B2** | **`recall` 不暴露 tools（0）** |
| B3 | `recall` 独立 intent |
| C1 | 记忆条 banner 右侧 |
| C2 | tokens 仅 dev |
| C3 | 首次压缩教育句 |
| D1 | 不转发内核软提醒 |
| E1 | 泛问句 → `qa` |
| F1 | T-905 系列 |
| — | **T-905d** `session.history` 续接灌聊天区 |

**实现细节（非阻塞）**：L1 与记忆条同排顶栏右侧；过程块首行作 fallback。

---

## 11. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0-draft | 2026-07-11 | 初稿：三层反馈、recall intent、记忆可见、待决清单 |
| 0.1.1-draft | 2026-07-11 | 评审：A1 改默认显示；§1.3 已决摘要；§4.4 B2 讨论表 |
| 0.2.0-draft | 2026-07-11 | B2 收口：`recall` 不暴露 tools；§10 决议清单 |
| 0.2.0 | 2026-07-11 | **已实现** T-905a～c |
| 0.2.1 | 2026-07-11 | **T-905d** `session.history`；§5.1 注明 memory vs history 分工 |
