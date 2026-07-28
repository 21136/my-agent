# 记忆系统设计（MEMORY）

> 版本 0.2.3 · 2026-07-09 · 与 `RUNTIME.md` 配套

---

## 1. 目标

定义 my-agent 的 **记忆三件套**与 **统一主题索引**：按职责分层、按主题组织；与 **按主题放置的 evolved tools** 共用 `evolve/_index.toml`。

本阶段 **不涉及** skill 自动路由；升格与 proposal 协议见 [EVOLVE.md](./EVOLVE.md)。

---

## 2. 三件套总览

| 层 | 是什么 | 典型内容 | 物理位置 | 注入方式 |
|----|--------|----------|----------|----------|
| **Prompt（特殊要求）** | 必须遵守的硬规则 | 编码习惯、路径边界、确认策略 | `evolve/prompts/<topic>.md` | 主题确认后注入 **system** |
| **久远记忆** | 跨会话的软事实与背景 | 项目背景、历史做法、参考资料摘要 | `evolve/memories/<topic>/*.md` | 启动注入 **id + summary 索引**；正文按需 `read_file` |
| **短期记忆** | 仅本 thread 有效的工作上下文 | 本次会议目标、多轮对话、压缩摘要 | `goal.md` + `messages.jsonl` + `digest.md` | 锚定块 + messages；见 [RUNTIME.md](./RUNTIME.md) |

**桌面呈现（T-905d）**：磁盘 `messages.jsonl` 仍 **完整**；grow 聊天区仅显示过滤后的 user/assistant 可见行（`session.history`），与顶栏条数元数据（`session.memory`）分工见 [DESKTOP.md](./DESKTOP.md) §5.2.1。

一句话：

> **Prompt 管默认习惯，久远记忆管你知道什么，短期记忆管你现在在干什么。**

与 `LAYERS.md` 的对应：Prompt + 久远记忆均属 **L1**；短期记忆 **不进化** 进 `evolve/`（session 结束可丢弃，除非用户升格）。

---

## 3. 按主题拆分

### 3.1 为何按主题

- 单文件 prompt / memory 会把所有领域挤进同一段 system context，**有效记录容量**受 token 限制而非磁盘限制。
- 按主题拆分后，每个主题文件可以写得更细；**会话开始时只加载相关主题**，避免「记录得越多越用不上」。

磁盘（个人 2TB 级）不是瓶颈；**context 窗口**才是。主题拆分的目的是 **组织与按需加载**，不是省硬盘。

### 3.2 主题注册表（已决：统一索引）

**`evolve/_index.toml`** — 全进化层唯一主题索引，**每次启动轻量注入**。同时描述 prompt、memory、**evolved tool** 路径。

```toml
[[topic]]
id = "coding"
name = "开发与代码"
description = "Python、agent-core、Git、工具实现、文档结构"
prompt = "prompts/coding.md"
memory_dirs = ["memories/coding"]
tool_dirs = ["tools/coding"]
llm_model = "deepseek-v4-pro"   # 可选；含 coding 主题时会话用 pro

[[topic]]
id = "writing"
name = "写作与内容"
description = "稿子、文案、视频脚本、对外表述"
prompt = "prompts/writing.md"
memory_dirs = ["memories/writing"]
tool_dirs = ["tools/writing"]

[[topic]]
id = "workflow"
name = "个人工作流"
description = "文件整理、重复性操作、日程类任务"
prompt = "prompts/workflow.md"
memory_dirs = ["memories/workflow"]
tool_dirs = ["tools/workflow"]

[[topic]]
id = "safety"
name = "安全与边界"
description = "路径、确认、隐私、不落盘策略"
prompt = "prompts/safety.md"
memory_dirs = []
tool_dirs = []
```

**`tools/common/`**：不在 topic 条目中声明；凡 `status=active` 的 common 工具 **每个 session 都** 列入 evolved 清单（见 [TOOLS.md](./TOOLS.md) §4）。

**主题数量**：无磁盘硬上限。种子主题在 `_index.core.toml`；用户扩展在 `_index.user.toml`（合并加载，见 [EXTENSIONS.md](./EXTENSIONS.md)）。**LLM 不得**自动注册新主题；用户通过 REPL `注册主题 <id>` 或手改 user 索引。

### 3.3 目录结构（实现后）

```
evolve/
├── _index.core.toml            # 种子主题（随仓库）
├── _index.user.toml            # 用户扩展主题（可为空）；见 EXTENSIONS.md
├── prompts/
│   ├── coding.md
│   ├── writing.md
│   ├── workflow.md
│   └── safety.md
├── memories/
│   ├── coding/
│   │   └── project-my-agent.md
│   └── workflow/
│       └── downloads-sort.md
└── tools/
    ├── common/                 # 每 session 都列入 evolved 清单
    │   └── write_text/
    └── workflow/
        └── sort_downloads/

data/sessions/<conversation_id>/
    goal.md
    meta.json
    messages.jsonl
    digest.md              # context 压缩摘要，可选
```

内核只读 `agent-core/prompts/core.txt`；用户策展内容在 `evolve/`。

---

## 4. 两阶段主题路由（「两次提问」）

**不是**每条用户消息都路由两次 LLM；而是在 **session 开头** 用两次交互完成「定调 + 加载」。

### 4.1 阶段 0 — 启动（固定注入）

进入 system prompt：

1. `agent-core/prompts/core.txt`（内核规则）
2. **`evolve/_index.toml`** 渲染为主题列表（id / name / description）
3. **全局久远记忆索引**（所有 active 的 `id + summary`）
4. **Builtin 说明**（6 个，见 TOOLS.md）；evolved 清单在主题确认后注入

**不加载**任何 `coding.md` 等主题 prompt 全文。

### 4.2 阶段 1 — 路由（第一次问）

收集本次会议目标后（可与 §6 合并为同一轮），由 **LLM 读 `_index` 输出 `topics[]`**，用户确认：

```text
Agent: 这次主要做什么？（一句话）
User:  把记忆模块设计写进 MEMORY.md

Agent: 根据主题索引，我建议加载：coding、workflow。确认？(y/n/改)
User:  y
```

结构化输出示例：

```json
{ "topics": ["coding", "workflow"], "reason": "项目文档与开发流程" }
```

**默认策略（已决）**：LLM 提议主题 → **用户确认** → 再加载。用户可说「只要 coding」或「换 writing」覆盖。

可选快捷：用户启动时直接说 `主题 coding`（跳过 LLM 路由提议）。

### 4.3 阶段 2 — 加载（第二次问之后）

用户确认主题后，追加注入 system（或 session overlay）：

- 每个命中主题的 `evolve/prompts/<topic>.md` **全文**
- **本会话 evolved 工具清单**：`tools/common/*`（全部 active）+ 各命中 `tool_dirs` 下 active 工具（name + description，供 `run_evolved` 选用）

久远记忆 **不**在阶段 2 重复列出；阶段 0 全局索引每行已含 `(topic)`，MVP 够用。

**同一会话内**默认不再重新路由，除非用户 `换主题` / `加主题` / `主题 …`（见 §9）。

### 4.4 流程图

```
启动 → 注入 core + _index + 记忆全局索引
    → 问「这次做什么？」→ 写 goal.md
    → 阶段1：LLM 输出 topics[] → 用户确认
    → 阶段2：加载主题 prompt + common/主题 evolved 清单
    → 正常对话（6 Builtin + run_evolved）
exit → goal 可摘要入 evolve_log；不自动升格
```

---

## 5. 久远记忆

### 5.1 文件格式

```markdown
---
id: project-my-agent
topics: [coding]
status: active
summary: my-agent 个人进化 agent，Python 3.12，建设顺序先 tool 后 skill
---

## 背景
（长文、链接、备注…）
```

| 字段 | 说明 |
|------|------|
| `id` | 全局唯一，`evolve_log` 引用用 |
| `topics` | 与 `_index.toml` 的 `id` 对应，可多选 |
| `status` | `active` \| `archived` \| `suspect`（与 PROJECT §4.3 一致） |
| `summary` | **一行**，进入启动索引 |

### 5.2 启动注入（已决：id + summary 列表）

```text
[久远记忆]
- project-my-agent (coding): my-agent 个人进化 agent，Python 3.12…
- downloads-sort (workflow): 下载目录按扩展名分子文件夹…
```

- `status: archived` **不注入**
- 正文 **不** 默认进 context；需要时 `read_file evolve/memories/...` 或用户说「展开 project-my-agent」

### 5.3 与 Prompt 的边界

| 内容 | 放哪 |
|------|------|
| 「以后都…」「默认…」「必须…」 | `evolve/prompts/<topic>.md` |
| 「某项目/某事实是…」 | `evolve/memories/<topic>/*.md` |
| 「这次先…」 | 短期 `goal.md` / 对话 only |
| 重复 3 次且步骤固定的流程 | **L3 tool**，不是 memory |

升格（memory → prompt）**不预设自动规则**；使用中 LLM 可主动问「要不要写进 coding 的 prompt？」，用户确认后走 [EVOLVE.md](./EVOLVE.md) proposal 流程。

---

## 6. 短期记忆（本 thread）

> Session 默认 **续接**最近 thread；仅 **`新会话`** 新建 `conversation_id`。见 RUNTIME §2。

### 6.1 目标（可选 · 非新会话默认）

1. **`新会话` 默认不问目标**（直接 S4 开聊）；见 [RUNTIME.md](./RUNTIME.md) §2、[DESKTOP.md](./DESKTOP.md) §3.2.1。
2. 显式调用 `prompt_and_set_goal`（测试或日后 `目标 …` 命令）时：问「这次主要做什么？」→ 写入 `data/sessions/<id>/goal.md`（**gitignore**）。
3. 主题确认后写入 `meta.json`。

续接时沿用已有 `goal.md`，不重复问。

### 6.2 组成

| 来源 | 说明 |
|------|------|
| `goal.md` | 本 thread 目标 |
| `meta.json` | 已确认 `topics[]` 等；字段默认与兼容见 [RUNTIME.md](./RUNTIME.md) **§2.4 DOC-05** |
| 锚定块 | messages 首条模板（RUNTIME §5） |
| `messages.jsonl` | 多轮对话 + tool 结果；**仅本机** |
| `digest.md` | context 压缩后的 **早前对话摘要**（§6.3） |

`exit` 时：可写 goal 摘要入 `evolve_log`；**不**自动生成 proposal（除非显式「记住」）。

### 6.3 Context 压缩 = 短期记忆的延伸（已决）

thread 过长时（RUNTIME §8），不强制 `新会话`：

```text
早前 messages → LLM 压成 digest.md → 从上下文移除原文
              → digest 注入 system overlay（同「久远记忆索引」思路）
              → 保留最近 K 轮完整 messages
```

| 层 | 压缩时 |
|----|--------|
| **短期 digest** | 自动写入 `digest.md` |
| **久远记忆** | 不自动；用户说「记住」→ proposal |
| **Prompt** | 不删；safety + 主题 prompt 保留 |

**新会话** 用于换目标/清空现场；**digest** 用于同 thread 续聊。

---

## 7. 与 Skill / Tool 的关系

| 类型 | 关系 |
|------|------|
| **Skill（L2）** | 多步 SOP；M4 可选。可引用某主题下 memory / evolved 名 |
| **Tool（L3）** | Evolved 按 `tools/<topic>/` 与 `tools/common/` 放置；Builtin 固定 6 个 |
| **主题路由** | **同一 `_index.toml`** 驱动 prompt、memory 索引、evolved 会话清单 |

---

## 8. 审计与日志

> 引用级别（L0–L4）与治理事件见 [GOVERNANCE.md](./GOVERNANCE.md) §3、§9。**L2 memory**：仅 `read_file` 读取 `evolve/memories/**` 时记 `entity_used`（§3.1）。

| 事件 | evolve_log 字段 |
|------|-----------------|
| session 启动 | `conversation_id`, `memory_ids_loaded[]`, `topics_available[]` |
| 主题确认 | `topics_confirmed[]`, `prompt_files_loaded[]`, **`evolved_tools_listed[]`** |
| memory L2 引用 | `entity_used`：`entity_id`, `level: L2`, `reason`（路径或 tool 名） |
| session 结束 | `goal_summary?` |

---

## 9. 已决事项（原开放问题）

| # | 议题 | 决议 |
|---|------|------|
| 1 | 阶段 2 是否重复列出「本会话相关 memory」 | **MVP 不做**；阶段 0 全局索引含 `(topic)`；条目很多时再考虑置顶标记（M3+） |
| 2 | 换主题时 prompt overlay | **默认替换**：`新会话` / `换主题` / `主题 X` 确认后 `meta.topics` = 本次集合并重载 overlay；**仅** `加主题 …`（或自然语言「再加上 workflow」）为并集追加；「只保留 writing」= 单主题替换 |
| 3 | `_index.toml` / evolve 变更何时生效 | **accept / 手改不重载**（与 EVOLVE 一致）；**`换主题` / `加主题` / `主题 X` 立即重读磁盘并重载 overlay**；`新会话` / 下次启动读最新 |
| 4 | `_index` 位置 | `evolve/_index.toml` |
| 5 | common 工具 | 每 session 列入 evolved 清单 |
| 6 | L2 `entity_used` | 仅 `read_file` → `evolve/memories/**`；见 GOVERNANCE §3.1 |

---

## 10. 验收（MEMORY 设计阶段）

- [ ] 三件套职责与 `LAYERS.md` 无冲突
- [ ] 主题路由两阶段 + 用户确认流程无歧义
- [ ] 久远记忆 `id + summary` 格式固定
- [ ] 短期 goal 与 `PROJECT.md` §6.4 gitignore 一致
- [ ] 升格策略为「使用中 LLM 提议」，无自动硬规则

- [ ] 与 `evolve/_index.toml`、TOOLS 主题工具规则一致

实现验收见 `TASKS.md` Phase 3（T-301～T-308）。

---

## 11. 文档索引

| 文档 | 内容 |
|------|------|
| [LAYERS.md](./LAYERS.md) | 先 tool 后 skill；L1 含 prompt + 久远记忆 |
| [RUNTIME.md](./RUNTIME.md) | 续接 session、system 拼装、digest |
| [EVOLVE.md](./EVOLVE.md) | proposal、防重复、M2 写入 |
| [GOVERNANCE.md](./GOVERNANCE.md) | review、audit、suspect、ReviewReport |
| [TASKS.md](./TASKS.md) | Phase 3 实施 task |
| [PROJECT.md](./PROJECT.md) | 总览 §4.4 使用侧协议 |
