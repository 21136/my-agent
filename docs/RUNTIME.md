# 对话层设计（RUNTIME）

> 版本 0.2.5 · 2026-07-09 · 与 `MEMORY.md`、`TOOLS.md` 配套  
> M1b 设计文档，**先评审再写代码**

---

## 1. 目标

定义 **CLI 对话层**：Session 生命周期、LLM 调用、system 拼装、主循环与 tool 内循环、context 压缩。

**已决**：

| 项 | 决议 |
|----|------|
| Session 默认 | **续接**最近 `conversation_id` |
| 新开会话 | 仅 **`新会话`** 重置 goal / topics / 空 messages |
| `exit` | 保存状态退出；**不强制** proposal |
| Proposal | **仅显式触发**（见 §9）；与 [PROJECT.md](./PROJECT.md) §4.3 衔接 |
| LLM | **OpenAI 兼容 API + DeepSeek**；默认 flash，**coding 主题**用 pro（§6.1） |
| Context 爆了 | **记忆式压缩**（§8），非仅警告 |

---

## 2. Session 模型

### 2.1 默认续接（已决）

```text
启动 CLI
  → 加载最近 session（data/sessions/<id>/）
  → 恢复 goal、topics、messages
  → 拼 system（基础 + overlay，含 safety）
  → 插入会话锚定块（§5）
  → 直接进入主循环（跳过目标/主题问答）

用户输入「新会话」
  → 新 conversation_id
  → S1 目标 → S2 主题路由 → S3 确认 → 主循环
```

| 命令 | 行为 |
|------|------|
| （默认启动） | `resume` 最近 session |
| `新会话` / `new` | 新 id；问 goal；LLM 提议 topics；用户确认 → **替换** topics，全量重载 overlay |
| `主题 coding` | **替换** `meta.topics` 为本次集合；立即重载 overlay（可跳过 LLM 提议） |
| `换主题` | 保留 messages；重新走 S2/S3 或快捷口令 → 确认后 **替换** topics，重载 overlay |
| `加主题 workflow` | `meta.topics` **并集**追加；增量加载新 topic 的 prompt + evolved 清单 |
| `压缩` / `summarize` | 手动触发 context 压缩（§8）；同 thread，不换 `conversation_id` |
| `exit` | 持久化 session；退出；**不**自动生成 proposal；若启用反馈（§10）且有待反馈 L2+ 实体，**可选**问一句（可跳过） |
| `Ctrl+C` | 取消当前输入/confirm；**不**结束 thread |

**overlay 重载（已决，与 [MEMORY.md](./MEMORY.md) §9）**：

| 触发 | 行为 |
|------|------|
| `proposals accept` / 手改 `evolve/` | **不重载**当前 session overlay |
| `换主题` / `加主题` / `主题 …` | **立即**重读 `_index.toml`、memories、prompts、tools，重载 overlay |
| `新会话` / 下次启动 | 读磁盘最新状态 |

### 2.2 持久化（gitignore）

```text
data/sessions/<conversation_id>/
  goal.md              # 本次会议目标
  meta.json            # topics[], llm_model, updated_at, phase；M4+ pending_feedback[]
  messages.jsonl       # 完整多轮（含 tool）；**永不截断**；默认不进 Git；压缩后仍可检索（§8）
  digest.md            # 早前对话摘要（§8）；多次压缩追加节
  tool_outputs/        # 超长 tool 结果落盘（TOOLS §6.3）
```

`messages` **仅本机**（已决）。**默认 exit 不写** `data/conversations/`；见 §2.3 `--record`。

---

## 3. Session 阶段

| 阶段 | 何时 | 调 LLM？ |
|------|------|----------|
| **S0** | 每次启动 / 续接 | 否：拼 system 基础层 |
| **S1** | 仅 `新会话` | 否：CLI 问目标 → `goal.md` |
| **S2** | 仅 `新会话`（或 `换主题`） | **是 1 次**：提议 `topics[]` |
| **S3** | 用户确认 topics | 否：加载 overlay |
| **S4** | 主循环 | 是：每轮用户输入 + tool 内循环 |

续接时：**S1～S3 跳过**；沿用 `meta.json` 中的 `topics` 与 `goal.md`。

### 2.3 对话归档 `--record`（已决）

| 模式 | 行为 |
|------|------|
| **默认** | 仅写 `data/sessions/<id>/messages.jsonl`（gitignore）；**不**写 `data/conversations/` |
| `my-agent --record` 或 `exit --record` | 追加 `data/conversations/<id>.json`：**摘要 ≤200 字** + proposal / evolve 引用；**可进 Git** |
| `--record full` | 同上，且另存该 session 全文到 `data/conversations/<id>-full.jsonl`；启动时提示 U 盘丢失泄露风险 |

---

## 4. System 拼装

### 4.1 基础层（S0，续接与新会话相同）

顺序固定：

```text
1. agent-core/prompts/core.txt
2. [主题索引]              ← evolve/_index.toml
3. [久远记忆索引]          ← 全局 id + summary
4. [Builtin 说明]          ← 6 个函数用法摘要
```

### 4.2 会话 overlay（续接时按 meta.topics 加载；新会话在 S3 后加载）

```text
5. [本次会议]              ← goal、topics、conversation_id
6. [safety.md 全文]        ← 每个 session 都加载（已决 B）
7. [主题 prompt 全文]      ← 各 prompts/<topic>.md
8. [本会话 evolved 清单]   ← common/* + 命中 tool_dirs
9. [对话摘要 digest]       ← 若 digest.md 存在（§8）
```

实现：单条 `system` 字符串，段落间 `\n---\n` 分隔。

### 4.3 safety 始终加载（已决）

不依赖主题路由是否命中 `safety`；**每次** append `evolve/prompts/safety.md` 全文。

---

## 5. 短期记忆与锚定块（已决 A）

进入 S4 前（续接后首次进循环、或新会话 S3 后），在 **messages 最前** 插入一条锚定（`role: user`，内容模板固定）：

```text
[本次会议上下文]
conversation_id: <id>
目标: <goal 全文>
主题: <topics 逗号分隔>
说明: 工作区 workspace/；进化目录 evolve/；动手只用 Builtin 与 run_evolved。
```

之后 append 历史 `messages.jsonl`（若有 digest，见 §8）。

多轮对话与 tool 结果照常追加；**不**把 goal 只藏在 system 里。

---

## 6. LLM 客户端

### 6.1 Provider（已决）

**OpenAI 兼容 + DeepSeek**；薄封装 `llm_client.py`，不做多 adapter。

| 环境变量 | 说明 |
|----------|------|
| `LLM_API_KEY` | DeepSeek API Key |
| `LLM_BASE_URL` | 默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 默认 **`deepseek-v4-flash`**（通用会话） |
| `LLM_MODEL_CODING` | 默认 **`deepseek-v4-pro`**（含 `coding` 主题时） |
| `LLM_TIMEOUT_SEC` | 默认 **`120`** |
| `LLM_CONTEXT_LIMIT` | 见下方「context 上限」；可被环境变量覆盖 |

> `deepseek-chat` / `deepseek-reasoner` 将于 2026-07-24 弃用；不再作为默认。

**context 上限（已决）**：

| 会话 model | 未设 `LLM_CONTEXT_LIMIT` 时默认 |
|------------|--------------------------------|
| flash | `128000` |
| pro（含 `coding`） | `1000000` |

**会话模型解析（已决）**：

| 条件 | 主循环 S4、§8 digest 使用的 model |
|------|-----------------------------------|
| `meta.topics` **含** `coding` | `LLM_MODEL_CODING`（默认 pro） |
| 否则 | `LLM_MODEL`（默认 flash） |

- `换主题` / `加主题` / `主题 …` 确认后 **重新解析**；结果写入 `meta.json` 的 `llm_model`
- **S2 主题路由**始终用 `LLM_MODEL`（flash），仅输出 JSON，不必 pro
- `web_search` 子调用仍用 `WEB_SEARCH_MODEL`（默认 flash），见 [TOOLS.md](./TOOLS.md) §7.4

### 6.2 调用模式

| 调用 | model | tools | 温度 | 说明 |
|------|-------|-------|------|------|
| 主题路由 S2 | `LLM_MODEL`（flash） | 无 | 0 | 输出 JSON |
| 主循环 S4 | **会话 model**（§6.1） | 6 Builtin | 0.3 | 可 tool_calls |
| Context 摘要（§8） | **会话 model** | 无 | 0 | 压缩早前对话 |

### 6.3 MVP 默认

- **无流式**；整段返回后打印  
- 超时 **`LLM_TIMEOUT_SEC`**（默认 120s）

### 6.4 主题路由消息（S2）

```json
{"topics": ["coding", "workflow"], "reason": "项目文档与开发流程"}
```

User 消息模板：

```text
本次会议目标：
{goal}

请根据 system 中的主题索引，提议应加载的 topics。只输出 JSON：{"topics":[],"reason":""}
```

用户确认后写入 `meta.json`；快捷：`主题 coding` 直接设 `topics=["coding"]`。

---

## 7. 主循环与 Tool 内循环

### 7.1 流程

```text
读取用户一行
  → append user message
  → 检查 context 预算（§8）
  → tool_loop (最多 10 轮):
        LLM(messages, tools=6 builtins)
        无 tool_calls → 打印回复 → break
        有 tool_calls → 顺序 executor → append tool results
  → 写 messages.jsonl
```

### 7.2 Builtin functions（恒 6）

`read_file` · `list_dir` · `grep` · `web_search` · `fetch_url` · `run_evolved`

`run_evolved.tool_name` 必须在本会话 evolved 清单内（[TOOLS.md](./TOOLS.md) §4）。

### 7.3 Executor

- `confirm` / `dry_run` 在 CLI 阻塞；**不**再调 LLM  
- 结果写入 `evolve_log.jsonl`  

---

## 8. Context 压缩（记忆式机制，已决）

> **对齐 Cursor**：有损压缩、同 thread 续聊、静态层不压、压早前对话 + 保留近期完整轮次、磁盘保留可检索全文（`messages.jsonl`）。**my-agent 策略**：85% 主动触发 + 手动 `压缩` + digest ≤8000 字符（比 Cursor 常见 ~1k token 摘要更保守，减失忆）。

Context 接近上限时，**不**默认要求 `新会话`；沿用记忆三件套思路：

| 记忆层 | Context 压缩的对应 |
|--------|-------------------|
| **短期** | 将 **早前 messages** 摘要写入 `digest.md`，主循环注入 overlay §4.2.9 |
| **久远** | 摘要前若有关键事实，提示「要说 `记住` 才能进 evolve」；**不自动** proposal |
| **Prompt** | 不变；safety + 主题 prompt 保留 |

**不压缩（静态层）**：`core.txt`、`_index`、记忆索引、safety、主题 prompt、evolved 清单、锚定块。

### 8.1 触发

| 方式 | 条件 |
|------|------|
| **自动** | 估算 `system + messages` tokens ≥ **`LLM_CONTEXT_LIMIT` × 85%** |
| **手动** | 用户输入 **`压缩`** / `summarize` |
| **可选** | executor 返回 `context_pressure: true`（等同自动阈值）；**M1 不实现**，M2 若单轮 tool 环过长再加 |

**Token 估算（MVP）**：`tokens ≈ len(text) // 4`（heuristic，与精确 tokenizer 无关）。

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `LLM_CONTEXT_LIMIT` | flash `128000` / pro `1000000` | §6.1；env 可覆盖 |
| `CONTEXT_COMPACT_RATIO` | `0.85` | 自动压缩触发比例 |
| `CONTEXT_KEEP_TURNS` | `8` | 保留最近完整 **轮次**（1 轮 ≈ user + assistant 含 tool） |
| `CONTEXT_DIGEST_MAX_CHARS` | `8000` | 单次摘要写入上限（字符） |

### 8.2 压缩步骤

```text
1. 保留：锚定块 + 最近 K=CONTEXT_KEEP_TURNS 轮完整 messages
2. 将 K 轮之前的对话 LLM 压成 digest（结构化模板，≤ CONTEXT_DIGEST_MAX_CHARS）
3. 追加写入 data/sessions/<id>/digest.md（新节 `# 压缩 N`，不覆盖旧节）
4. messages.jsonl 磁盘 **追加不变、不截断**；仅从 **发给 LLM 的 payload** 移除已摘要部分
5. system overlay 注入 digest 全文（§4.2.9）
6. 需要细节时：LLM 可 read_file / grep messages.jsonl（对齐 Cursor 历史文件可检索）
```

**digest 结构模板**（摘要 prompt 固定要求）：

```markdown
## 目标
## 已做
## 未决
## 关键路径与命令
## 用户约束
```

### 8.3 与 `新会话` 的关系

| 手段 | 何时 |
|------|------|
| **digest 压缩** | 同 thread 太长；**不换** conversation_id |
| **新会话** | 你要彻底换目标/清空现场 |

用户说「记住」→ 走 proposal（§9），与压缩独立。

---

## 9. Proposal 触发（已决，修订 PROJECT §4.3）

| 触发 | 是否 |
|------|------|
| 「记住」「记住这个」「沉淀」「写进 evolve」「以后都这样」 | **是** |
| LLM 主动问升格 + 用户确认 | **是** |
| 任务成功自动提议 | **否** |
| `exit` | **否**（不强制） |
| `新会话` 前 | **否**（已决 1.A）；仍可说 `记住` 开检查点 |

Proposal 流程见 [EVOLVE.md](./EVOLVE.md)；写入 `evolve/proposals/`，用户审后进入 prompts/memories/tools。

---

## 10. 使用侧反馈（M4，T-602）

> 与 [PROJECT.md](./PROJECT.md) §4.4、[GOVERNANCE.md](./GOVERNANCE.md) §6 配套。M1–M3 **只**写 `entity_used`；**M4** 起启用 exit 问句与 `feedback_*` 事件。

### 10.1 开关

| 项 | 决议 |
|----|------|
| 默认 | **关** |
| 启用 | 环境变量 `MY_AGENT_FEEDBACK_ON_EXIT=1` |
| 与 proposal | 独立；`exit` 仍不强制 proposal |

### 10.2 何时问

| 触发 | 是否 |
|------|------|
| `exit` 且有待反馈 L2+ 实体 | **是**（启用开关时） |
| 每轮 assistant 回复后 | **否** |
| 仅 `run_evolved` 成功后 | **否** |
| 仅展开 memory 后 | **否** |
| `新会话` | **否** |
| 任意时刻用户说「`mem-xxx` 不对」「别再用 write_text」 | 走明示否定（[GOVERNANCE.md](./GOVERNANCE.md) §6.2），**不**依赖 exit |

**待反馈**：本 `conversation_id` 内出现过 `entity_used`（level ≥ **L2**），且该 `entity_id` 本 session 尚无 `feedback_positive` / `feedback_negative`。`meta.json` 维护 `pending_feedback: [{ entity_id, type, level, used_at }]`，按 `entity_id` 去重保留最新。

**多实体**：只问 **一个**——优先级 **L4 skill > L3 tool > L2 memory**；同层取 `used_at` 最新。**不**纳入 prompt（L1）。

### 10.3 `entity_id`

与 [GOVERNANCE.md](./GOVERNANCE.md) §9 `entity_used` 一致：

| 类型 | `entity_id` |
|------|-------------|
| memory | frontmatter `id` |
| tool | `tool.toml` 的 `name`（或 registry 全局 id） |
| skill | `meta.json` 的 `id` 或目录名 |

### 10.4 Exit 交互

```text
本次会话用到了 evolve 条目：tool:write_text
这次用得对吗？(y/n，回车跳过)
>
```

| 输入 | 效果 |
|------|------|
| `y` / `对` | `feedback_positive`；该 entity 的 `failure_streak` 归零 |
| `n` / `不对` | `feedback_negative`；聚合 streak（≥3 → suspect） |
| 回车 / `skip` | 不写 feedback；streak 不变 |

`failure_streak` 仅从 `evolve_log` 聚合；`status: suspect` 写入实体文件。详见 GOVERNANCE §6。

---

## 11. 模块划分

```text
main.py           REPL、命令：新会话/换主题/压缩/exit（含 §10 反馈）
session.py        续接、持久化、conversation_id、pending_feedback
loader.py         system 基础 + overlay（含 safety、digest）
router.py         S2 主题 JSON
agent.py          主循环 + tool 内循环 + context 检查
llm_client.py     DeepSeek（OpenAI 兼容）
context.py        digest 压缩（§8）
```

---

## 12. 验收（RUNTIME 设计阶段）

- [ ] 默认续接 + `新会话` 重置无歧义
- [ ] system 拼装顺序与 safety 始终加载
- [ ] DeepSeek env 与 6 Builtin tool 环
- [ ] 锚定块 + messages 持久化
- [ ] Context 压缩：85% 自动、`压缩` 手动、K=8、digest≤8k、`messages.jsonl` 不截断
- [ ] Proposal 仅显式触发；exit 不强制
- [ ] M4：`MY_AGENT_FEEDBACK_ON_EXIT=1` 时 exit 反馈与 GOVERNANCE §6 一致

实现见 `TASKS.md` Phase 2（T-201～T-210）、Phase 6（T-602）。

---

## 13. 文档索引

| 文档 | 内容 |
|------|------|
| [MEMORY.md](./MEMORY.md) | 三件套、主题、digest 与短期记忆 |
| [EVOLVE.md](./EVOLVE.md) | proposal、防重复、接受路由 |
| [TOOLS.md](./TOOLS.md) | Builtin、web_search、run_evolved |
| [GOVERNANCE.md](./GOVERNANCE.md) | review、exit 反馈、suspect |
| [TASKS.md](./TASKS.md) | 实施 task |
