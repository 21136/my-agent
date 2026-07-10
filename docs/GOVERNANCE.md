# 治理设计（GOVERNANCE）

> 版本 0.2.10 · 2026-07-09 · M4 设计文档（**不写 agent-core 代码**）  
> 与 [PROJECT.md](./PROJECT.md) §4.4–4.5、[EVOLVE.md](./EVOLVE.md)、[MEMORY.md](./MEMORY.md)、[TOOLS.md](./TOOLS.md) 配套

---

## 1. 目标

定义 my-agent **进化层治理**：发现膨胀/失效/冲突，**裁决权在人**；回滚靠 Git；审计靠 `evolve_log`。

**M4 范围**：

| 包含 | 不含 |
|------|------|
| `my-agent review`（确定性清单） | 自动 prune / archive |
| `my-agent audit`（LLM 语义审计，按需） | 向量语义去重（M4+） |
| suspect 标记与恢复约定 | skill 自动路由（M4+） |
| `ReviewReport` canonical JSON + 多格式输出 | `review apply` 一键改文件 |
| Git 回滚习惯 | `backup/` 目录 |

写入侧见 [EVOLVE.md](./EVOLVE.md)；读取/引用见 [MEMORY.md](./MEMORY.md) §8。

---

## 2. 设计原则

| 原则 | 说明 |
|------|------|
| **发现自动化，裁决人工** | CLI 只列清单与建议；不静默删改 evolve |
| **Git 是真源，log 是索引** | 内容回滚用 `git`；`evolve_log` 记谁用过、何时失效 |
| **轻量可跳过** | 反馈问句、review 周期、audit 均可跳过 |
| **确定性 vs LLM 分层** | `review` 零 LLM；`audit` 花 token 做语义兜底 |
| **canonical JSON** | 所有输出模式共享 `ReviewReport` schema，便于未来 evolved tool |

---

## 3. 引用级别（「未引用」定义）

| Level | 事件 | 算不算「有效引用」 |
|-------|------|-------------------|
| L0 | 启动时 memory **id + summary** 索引注入 | **否** |
| L1 | 主题确认后 **prompt 全文**加载 | prompt：**是**；memory 索引仍 **否** |
| L2 | `read_file` 读取 **`evolve/memories/**`** 正文（见 §3.1） | memory：**是** |
| L3 | `tool_invoked` / `run_evolved` 成功执行 | tool：**是** |
| L4 | skill **显式**加载全文 | skill：**是** |

`review` 的 **never-used** 默认只统计 **L2+（memory/tool/skill）** 与 **L1+（prompt）**；L0 不计入，避免几乎所有 memory 误报为「已用」。

### 3.1 L2 路径规则（`entity_used`，已决）

| 路径 / 行为 | 是否 L2 |
|-------------|---------|
| `read_file` 且路径在 **`evolve/memories/`** 下（含子目录 `.md`） | **是** → `entity_used`，`level: L2` |
| `read_file` 读 `workspace/**`、`evolve/prompts/**`、agent-core 等 | **否** |
| 启动注入的 memory **id + summary** 索引 | **否**（L0） |

实现：`read_file` executor 在成功返回前按路径判定；`entity_id` 取该 memory 文件 frontmatter 的 `id`。

---

## 4. 治理分层

```text
提案阶段（EVOLVE 已有）
  anchor 重复拒绝 · 跨层 duplicates 警告
        │
        ▼
review（确定性，默认，零 LLM）
  never-used · suspect · memory 硬/软冲突
  【不扫 prompt 语义】
        │
        ▼
audit（LLM，不定期 / 按需）
  prompt 内矛盾 · 跨层语义冲突 · 过时规则
```

---

## 5. `my-agent review`（确定性）

### 5.1 命令

```bash
my-agent review                              # 默认：cli → stdout
my-agent review --format json                # canonical JSON
my-agent review --format markdown -o path    # 落盘（显式 -o）
my-agent review --topic coding               # 缩小 scope（可选）
```

### 5.2 输出块

| 块 | 内容 |
|----|------|
| **Summary** | 条目计数、suspect/conflict/never-used 数量 |
| **Never-used** | `active` 且 L2+ 无引用；创建 ≥14 天 |
| **Observation** | 创建 &lt;14 天且未引用（观察期，非问题） |
| **Pending implementation** | `tool_suggestion` 已接受但未 `active` |
| **Conflicts (hard)** | `conflicts_with` 双向仍 `active` 的 memory↔memory |
| **Conflicts (soft)** | 同 topic 两 `active` memory：`summary` 分词（长度 ≥3）后 **≥3 个相同词** → 警告（§5.2.1） |
| **Suspect** | `status: suspect` 的条目 |

**不含**：prompt 文件内语义矛盾、跨 topic 优先级——交给 §7 `audit`。

### 5.3 过滤规则

- `status: archived` — 不出现在默认清单
- 创建 **&lt; 14 天** 且 `use_count=0` — 进 **Observation**，不进 Never-used
- `staged` tool — 进 **Pending implementation**

### 5.2.1 软冲突算法（已决）

对同一 `topic` 下任意两条 `status: active` 的 memory（`id` 不同）：

1. 取 frontmatter `summary`，按空白分词，保留长度 **≥3** 的 token（小写）
2. 若交集 **≥3 个词** → 记入 `conflicts_soft`（**仅警告**，不自动改 `conflicts_with`）
3. 停用词表 MVP **不做**；M4+ 可扩展

---

## 6. suspect：标记与恢复

### 6.1 状态机

```text
active ──(连续否定)──► suspect ──(人工)──► active | archived
```

### 6.2 触发

| 信号 | 效果 |
|------|------|
| 任务结束问「这次用得对吗？」→ **否** | `failure_streak` +1（见 log） |
| 用户明说「这条不对」「别再用 xxx」 | +1；可一次直达 suspect |
| tool 失败且用户否定 | +1 |
| **是** / 正面反馈 | `failure_streak` 归零 |

**阈值**：`failure_streak >= 3` → 写 `status: suspect` + log `marked_suspect`。

**存储（已决）**：

- `status` 写入实体文件（memory frontmatter / `tool.toml` / `meta.json`）
- `failure_streak` **仅从 `evolve_log` 聚合**，避免双写不同步

### 6.3 运行时行为

| 类型 | suspect 时 |
|------|------------|
| memory 索引 | 仍列出，标注 `(suspect)` |
| memory 展开 | 允许，CLI 警告 |
| tool | **不进** evolved 清单；`run_evolved` 拒绝 |
| skill | 显式加载时警告 + 需确认 |

### 6.4 恢复

人工改 `status` 或后续 CLI 扩展；约定动作：

| 动作 | 效果 |
|------|------|
| **recover** | `status→active`，`failure_streak` 视为 0，log `suspect_recovered` |
| **archive** | `status→archived`，log `entity_archived` |

M4 **不做** `review apply`；改文件 + 建议 `git commit`。

### 6.5 exit 反馈协议（已决）

> 对话层细节见 [RUNTIME.md](./RUNTIME.md) §10；实现 T-602。

| 项 | 决议 |
|----|------|
| 分期 | M1–M3 仅 `entity_used`；M4 起 exit 问句 + `feedback_*` |
| 开关 | 默认 **关**；`MY_AGENT_FEEDBACK_ON_EXIT=1` 启用 |
| 触发 | **仅** `exit`（有待反馈 L2+ 实体时）；不问 prompt（L1） |
| 多实体 | 只问一个：L4 > L3 > L2，同层 `last_used_at` 最新 |
| 跳过 | 回车 / `skip` → 不写 log，streak 不变 |
| 明示否定 | 「这条不对」「别再用 xxx」→ +1 或 **一次直达** suspect |

---

## 7. `my-agent audit`（LLM 兜底）

### 7.1 定位

**Prompt 冲突与语义矛盾的兜底**：不定期或感到「规则打架」时运行；**不**并入每次例行 `review`。

### 7.2 命令

```bash
my-agent audit                    # 默认 scope：全部 active prompt + 同 topic memory
my-agent audit prompts            # 仅 prompt 文件
my-agent audit --topic coding     # 单主题
my-agent audit --only-llm         # 只输出 llm_findings（不重复打印确定性块）
my-agent audit --format json -o -
```

### 7.3 流程

1. 调用与 `review` 相同的 `ReviewCollector.collect()`（确定性数据）
2. LLM 阅读：`evolve/prompts/<topic>.md`、同 topic `active` memory（summary + 按需正文）
3. 可选输入：当前 `suspect` / `never-used` 清单作上下文
4. 结果写入同一 `ReviewReport.llm_findings[]`
5. log `audit_completed`（摘要）；完整 report 经 `-o` 或 tool 返回

### 7.4 LLM 任务（固定审阅模板）

- 互相矛盾的规则（prompt 段内、prompt↔memory、多 topic 若本次在 scope 内）
- 过时 / 不再适用（对照 `evidence`、`last_used_at`）
- 该升格或该归档（memory 像硬规则、prompt 像软事实）

### 7.5 `llm_findings` 条目格式

```json
{
  "finding_id": "lf-001",
  "kind": "contradiction",
  "severity": "high",
  "entities": [
    {"type": "prompt", "topic": "coding", "anchor": "默认编码"},
    {"type": "memory", "id": "encoding-pref"}
  ],
  "summary": "prompt 要求 UTF-8，memory 写 GBK",
  "evidence": ["摘录…"],
  "suggested_action": "archive memory | edit prompt",
  "confidence": "medium"
}
```

**不自动改文件**。用户认可 → 手改 / proposal / archive / commit。误报可 log `audit_finding_dismissed`；已裁决可选手写 `evolve/prompts/<topic>.meta.json` 的 `dismissed_findings[]`。

### 7.6 `conflicts_with` 与 prompt

| 实体 | `conflicts_with` |
|------|------------------|
| memory / tool / skill（有 `id`） | ✅ |
| prompt 文件 | ❌ 不用 id 级字段 |
| prompt 内 / 跨层语义问题 | `audit` → `llm_findings` |

---

## 8. `ReviewReport` schema

治理结果的 **canonical 形态**；CLI、落盘、未来 evolved tool 均消费此结构。

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-07-09T18:47:00+08:00",
  "scope": {
    "log_window_days": 90,
    "topics": ["coding"],
    "include_observation_period": true,
    "audit_ran": false
  },
  "summary": {
    "memories": 12,
    "prompts": 3,
    "tools": 2,
    "skills": 0,
    "never_used_count": 5,
    "suspect_count": 3,
    "conflict_hard_count": 1,
    "conflict_soft_count": 1,
    "llm_findings_count": 0
  },
  "never_used": [],
  "observation_period": [],
  "pending_implementation": [],
  "conflicts_hard": [],
  "conflicts_soft": [],
  "suspect": [],
  "llm_findings": []
}
```

### 8.1 输出模式

| mode | M4 | 说明 |
|------|-----|------|
| `cli` | ✅ 默认 | 人类 5 分钟扫清单 |
| `json` | ✅ | 脚本、LLM tool、`jq` |
| `markdown` | ✅ | `-o` 落盘、可选 commit |
| `jsonl` | 预留 | 大仓库流式 |

### 8.2 架构（实现时）

```text
ReviewCollector.collect(options) → ReviewReport
ReviewRenderer.render(report, mode) → str
ReviewSink.emit(content, target)   → stdout | file
```

新增输出模式 **只加 renderer**，不改 `collect()`。

### 8.3 落盘约定

| 命令 | 目标 | Git |
|------|------|-----|
| 默认 | stdout only | — |
| `-o data/reviews/latest.md` | 本地对照 | gitignore |
| `-o evolve/reviews/YYYY-MM-DD.md` | 策展留档 | 建议 commit |

**默认不写文件**；留痕需显式 `-o`。

### 8.4 未来 LLM tool（可选，T-601b）

Builtin 仍 **6 个封顶**（[TOOLS.md](./TOOLS.md)）。扩展路径：

`evolve/tools/common/governance_review/` 经 `run_evolved` 调用同一 `ReviewCollector`：

```json
{
  "action": "review",
  "format": "json",
  "topic": "coding"
}
```

返回 `{ "ok": true, "report": { ... } }`。M4 文档 + schema + CLI 为先；tool 壳子可选。

---

## 9. evolve_log 与 Git

### 9.1 分工

| | evolve_log | Git |
|--|------------|-----|
| 粒度 | 事件流 | 文件快照 |
| 回滚 | 不删；追加 `rollback_noted` | `git checkout` / `revert` |
| review | `review_completed` 摘要 | — |

### 9.2 引用与反馈事件

| 事件 | 字段示例 |
|------|----------|
| `entity_used` | `entity_id`, `type`, `level`, `reason`, `conversation_id` |
| `feedback_positive` | `entity_id`, `note?` |
| `feedback_negative` | `entity_id`, `note?` |
| `marked_suspect` | `entity_id`, `failure_streak` |
| `suspect_recovered` | `entity_id`, `by: user` |
| `entity_archived` | `entity_id`, `by: user` |
| `review_completed` | `never_used_count`, `suspect_count`, … |
| `audit_completed` | `findings_count`, `scope` |
| `audit_finding_dismissed` | `finding_id` |
| `audit_finding_accepted` | `finding_id`, `action?` |
| `rollback_noted` | `entity_id?`, `git_ref`, `paths[]`, `note` |

`entity_used` 时更新实体上 `use_count` / `last_used_at`（文件为准，log 可交叉验证）。

### 9.3 Git 习惯

1. 每次 `proposals accept` 成功 → 提示 `git commit -m "evolve: accept <id>"`
2. review / audit 后若 archive 或批量修改 → 提示 commit
3. 误接受 → `git log -- evolve/...` → `git checkout <hash> -- <path>` → log `rollback_noted`

**不做**：log 与 commit 自动绑定、一键 `my-agent rollback`（M4+ 再议）。

---

## 10. 推荐节奏（个人项目）

| 时机 | 动作 |
|------|------|
| 每次 accept | `git commit`（30s） |
| 每 2 周或 evolve 条目 +5 | `my-agent review`（~5 min） |
| 大改 prompt / 感觉规则打架 | `my-agent audit` |
| 出问题时 | `git checkout` + `rollback_noted` |

---

## 11. M4 验收

- [ ] 真实任务后 `evolve_log` 有 `entity_used`（L2+）
- [ ] 连续 3 次否定 → `suspect`；tool 不可 `run_evolved`
- [ ] `my-agent review` 列出 never-used / suspect / hard+soft conflict
- [ ] `my-agent review --format json` 输出合法 `ReviewReport` v1.0
- [ ] `my-agent audit` 填充 `llm_findings`；不自动改文件
- [ ] recover 后 tool 恢复可调用
- [ ] 误 accept 可用 git 恢复 + `rollback_noted`

实现任务见 [TASKS.md](./TASKS.md) Phase 6。

---

## 12. 文档索引

| 文档 | 内容 |
|------|------|
| [PROJECT.md](./PROJECT.md) | §4.4–4.5 总览 |
| [EVOLVE.md](./EVOLVE.md) | 提案阶段闸门、M2 事件 |
| [MEMORY.md](./MEMORY.md) | 引用级别、session 审计 |
| [RUNTIME.md](./RUNTIME.md) | §10 exit 反馈协议 |
| [TOOLS.md](./TOOLS.md) | tool `status`、Builtin 封顶 |

---

## 13. 已决事项

| # | 议题 | 决议 |
|---|------|------|
| 1 | 未引用定义 | L0 不算；L2+ 算（memory/tool/skill）；prompt 用 L1 |
| 2 | suspect 计数 | log 聚合 `failure_streak`；`status` 写文件 |
| 3 | prompt 冲突 | M4 不自动；`audit` LLM 兜底；`conflicts_with` 仅有 `id` 实体 |
| 4 | review 输出 | 默认 CLI；canonical `ReviewReport`；`--format` / `-o` |
| 5 | audit 与 review | `audit` 先 `collect()` 再 LLM；`--only-llm` 可选 |
| 6 | evolved tool | T-601b 可选；不增 Builtin |
| 7 | exit 反馈 | 默认关；`MY_AGENT_FEEDBACK_ON_EXIT=1`；仅 exit、L2+、单实体；见 §6.5 |
| 8 | L2 `entity_used` | 仅 `read_file` 命中 **`evolve/memories/**`**；见 §3.1 |
| 9 | 软冲突 | 同 topic summary **≥3 相同词**（词长 ≥3）；§5.2.1 |
