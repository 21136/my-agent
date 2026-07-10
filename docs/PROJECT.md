# my-agent 项目文档

> 版本 0.2.7 · 2026-07-09 · 个人自用 · Git 为真源，U 盘为便携工作区

---

## 1. 摘要

**my-agent** 是一个仅供作者本人使用的、可随对话逐步「进化」的本地 Agent 系统。它不从「一次性做完全能助手」出发，而是从 **极简内核 + CLI + 少量基础工具** 起步，在与用户的反复交互中，将偏好、流程、脚本沉淀为可审计的 **memories / skills / tools**，从而针对 **个人真实工作流** 变得越来越贴合。

**核心命题**：通用 Agent 无法预先覆盖所有场景；真正有价值的是 **为「这一个用户、这一类任务」长出来的专用层**。

**v0.2.7 变更**：M4 治理 `GOVERNANCE.md`；`review` / `audit`；`ReviewReport` schema。

**v0.2.6 变更**：进化写入 `EVOLVE.md`；检查点 ≤2；防重复三层指纹；memory update 追加修订。

**v0.2.5 变更**：对话层 `RUNTIME.md`；默认续接 session；proposal 显式触发；context digest。

**v0.2.4 变更**：工具 **6 Builtin** + evolved **按主题** + `tools/common/`；统一 **`evolve/_index.toml`**。

**v0.2.3 变更**：记忆 **三件套** + 主题路由；`MEMORY.md`。

**v0.2.2 变更**：**先 tool 后 skill**；新增 `LAYERS.md` / `TOOLS.md` / `TASKS.md`（细分到每个 task）。

**v0.2.1 变更**：去除 Word 专项化；MVP 与验收改为 **领域无关** 的进化闭环验证（Word 等复杂场景见附录 C，仅作可选示例）。

**v0.2.0 变更摘要**（四轮 LLM 评审整合）：

- 补齐 **使用侧闭环**（§4.4）：MVP 用显式 skill 调用 + memory 摘要注入
- **便携架构**：Git 为 source of truth，U 盘为工作副本（§6.1）
- **验收改为行为导向**：测用户净收益，不测条目数量（§7.2）
- **收紧范围**：砍掉多 LLM adapter、SQLite、自动 L3、MVP 自动路由
- **安全与隐私诚实化**：无技术沙箱；对话默认不落盘全文

---

## 2. 背景与动机

### 2.1 通用 Agent 的结构性缺陷

| 问题 | 根因 |
|------|------|
| 预装能力 vs 个人流程错位 | 开发者无法穷举每个用户的习惯、工具链与领域知识 |
| 工具链有损抽象 | 读入时丢失结构/上下文；Agent 在语义层重写，难以保真还原 |
| 「控电脑」不能替代领域工具 | UI 自动化脆、慢；复杂任务应调度 **确定性脚本**，而非点界面 |
| 一次性配置不增长 | Rules/skills 若全靠人工维护，边际成本高，难以越用越贴 |

**结论**：不应追求「出厂即全能」，而应追求 **可积累的个人进化层**——具体领域（文档、代码、内容、数据等）由使用过程中长出来，而非写死在项目定义里。

### 2.2 设计灵感

- Cursor rules / skills / memories：人工沉淀的「进化」
- cheat-on-content 等 skill 体系：可版本化、可复用的流程外挂
- 本项目的差异：**对话驱动、半自动提议、用户确认、可回滚** 的进化协议

### 2.3 与 Cursor 生态的关系（已决）

**并存 + 可导入**，不是替代 Cursor。

- `evolve/skills/<name>/SKILL.md` **兼容 Cursor Agent Skills 格式**（frontmatter + 触发描述）
- my-agent 特有元数据（`use_count`、`evidence`、`status` 等）放在同目录 `meta.json`，**不污染 SKILL.md**
- 可从现有 cheat-* 等 skills 导入；也可在 Cursor 中手工编辑同一 skill 文件

---

## 3. 目标与非目标

### 3.1 目标

1. **个人专属**：只为作者服务，规则与技能可极度私有化
2. **可进化**：从对话中提议并固化 memories / skills / tools
3. **可审计**：用户能打开 `evolve/` 看见 agent「长了什么」
4. **可便携**：`evolve/`、`assets/`、`data/` 可随 U 盘携带；**Git 远端为真源**（§6.1）
5. **价值优先**：MVP 先验证 **进化闭环本身**（能记、能用、能提议），不绑定某一业务场景
6. **用户净收益**：进化以降低用户认知负荷为目标，而非单纯增加 proposal 数量

### 3.2 非目标（明确不做）

| 非目标 | 理由 |
|--------|------|
| 多用户 / SaaS / 计费 | 个人项目 |
| Agent 自主修改 `agent-core` 主程序 | 风险高、难审计 |
| 自主 fine-tune 模型 | 成本与收益不匹配 |
| 任意领域「一次做对」的通用承诺 | 能力应从使用中沉淀，不预置业务逻辑 |
| 全自动静默进化（无用户确认） | 易进化歪、难回滚 |
| MVP 多 LLM adapter / SQLite / 向量检索 | 单人两周内无收益，延后 |
| MVP 自动 L3 工具生成 | 风险高；tool 由用户审阅后放入 |

---

## 4. 核心概念

### 4.1 三层架构

```
┌─────────────────────────────────────────┐
│  对话层                                   │
│  CLI + 基础工具（读文件、跑脚本、列目录）    │
├─────────────────────────────────────────┤
│  进化层 evolve/                           │
│  memories · skills · tools · proposals  │
├─────────────────────────────────────────┤
│  内核 agent-core/                         │
│  加载 evolve、工具调用、进化提议协议         │
└─────────────────────────────────────────┘
```

- **内核**：尽量稳定、变更需人工审核
- **进化层**：越用越厚，是「变完美」的载体
- **对话层**：用户唯一日常入口；目标 **插盘 → 启动 → 对话 < 5 秒**（快捷方式 / `.bat`）

### 4.2 「进化」的定义（分层）

| 层级 | 内容 | MVP 自动度 | 风险 |
|------|------|------------|------|
| L1 记忆 | 偏好、事实、项目背景 | 提议 → 用户确认 | 低 |
| L2 技能 | 流程文档（SKILL.md） | 提议 → 用户确认 | 中 |
| L3 工具 | 脚本、模板填充器 | **仅人工放入**；不自动生成 | 中高 |
| L4 内核 | 改 agent-core 代码 | **不做自动** | 极高 |
| L5 模型 | fine-tune / 换模型 | **不做自动** | 极高 |

**原则：进化的是可看见的外挂，不是不可控的黑盒自改。**

**Curator 角色说明**：用户是进化层的策展人（审 proposal、维护 `assets/` 与 tools、定期审阅）。设计必须 **低打扰**——proposal 宁少勿滥，命中率优先于数量。若进化让用户更累，则进化失败。

### 4.3 进化协议（写入侧）

#### 对话边界（v0.2.5 修订）

- **默认续接**最近 thread（`data/sessions/`）；**`新会话`** 才新建 `conversation_id` 并问 goal/主题
- `exit`：保存 thread 并退出；**不强制** proposal
- `Ctrl+C`：中断当前输入/confirm；**不**结束 thread
- Proposal：**仅显式触发**（见 [EVOLVE.md](./EVOLVE.md)）；每 **检查点** **≤2 条**（同会话可多次检查点）

#### 触发条件（降噪，v0.2.5）

仅在以下情况生成 proposal：

1. 用户显式说「记住这个」「沉淀」「写进 evolve」「以后都这样」等
2. LLM 提议升格 + **用户确认**

**禁止**：任务成功自动提议、`exit` 自动生成。

#### 写入流程

> 完整格式、防重复、路由见 [EVOLVE.md](./EVOLVE.md)。

```
满足触发条件时（开一个检查点）
    → 生成 proposal 写入 evolve/proposals/（≤2 条/检查点）
    → 防重复闸门（id / evidence / fingerprint）
    → 用户审阅：接受 / 修改 / 拒绝 / 稍后
    → 接受：路由至 prompts/、memories/<topic>/ 或 tool spec；追加 evolve_log
    → 拒绝：归档或删除 proposal
```

#### 记录字段

每条进化记录（含 `meta.json`）应包含：

| 字段 | 说明 |
|------|------|
| `id` | 唯一标识 |
| `created_at` | 创建时间 |
| `source` | `conversation_id` |
| `type` | memory \| skill \| tool |
| `evidence` | **MVP：摘录对话原文**（非 LLM 分析，避免自我引用循环） |
| `content_path` | 文件路径 |
| `status` | active \| suspect \| archived |
| `use_count` | 被引用次数 |
| `last_used_at` | 上次引用时间 |
| `conflicts_with` | 可选，冲突条目 id 列表 |

### 4.4 使用侧协议（读取 / 注入 / 反馈）

> v0.1.0 缺失、四轮评审共识补齐。

#### MVP 策略（显式调用 + 主题路由）

> 完整设计见 [MEMORY.md](./MEMORY.md)。

| 类型 | MVP 加载方式 |
|------|----------------|
| **Prompt（特殊要求）** | 按主题分文件；启动仅注入 `_index`；session 目标确认后 LLM 提议 `topics[]` → **用户确认** → 加载对应 `prompts/<topic>.md` |
| **久远记忆** | 启动注入各文件 frontmatter 的 **id + summary** 列表；正文按需 `read_file` 或显式展开 |
| **短期记忆** | 本次会议 `goal.md` + 多轮 `messages` + tool 结果；**不**默认进化进 evolve |
| **Skill** | **仅当用户显式调用**（如「用 xxx skill」）时注入该 skill 全文 |
| **Tool** | **6 Builtin** 始终可用；evolved 在 `tools/<topic>/` 与 `tools/common/`；经 `run_evolved` 调用；会话清单见 `TOOLS.md` |

- **不** 在 MVP 做 skill 自动路由（推迟至 M4+）；**记忆**用主题两阶段路由代替向量检索
- 每次实际引用 memory/skill/tool 时，写入 `evolve_log`：`{ id, used_at, reason, outcome? }`
- **升格**（memory → prompt）：无自动硬规则；使用中 LLM 可主动提议，用户确认后手改或 proposal

#### 失效与反馈（MVP 轻量版）

- **M1–M3**：每次 L2+ 引用写 `entity_used`；**不**弹 exit 问句
- **M4**：`MY_AGENT_FEEDBACK_ON_EXIT=1` 时，`exit` 可选问：「这次用得对吗？」（可跳过）；详见 [RUNTIME.md](./RUNTIME.md) §10
- 连续 3 次否定 → 对应条目 `status: suspect`（`failure_streak` 仅从 log 聚合）
- 用户明示「这条不对」可一次直达 suspect
- `suspect` 条目在下次 `my-agent review` 时人工裁决

#### M4+ 自动路由（预留）

- 读取 `evolve/skills/*/SKILL.md` frontmatter（name / description / triggers）
- 关键词或 LLM 选 **0～1** 个 skill 注入，避免 context 爆炸

### 4.5 冲突、失效与回滚

> 完整设计见 [GOVERNANCE.md](./GOVERNANCE.md)。

- 新 memory 与旧 memory 冲突时，**显式提示**，不静默覆盖
- `evolve_log` + **Git commit** 作为回滚手段；`backup/` 目录延后
- 区分 **「这次临时」** 与 **「以后都这样」**
- **引用级别**：L0 索引注入不算「用过」；L2+ 才算（memory/tool/skill）；prompt 以主题加载为 L1
- M4 **`my-agent review`**（确定性，零 LLM）：never-used、suspect、memory 硬/软冲突
- M4 **`my-agent audit`**（按需 LLM）：prompt 语义矛盾与跨层冲突兜底
- 治理结果 canonical 形态为 **`ReviewReport` JSON**；默认 CLI 输出，支持 `--format` / `-o`

---

## 5. 场景如何沉淀（通用）

本项目 **不预设** 主攻领域（文档、代码、内容、自动化等均由使用过程中决定）。以下为 **领域无关** 的沉淀模式：

### 5.1 通用进化路径

```
反复使用中暴露痛点
    → L1 memory（偏好、事实）
    → L2 skill（可复用流程，SKILL.md）
    → L3 tool（确定性脚本，人工审阅后放入 evolve/tools/）
    → 后续对话显式调用 skill / 执行 tool
```

### 5.2 静态资源（可选）

- `assets/`：用户自备的 **场景相关静态文件**（模板、配置样例、参考数据等），**按需在 skill 中引用**
- 无全局「必须用 assets」的 MVP 要求；某场景不需要则可空置
- 资源应带版本或说明（文件名、`meta.json` 或 skill 内文档），避免静默过期

### 5.3 确定性工具原则

- 复杂、易错、需保真的操作 → **脚本/tool**，不由 LLM 直接「重写一遍」
- Tool 放入 `evolve/tools/` 前须用户审阅；执行前确认（§6.4）
- Agent 的职责：**理解意图 → 选 skill/tool → 填参数**，而非替代专用引擎

### 5.4 无现成流程时的降级

1. 记录痛点为 memory（若用户愿意）
2. 输出 **可执行 checklist** 或分步建议
3. **不** 假装已有自动化；待用户沉淀 skill/tool 后再固化

> 复杂格式文档（如 Word）的处理思路见 **附录 C**，不作为项目核心路径。

---

## 6. 技术考量

### 6.1 部署与便携（已决）

**Git 远端是 source of truth；U 盘是便携工作副本，不是唯一存储。**

| 内容 | 归属 | 同步 |
|------|------|------|
| `agent-core/`、`docs/`、依赖锁文件 | Git | clone / pull |
| `evolve/`、`assets/` | 私有 Git | commit / push |
| `data/state.json` | 本地 + **gitignore** | 含本机路径 |
| `workspace/` | U 盘本地 + **gitignore** | 可能含客户文件 |
| `data/conversations/` | 默认 **仅摘要** 入 Git；全文 optional | 见 §6.4 |
| API key | 本机环境变量 | 不在 U 盘明文 |

**换机流程**：插盘（可选）→ `git clone` / `pull` → 安装 Python → 配置 API key → 启动 CLI。

「插盘即用」保留为体验目标，但文档承认需安装运行时与配置密钥。

### 6.2 U 盘注意事项

| 议题 | 对策 |
|------|------|
| 盘符变化 | 相对路径；启动时检测 agent 根目录 |
| 速度 | 优先移动固态；大模型放本机 SSD |
| 损坏 / 丢失 | **不依赖 U 盘唯一性**；evolve 在 Git |
| 丢失泄露 | 见 §9 R2；对话全文默认不落盘 |

### 6.3 技术栈

#### MVP 固定选择

| 模块 | 选择 |
|------|------|
| 语言 | Python 3.12+ |
| 依赖 | 根目录 `requirements.txt`：**`httpx>=0.27`**（`fetch_url`）；其余 stdlib |
| 界面 | CLI（+ 启动 `.bat` 快捷方式） |
| LLM | **单一 provider**；`llm_client.py` 薄封装，不做多 adapter |
| 状态 | `state.json` + Markdown/JSON 文件树 |
| 版本控制 | Git（私有远端） |

#### 后续扩展（M4+，不在 MVP 实现）

- 多 LLM adapter
- SQLite / 向量检索
- 自动 skill 路由
- Web / TUI
- 进程级沙箱

### 6.4 安全边界（个人版）

- 工具脚本 **约定** 在 `workspace/` 下操作；**无技术沙箱强制**
- 工具安全性依赖：**(a) 用户审阅脚本源码；(b) 执行前确认（默认开启，不可关闭）**
- 支持 **dry-run** 优先（对破坏性/不可逆 tool）
- API key 仅存本机环境变量
- **对话归档（已决）**：
  - **默认**：仅 `data/sessions/<id>/messages.jsonl`（gitignore）；exit **不写** `data/conversations/`
  - `my-agent --record` 或 `exit --record`：追加 `data/conversations/<id>.json`（摘要 **≤200 字** + proposal/evolve 引用），**可进 Git**
  - `--record full`：另存 session 全文至 `data/conversations/<id>-full.jsonl`；启动时提示「U 盘丢失可导致泄露」

---

## 7. MVP 定义

### 7.1 MVP 范围

| 包含 | 不包含 |
|------|--------|
| CLI + 单 LLM + **6 Builtin** + `run_evolved` | Web / TUI |
| 读 evolve、**LLM 调 builtin/evolved tool** | 多 LLM adapter |
| 引用日志（`evolve_log`） | 自动 skill 路由 |
| 显式触发 proposal（≤2 条/检查点，先 memory/tool） | SQLite / 向量检索 |
| 用户审阅后写入 evolve/ | 自动 L3 工具生成 |
| Git 手动 commit 作回滚 | 全自动静默写入 |
| 调用 **已审阅** 的 `evolve/tools/` 脚本 | 自动安装新依赖 |
| 领域无关：不要求特定业务场景 | 预置领域专用逻辑 |
| | **M1 不做 skill 加载**（见 `docs/LAYERS.md`） |

### 7.2 MVP 验收标准（行为导向）

> **两周后，是否值得继续用？**

**必须同时满足：**

1. **真实任务**：至少 **1 次** 个人真实任务完成得比「无 evolve 的通用对话」更省事（用户自述 + `evolve_log` 有引用记录）
2. **进化意愿**：至少 **1 条** proposal 被用户 **主动接受**（非盲点全收）
3. **继续意愿**：用户主观认为下周还会用（自述即可）

**辅助观测（非主判据）：**

- `evolve_log` 中有 skill/memory/tool 引用记录
- `use_count ≥ 1` 的条目 ≥ 1

**废弃指标**：不以「memories ≥ 5 条」等数量型指标作主判据；不以「必须完成某类文档任务」作 MVP 门槛。

### 7.3 里程碑（实施细表见 `docs/TASKS.md`）

| 阶段 | 交付 | 验证什么 |
|------|------|----------|
| **M0** | 目录 + 文档 | 方向对齐 |
| **M1a** | 工具协议 + builtin + evolved 执行器 + CLI 无 LLM 调 tool | 手脚能动 |
| **M1b** | CLI + 单 LLM + tool 调用环 | 对话能调工具 |
| **M1c** | 记忆三件套 + 主题路由 | 便签能贴；**紧随 M1b，不阻塞首版可运行** |
| **M2** | proposal + evolve_log + 防重复（先 memory / tool，**无 skill**） | 进化写入+读取 |
| **M3** | 真实任务固化 **第一条 evolved tool**（或 memory） | 个人价值 |
| **M4** | `my-agent review` + Git 习惯 + **可选** skill 显式加载 | 治理 |

**建设顺序**：`TOOLS.md` 评审 → **Phase 1（M1a）→ Phase 2（M1b）先跑通 tool 环** → Phase 3（M1c）补记忆与主题 → Phase 4… → **Skill 最晚（M4 可选）**。

> **已决**：M1c 不阻塞 M1a/M1b 首次可运行交付；无主题路由时可用 `topics=[]` stub 进入主循环。

**实施细表**：见 [TASKS.md](./TASKS.md)。

---

## 8. 目录规范

```
my-agent/
├── agent-core/
│   ├── main.py
│   ├── loader.py          # 读 evolve、注入 context
│   ├── evolve.py          # proposal 协议
│   └── llm_client.py      # 单一 provider 薄封装
├── evolve/
│   ├── _index.toml          # 主题：prompt + memory + tool_dirs
│   ├── prompts/<topic>.md
│   ├── memories/<topic>/*.md
│   ├── tools/
│   │   ├── common/          # 每 session 列入 evolved 清单
│   │   └── <topic>/<name>/
│   ├── skills/<name>/
│   │   ├── SKILL.md       # 兼容 Cursor 格式
│   │   └── meta.json      # use_count, status, evidence...
│   ├── tools/*
│   └── proposals/*
├── assets/                # 可选：用户静态资源（按场景使用）
├── data/
│   ├── state.json         # gitignore
│   ├── conversations/     # 默认仅摘要
│   └── evolve_log.jsonl
├── data/sessions/<id>/    # goal.md，gitignore
├── workspace/             # gitignore
├── backup/                # 延后使用
└── docs/
```

### 8.1 `meta.json` 示例

```json
{
  "id": "my-workflow",
  "status": "active",
  "source": "conv-2026-07-09-001",
  "evidence": "用户原话：「整理下载目录时先按扩展名分文件夹」",
  "use_count": 3,
  "last_used_at": "2026-07-09T16:00:00+08:00"
}
```

> `failure_streak` 仅从 `evolve_log` 聚合，**不**写入实体文件（见 [GOVERNANCE.md](./GOVERNANCE.md) §6）。

---

## 9. 风险登记

| ID | 风险 | 影响 | 缓解 |
|----|------|------|------|
| R1 | 错误规则被固化 | 任务持续跑偏 | 确认流 + evidence 原文 + 回滚 |
| R2 | U 盘损坏 **或丢失** | 数据丢失 **或敏感内容泄露** | Git 真源；对话默认不落全文；workspace gitignore |
| R3 | 进化层膨胀 | context 爆炸、维护负担 | 显式调用；M4 review；仅注入 0～1 skill |
| R4 | 工具脚本破坏文件 | 文件丢失 | 人工确认 + dry-run；**无技术沙箱** |
| R5 | 盘符/路径变化 | 脚本失败 | 根目录探测；skill 内禁止绝对路径 |
| R6 | 单一 LLM API 变更 | 需改 client | M4 再抽象 adapter |
| R7 | 静态资源过期（assets） | skill 引用失效 | 版本约定；执行前校验 |
| R8 | 确认疲劳 | 进化退化为静默全收 | proposal ≤2/检查点；显式触发；防重复（EVOLVE §6） |
| R9 | 进化测系统不测用户 | 用户更累仍「验收通过」 | §7.2 行为导向验收 |

---

## 10. 开放问题（剩余）

| # | 问题 | 状态 |
|---|------|------|
| 1 | 进化触发时机 | **已决**：§4.3 |
| 2 | 路由机制 | **已决**：MVP 显式；M4+ 自动 |
| 3 | CLI vs Web | **已决**：MVP CLI + 快捷启动 |
| 4 | 本地 embedding | **已决**：MVP 不需要 |
| 5 | 领域专用 tool 是否内置 | **已决**：不预置；由用户沉淀到 `evolve/tools/` |
| 6 | state.json vs SQLite | **已决**：MVP 仅 JSON/文件 |
| 7 | 对话归档 | **已决**：默认摘要；`--record` 全文 |
| 8 | skill 有效性验证 | **已决**：引用日志 + 可选反馈 + suspect |
| 9 | 多 LLM 评审合并 | **已决**：见 §12、REVIEW-SUMMARY.md |
| 10 | 与 Cursor 关系 | **已决**：§2.3 并存 + 格式兼容 |

---

## 11. 术语表

| 术语 | 含义 |
|------|------|
| 内核 | `agent-core/`，稳定、少变 |
| 进化层 | `evolve/`，可增长能力 |
| Curator | 用户：审 proposal、维护 assets/tools、定期 review |
| Proposal | 待确认的进化草稿 |
| assets | 可选静态资源目录，由具体 skill 引用 |
| Source of truth | 私有 Git 远端，非 U 盘唯一副本 |

---

## 12. 文档历史

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-07-09 | 初稿：脚手架 + 项目文档 |
| 0.2.0 | 2026-07-09 | 整合四轮 LLM 评审；见 `docs/REVIEW-SUMMARY.md` |
| 0.2.1 | 2026-07-09 | 去除 Word 专项化；`templates/` → `assets/` |
| 0.2.7 | 2026-07-09 | `GOVERNANCE.md`；review / audit；ReviewReport |
| 0.2.6 | 2026-07-09 | `EVOLVE.md`；检查点；防重复；memory update 追加修订 |
| 0.2.5 | 2026-07-09 | `RUNTIME.md`；续接 session；digest；proposal 显式触发 |
| 0.2.4 | 2026-07-09 | 6 Builtin；主题 tools + common；`evolve/_index.toml` |
| 0.2.3 | 2026-07-09 | 记忆三件套；`MEMORY.md` |
| 0.2.2 | 2026-07-09 | 先 tool 后 skill；`LAYERS` / `TOOLS` / `TASKS` |

### 0.2.2 建设顺序修订

| 决议 | 说明 |
|------|------|
| Skill 推迟 | M1 不做 skill；M4 可选显式加载 |
| 工具优先 | M1a 工具层 → M1b LLM 调 tool → 再 memory/proposal |
| 实施细表 | 见 `docs/TASKS.md`（T-001～T-906） |

### 0.2.1 作者修订

| 决议 | 说明 |
|------|------|
| 不预设主攻场景 | Word 移至附录 C，不作 MVP 硬依赖 |
| 取消 M0.5 Word 脚本 | 里程碑 M0→M1→M2→M3→M4 直接验证进化闭环 |
| `templates/` 改名 `assets/` | 通用静态资源，按需使用 |

### 0.2.0 评审决议摘要

> 以下部分项已于 **v0.2.1** 调整（见上表）：含 M0.5、Word 专项验收、母版生命周期硬要求。

| 决议 | 采纳来源 | v0.2.1 |
|------|----------|--------|
| 新增 §4.4 使用侧协议 | 四轮共识 | 保留 |
| MVP 显式 skill 调用，自动路由 M4+ | Cursor、Claude、Grok | 保留 |
| M0.5 先于 agent-core | 四轮共识 | **取消** |
| Git 真源 + U 盘工作副本 | 四轮共识 | 保留 |
| 兼容 Cursor SKILL.md + `meta.json` | Cursor、Claude、GPT-5.5 | 保留 |
| 验收改行为导向 | 四轮共识 | 保留（改为领域无关） |
| evidence 用对话原文摘录 | Claude | 保留 |
| 对话边界 = CLI session 至 exit | Claude | 保留 |
| 母版生命周期 §5.4 | Claude、GPT-5.5 | **移至附录 C，非 MVP** |
| 无技术沙箱诚实声明 | 四轮共识 | 保留 |
| 拒绝：第二 LLM 审校 proposal | 四轮共识 | 保留 |
| 延后：`expires_at` / `confidence` 逻辑 | Grok、Cursor | 保留 |

---

## 附录 A：与「Computer Use」的关系

控制电脑（点 UI）适合偶发操作，**不适合**作为复杂任务的主路径：读不全、点不准、误差累积。

有价值的路径是：**Agent 调度确定性 tool**（脚本、专用库、用户审阅过的自动化），而非 Agent 充当各领域引擎。

Computer Use 可作为可选手段；**主路径是 evolve 中的 skills + tools**。

## 附录 B：个人使用声明

本项目不面向公众发布。MVP **不含技术沙箱**；安全依赖人工审阅脚本与执行前确认。`workspace/` 与对话全文可能含敏感内容，请按 §6.4 管理归档策略。

## 附录 C：可选场景——复杂格式文档（示例）

> **非项目核心**；若你日后需要处理 Word 等版式复杂文件，可按此思路 **自行沉淀** skill/tool，无需写进内核。

| 要点 | 说明 |
|------|------|
| 问题 | 通用 Agent 读 docx 有损，无法可靠「复刻版式」 |
| 可行策略 | 母版 + 定点替换（书签/占位符），**禁止**从零生成复杂 docx |
| 资源 | 母版放 `assets/`，由对应 skill 引用 |
| 工具 | `evolve/tools/` 内脚本（如 docxtemplater、COM）；执行前校验 |
| Computer Use | 不能替代上述确定性链路 |

此附录保留讨论背景，**不影响 MVP 范围与验收**。
