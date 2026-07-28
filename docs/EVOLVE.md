# 进化写入设计（EVOLVE）

> 版本 0.2.6 · 2026-07-09 · 与 `MEMORY.md`、`RUNTIME.md`、`TOOLS.md` 配套

---

## 1. 目标

定义 my-agent **进化写入侧**完整协议：

- 何时生成 proposal（检查点、触发词、升格两跳）
- proposal 文件格式与三类型模板
- evidence 原文摘录规则
- 接受后路由到 `prompts/`、`memories/<topic>/`、`tools/<topic>/`
- **防重复**（身份键、指纹、跨层互斥）
- 与续接 thread、digest 的边界

**M2 范围**：

| 包含 | 不含 |
|------|------|
| `memory` 新建 / 更新 | `skill` proposal（M4） |
| `prompt_patch`（`append_section`） | 自动生成 `.py` / `tool.toml` |
| `tool_suggestion` 规格书 | 自动新增 topic（须 `注册主题`；见 [EXTENSIONS.md](./EXTENSIONS.md)） |
| `evolve_log` 写入事件 | 第二 LLM 审校 proposal |
| 防重复最小集（§6） | 向量语义去重（M4+） |

读取侧（索引注入、主题路由）见 [MEMORY.md](./MEMORY.md)；执行侧见 [TOOLS.md](./TOOLS.md)；触发入口见 [RUNTIME.md](./RUNTIME.md) §9。

---

## 2. 检查点与降噪

### 2.1 检查点定义（已决）

```text
检查点 = 一次「允许生成 proposal 批次」的时刻
```

| 来源 | 是否开检查点 |
|------|----------------|
| 用户显式触发（§3.1） | **是** |
| LLM 升格询问 + 用户确认（§3.2） | **是** |
| `新会话` 前软询问 + 用户说「有，沉淀」 | **否**（已决 1.A） |
| `exit` | **否** |
| digest 压缩前/后 | **否** |
| 任务成功 | **否** |

**每检查点 ≤ 2 条 proposal**。同一会话可有 **多个** 检查点（每次仍须显式触发）；**不是**「整会话至多 2 条」。

### 2.2 与旧口径的统一

- `PROJECT.md` R8、`TASKS.md` 验收：以 **≤2/检查点** 为准
- `exit` **不**开检查点、不扫「可沉淀内容」

---

## 3. 触发与升格流程

### 3.1 显式触发（用户主导）

内核做 **关键词/短句匹配**（不必另调 LLM 判意图）：

| 类别 | 示例 | 行为 |
|------|------|------|
| 强触发 | `记住`、`记住这个`、`沉淀`、`写进 evolve`、`以后都这样` | 立即开检查点 |
| 弱确认 | 对 LLM 升格问的 `好`、`要`、`写进去`、`对` | 开检查点 |
| 不触发 | 单独 `exit` / `新会话`、任务完成、压缩 | 无 |

### 3.2 LLM 升格（两跳：先问后写）

```text
S4 对话中 LLM 发现可固化内容
  → 跳1（口头，不写文件）：「这条更像 coding 的硬规则，要写进 prompt 吗？」
  → 用户确认（§3.1 弱确认）
  → 跳2（检查点）：生成 ≤2 条 proposal 文件
```

**禁止**：口头一问就直接写 `proposals/`；禁止任务成功后静默生成。

**口头升格频率（已决）**：每会话 LLM **主动**升格询问 **≤1 次**；不问第二次。用户随时可说 `记住` 开检查点（不受此限）。

### 3.3 类型路由（生成前）

| 内容性质 | `type` | 接受后落点 |
|----------|--------|------------|
| 必须/默认/以后都/禁止 | `prompt_patch` | `evolve/prompts/<topic>.md` |
| 某项目/事实是/背景是 | `memory` | `evolve/memories/<topic>/<id>.md` |
| 固定步骤、可脚本化 | `tool_suggestion` | **仅 spec**；用户手放 `tools/<topic>/` |
| 多步 SOP | — | M2 不做；提示 M4 skill |

`topics[]` 必须来自合并后主题索引已有 `id`；LLM **不得**提议新 topic（用户经 `注册主题` 扩展；见 [EXTENSIONS.md](./EXTENSIONS.md)）。

### 3.4 生成前注入（降噪 + 防重复）

开检查点后，LLM 输入 **必须包含**：

```text
[已有 evolve 索引]
- memory: id + summary（active only）
- prompt: 各 topic 的 ## 标题列表
- pending proposals: id + summary + target
```

生成规则：

1. 已有条目 **已覆盖** → **不生成** proposal；口头指向已有 `id`
2. 需修订已有 → `mode: update`，`target` 指向已有 `memory_id` / `anchor`
3. 同检查点候选互相 fingerprint 过近 → **只留 1 条**

---

## 4. Proposal 文件格式

### 4.1 路径与命名

```text
evolve/proposals/<YYYYMMDD>-<seq>-<type>-<slug>.md
```

例：`20260709-001-memory-my-agent-conventions.md`

### 4.2 Frontmatter

```yaml
---
id: prop-20260709-001
status: pending          # pending | accepted | rejected | superseded
type: memory             # memory | prompt_patch | tool_suggestion
mode: create             # create | update（memory / prompt_patch）
topics: [coding]
target:
  topic: coding
  memory_id: my-agent-conventions      # memory
  path: memories/coding/my-agent-conventions.md
  # prompt_patch 额外：
  # path: prompts/coding.md
  # mode: append_section
  # anchor: "## 路径与仓库"
  # tool_suggestion 额外：
  # tool_name: format_py
  # path: tools/coding/format_py/
source:
  conversation_id: sess-abc
  checkpoint_at: "2026-07-09T17:53:00+08:00"
  triggered_by: explicit | llm_offer
  trigger_phrase: "记住这个"
fingerprint: "a1b2c3..."               # normalize(summary)；§6
evidence_fingerprints: ["e4f5..."]       # hash(quote)；§6
related:                               # 可选，跨层/近似重复
  - kind: memory
    id: project-my-agent
    relation: duplicates | supersedes | complements
created_at: "2026-07-09T17:53:00+08:00"
---
```

### 4.3 正文结构（固定三段）

```markdown
## Summary
一行：策展人扫一眼即懂。

## Proposed
（type 相关正文，§4.4）

## Evidence
（≤2 条原文摘录，§5）
```

### 4.4 三类型 `## Proposed` 模板

**memory（create）**

```markdown
---
id: my-agent-conventions
topics: [coding]
status: active
summary: 一行摘要，进启动索引
---

## 背景
（接受后整段写入目标文件）
```

**memory（update）** — 接受后 **追加** `## 修订 YYYY-MM-DD`，不覆盖正文（§7.2）。

**prompt_patch（MVP 仅 `append_section`）**

```markdown
## 段落标题
- 规则 1
- 规则 2
```

同 `anchor`（`## 标题`）已存在于目标 prompt → 生成阶段 **拒绝 append**；须改标题或用户手改后 `update`。

**tool_suggestion（无代码）**

```markdown
### 意图
一句话。

### 输入 / 输出
- in: ...
- out: ...

### 步骤
1. ...
2. ...

### 放置
- 目录: `evolve/tools/workflow/sort_downloads/`
- topics: [workflow]
- 备注: 接受后由用户手写 main.py + tool.toml
```

---

## 5. Evidence 规则

| 规则 | 说明 |
|------|------|
| 来源 | **对话原文摘录**；禁止 LLM 自评式 evidence |
| 数量 | **每条 proposal ≤ 2 条** evidence |
| 优先级 | 优先 `role: user`；`assistant` 仅当用户明确附和 |
| 格式 | verbatim `quote`；附 `ref`（`messages.jsonl#<line>` 或时间戳） |
| digest 后 | 优先从磁盘 `messages.jsonl` 抽；若仅 digest 留存 → `source: digest`，quote 来自 `digest.md` 原文 |

示例：

```markdown
## Evidence
- role: user
  quote: "以后改 docs 都先更新 CHANGELOG"
  ref: messages.jsonl#42
- role: user
  quote: "对，写进 coding 的 prompt"
  ref: messages.jsonl#43
```

---

## 6. 防重复

进化层「越用越乱」主要来自重复。M2 用 **硬去重 + 软警告**，不做向量检索。

### 6.1 四层重复

| 层 | 症状 | M2 策略 |
|----|------|---------|
| **A** 同检查点重复提议 | 连说两次「记住」出两条 pending | evidence_fingerprint + pending target |
| **B** 正式条目重复 | 两个 memory 内容相同 | `memory_id` 硬唯一 + summary fingerprint |
| **C** 跨层重复 | prompt 与 memory 说同一件事 | 路由规则 + `related.relation: duplicates` |
| **D** 短期↔久远 | digest 与 memory 双份事实 | digest **不**自动进 evolve；接受前查索引 |

### 6.2 三层指纹

**1. 稳定身份键（硬拦）**

| type | 唯一键 | 冲突时 |
|------|--------|--------|
| `memory` | `id` | 已存在且 `mode: create` → **拒绝**；须 `update` |
| `prompt_patch` | `topic` + `anchor` | 正文或 pending 已有同 anchor → **拒绝 append** |
| `tool_suggestion` | `tool_name` | registry 已有同名 → **拒绝** |

`memory` 的 `id` 建议：`{topic}-{slug}`。

**2. 内容指纹（软警告，已决）**

```text
fingerprint = normalize(summary 或 Proposed 首段)
  → 小写、去标点、去空白（MVP 可取前 120 字）
```

| 命中类型 | 规则 | 行为 |
|----------|------|------|
| **硬警告** | `summary` **精确相等**（normalize 后） | CLI 三选一：合并 / 改 id / 取消 |
| **软警告** | 分词（长度 ≥3）后 **≥2 个相同词** | CLI 提示；**不阻断** |

在以下集合扫描：

- `memories/**` 的 `summary`
- `prompts/<topic>.md` 行级内容
- `proposals/` 中 `status: pending` 的 summary

命中时 CLI（**默认不静默合并**）：

```text
⚠ 可能重复：memories/coding/project-my-agent.md
  [1] 合并进已有  [2] 仍新建（改 id）  [3] 取消
```

**近似重复**：M2 **警告不阻断**；硬拦仅用于同 `id`、同 `evidence_fingerprint`、同 pending `target`。

**3. 证据指纹（硬拦）**

```text
evidence_fingerprint = hash(quote 原文)
```

- 全局已有 **accepted** proposal 含相同 `evidence_fingerprint` → **拒绝再生成**
- 仅有 **pending** → **supersede** 旧 pending（默认，已决）

### 6.3 各阶段动作

| 阶段 | 动作 |
|------|------|
| **生成前** | 注入已有索引；已覆盖则不生成；同检查点候选去重 |
| **写 proposals/** | 同 `target.path` 已有 pending → **supersede**（默认）；本检查点已满 2 条 → 停止 |
| **接受** | 硬查 id/path；软查 fingerprint；`duplicates` 须 archive 旧条或取消 |
| **读侧** | 索引只列 `active`；prompt 每 topic 加载一次；digest 不进 evolve |

`supersede` 留痕：`old.status = superseded`，`superseded_by = <new id>`。

### 6.4 跨层互斥

| 应住 | 若另一层已有类似内容 |
|------|----------------------|
| `prompt` | 警告 memory 重复；建议迁到 prompt + archive memory（**不自动**） |
| `memory` | 警告 prompt 重复；建议删 prompt 句 + 写 memory |
| `tool` | 警告勿写成第三条 memory |

接受时 `relation: duplicates` → 强制选择 **archive 旧的** 或 **取消**。

---

## 7. 接受后路由

```text
用户审阅（接受 / 修改后接受 / 拒绝 / 稍后）
  │
  ├─ memory + create ──► 写入 evolve/memories/<topic>/<id>.md
  ├─ memory + update ──► 目标文件追加 ## 修订 YYYY-MM-DD（不覆盖正文）
  ├─ prompt_patch ─────► append_section 到 evolve/prompts/<topic>.md
  ├─ tool_suggestion ──► status=accepted；evolve_log「待实现」；会话内用 `write_evolve` 写 tools/，或手建/Cursor
  └─ rejected ─────────► status=rejected；可移 proposals/archive/
```

| 项 | 说明 |
|----|------|
| `_index.toml` | M2 **不**自动修改 |
| tool 实现 | 接受后可用 **`write_evolve`** 写 `tools/<topic>/<name>/`（每次 confirm）；或手建/Cursor。`status: active` 后进会话清单 |
| 热重载 | 接受后 **不重载** 当前 session overlay（已决）；下次启动/换主题生效 |
| 冲突字段 | 接受后可在目标 `meta` 或 frontmatter 写 `conflicts_with: [id, ...]` |

### 7.1 审阅时机（已决）

- **当轮 CLI**：生成后「现在审？(y/稍后/拒绝)」
- **离线**：`my-agent proposals list|accept|reject <id>`（续接 thread 后可策展）

---

## 8. 与续接 thread、digest

```text
messages.jsonl（全量落盘）──evidence 抽取、evidence_fingerprint
digest.md（仅减 LLM 上下文）──不自动 proposal；可作 evidence 来源
goal.md + meta.topics ──proposal 默认 topic
conversation_id 不变（续接）──证据去重跨启动有效
```

| 场景 | 行为 |
|------|------|
| **默认续接** | `source.conversation_id` 指向同 thread；evidence 可引用启动前的 messages |
| **digest 压缩** | 不减 evidence 池；压缩前可提示「要说 `记住` 才能进 evolve」 |
| **新会话** | 新 `conversation_id`；**不**软问沉淀（已决 1.A）；检查点计数重置 |
| **exit** | 持久化 session；不开检查点 |
| **换主题** | 不影响已有 pending；新 proposal 默认用 `meta.topics` |

**短期 → 久远** 唯一路径：显式检查点 → proposal → 接受。digest 要点 **不会** 自动升格。

---

## 9. evolve_log 事件

| 事件 | 字段示例 |
|------|----------|
| `checkpoint_opened` | `conversation_id`, `triggered_by`, `trigger_phrase` |
| `proposal_created` | `proposal_id`, `type`, `fingerprint`, `dedup: blocked\|warned\|ok` |
| `proposal_superseded` | `old_id`, `new_id` |
| `evolve_accepted` | `proposal_id`, `type`, `path`, `action: create\|update\|merge` |
| `evolve_rejected` | `proposal_id` |
| `tool_spec_accepted` | `tool_name`, `note: pending_implementation` |

**M4 治理事件**（完整见 [GOVERNANCE.md](./GOVERNANCE.md) §9）：

| 事件 | 字段示例 |
|------|----------|
| `entity_used` | `entity_id`, `type`, `level`, `reason`, `conversation_id` |
| `feedback_positive` / `feedback_negative` | `entity_id`, `note?` |
| `marked_suspect` | `entity_id`, `failure_streak` |
| `suspect_recovered` / `entity_archived` | `entity_id`, `by: user` |
| `review_completed` | `never_used_count`, `suspect_count`, … |
| `audit_completed` | `findings_count`, `scope` |
| `audit_finding_dismissed` / `audit_finding_accepted` | `finding_id` |
| `rollback_noted` | `git_ref`, `paths[]`, `note` |

---

## 10. CLI（M2 最小集）

| 命令 / 对话 | 作用 |
|-------------|------|
| `记住` / `沉淀` / … | 对话内开检查点 |
| `proposals` | 列出 pending |
| `proposals accept <id>` | 接受并路由（§7） |
| `proposals reject <id>` | 拒绝 |

实现见 `TASKS.md` T-402～T-405、T-407。

---

## 11. 端到端流程

```text
[检查点开启]
  → 注入已有索引 + 最近 N 轮 messages + goal + topics
  → 内部结构化意图（JSON，不落盘）
  → 防重复闸门（§6）
  → 生成 1～2 个 .md proposal
  → CLI：「已生成 prop-…，现在审？(y/稍后/拒绝)」

[接受]
  → 硬/软去重最后一道门
  → 按 type 写入 evolve/
  → evolve_log
  → proposal.status = accepted
```

---

## 12. 已决事项（原开放问题）

| # | 议题 | 决议 |
|---|------|------|
| 1 | `新会话` 前软问沉淀 | **否**（1.A）；仅显式 `记住` 等 |
| 2 | LLM 口头升格频率 | **每会话 ≤1 次**；用户 `记住` 不限 |
| 3 | 同 target 已有 pending | **默认 supersede** |
| 4 | `prompt_patch` | MVP 仅 `append_section` |
| 5 | 接受后 prompt 热重载 | **不重载**；下次启动生效 |
| 6 | `evidence_fingerprint` 范围 | **全局**（跨会话） |
| 7 | 软重复 | summary **精确相等**硬警告；**≥2 相同词**（≥3 字符）软警告；见 §6 |
| 8 | 拒绝的 proposal | 保留 `rejected` 状态，可移 `proposals/archive/` |
| 9 | 向量语义去重 | M4+ |
| 10 | `conflicts_with` 自动检测增强 | M4 `review`（memory 软冲突）；prompt 语义见 `audit` |

---

## 13. 验收（EVOLVE 设计阶段）

- [ ] 检查点定义与 ≤2/检查点、exit 不触发无歧义
- [ ] 三类型 proposal 模板可手填验收
- [ ] evidence 仅原文摘录；每条 ≤2
- [ ] 防重复：id / evidence / pending target 硬拦；fingerprint 软警告
- [ ] memory `update` 为追加修订，不覆盖正文
- [ ] tool 接受不生成代码
- [ ] 口头升格 ≤1/会话；`新会话` 不软问；pending 默认 supersede
- [ ] 与 MEMORY / RUNTIME / PROJECT §4.3 交叉引用一致

**M2 实现验收**：对话中说 `记住` → 产生 1 条 memory proposal → 接受 → 下次启动见索引；**不绑** `exit`。

实现见 `TASKS.md` Phase 4（T-401～T-407）。

---

## 14. 文档索引

| 文档 | 内容 |
|------|------|
| [MEMORY.md](./MEMORY.md) | 三件套、主题、读取侧 |
| [RUNTIME.md](./RUNTIME.md) | 续接、digest、触发入口 §9 |
| [TOOLS.md](./TOOLS.md) | evolved 落盘与 registry |
| [PROJECT.md](./PROJECT.md) | 总览 §4.3 进化协议 |
| [TASKS.md](./TASKS.md) | Phase 4 实施 task |
