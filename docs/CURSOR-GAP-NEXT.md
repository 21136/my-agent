# Cursor 差距收口（Phase 42 · 三轨）

> 版本 **0.1.0** · 2026-08-04 · **状态：文档已签 · 实现待「开始吧」**  
> 用户选题：写操作分层免确认 · 代码发现（Glob/语义）· 模型路由（Harness P3）  
> 基线：[CURSOR-ALIGN.md](./CURSOR-ALIGN.md) **§6 已签**（Track A～F 主体 done）· [AGENT-HARNESS.md](./AGENT-HARNESS.md) P1/P2/P4/P5 done  
> 关联：[CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · [WRITE-SCOPE.md](./WRITE-SCOPE.md) · [run_command_policy.py](../agent-core/run_command_policy.py) · [LLM-ROUTING.md](./LLM-ROUTING.md) · [TOOLS.md](./TOOLS.md)

---

## 0. 一句话

CURSOR-ALIGN 七轨补齐 **「能跑」**；本 Phase 补齐 **「写起来像 Cursor」** 的三块摩擦：**少点确认卡 · 大仓找得到 · 该强的步骤用强模型**。

**纪律**：先签本文 + 子册 → 再实现；每轨独立 IT/S；**不新增十个 `*_exec`**。

---

## 1. 三轨总表

| 轨 | 名称 | 对标 Cursor | 主交付 | Task 段 |
|----|------|-------------|--------|---------|
| **H** | 写操作分层确认 | Agent 改项目文件少弹窗 | `write_policy.py` + executor | T-4210～T-4214 |
| **I** | 代码发现 | Glob / `@codebase` | builtin `glob`（M0）· 语义（M2 defer） | T-4220～T-4225 |
| **J** | 模型路由 | 规划强、执行快 | [LLM-ROUTING.md](./LLM-ROUTING.md) | T-4201～T-4203 |

**推荐顺序**：**H → J → I(M0)**。H 直接减摩擦；J 减胡试；I 的 Glob 可并行但次于前两者。

### 1.1 提示词分层（AGENT-HARNESS E/F）

> 纪律见 [AGENT-HARNESS.md](./AGENT-HARNESS.md) §2.1：**确认/路由用代码（C/D）**；**选工具写 INDEX（E）**；**core.txt 只留硬边界（F）**。

| 轨 | **F · core.txt** | **E · INDEX / buckets / loader** | **不改** |
|----|------------------|-----------------------------------|----------|
| **H** | **基本不动**（见 §2.11） | `INDEX.md` 脚注 1 行；`buckets/write.md` 可选 1 条 | 不把「免确认」写成长教程 |
| **I** | **要改**（边界表 · 并行只读 · ask 模式 · builtin 计数） | INDEX 一行 + `loader.format_capability_hints` + builtin `description` | 不把 glob 语法灌进 core |
| **J** | **不动**（J6） | **不动** | 子代理/plan prompt 也不按模型分叉 |

**为何 H 几乎不改 prompt**：与 `run_command` A2 对称——executor 静默分层，**模型仍按「须 tool 结果」纪律调用**；`core.txt` 第 43 行「不得绕过 confirm」保留（防 `dry_run` 诡计），不教「项目内可不弹卡」。

**为何 I 必须改 prompt**：新 builtin 进 LLM function 列表后，若 `core.txt` / INDEX / hints 仍只写 `list_dir`+`grep`，模型会继续 **递归 list_dir 或瞎猜路径**——Glob 的价值在 **选工具层（E+F 短句）**，不在 schema  alone。

**任务挂钩**：T-4215（H 提示词）· T-4223（I INDEX/hints）· T-4226（I core.txt）— 与代码任务同 PR 验收。

---

## 2. Track H — 写操作分层确认

### 2.1 痛点

| 现状 | 问题 |
|------|------|
| `run_command` 已在 **project_root** 内对 build/test/readonly **免确认**（Phase 29 A2） | 写操作未对称 |
| `write_text` proxy / evolved | `allow_approve_all=true` → 仅 session **`a`** 可跳过；**每文件仍弹卡** |
| `patch_file` | `allow_approve_all=false` → **永远确认** |
| 项目写码节奏 | 一轮改 5 个文件 = 5 次确认 → 远慢于 Cursor |

### 2.2 目标

在 **已绑定项目** + **路径落在 project_root** + **非敏感路径** 时，对 **`write_text` / `patch_file`（含 proxy）** 实行与 `run_command` 同哲学的 **A2 分层**。

### 2.3 已决（H 系列 · 默认提案）

| ID | 决议 |
|----|------|
| **H0** | 新模块 `write_policy.py`，镜像 `run_command_policy.py` API 风格 |
| **H1** | 仅当 `session.project_root` 非空且 `active_shell=project`（或等效 project 绑定）时启用放宽 |
| **H2** | 目标路径 resolve 后须在 `project_root` 下（复用 `working_dir_under_project` 同类逻辑） |
| **H3** | **免确认类**：project 内 **源码/配置** 的 `patch_file`；project 内 **overwrite 已有文件** 的 `write_text` |
| **H4** | **仍确认**：project 外；`on_conflict` 新建（路径不存在）；**敏感相对路径**（见 §2.4）；`host:`；`dry_run=false` 的 **计划域四件套**（已有 executor 写拒 + 门） |
| **H5** | `copy_move` / `move_to_trash` / `write_evolve` / `git_*` **不在 H 轨放宽**（M0） |
| **H6** | 免确认时 **仍走** WRITE-SCOPE deny-list、计划门、Progress Gate；仅跳过 **confirm 卡** |
| **H7** | 桌面 confirm 预览须带 `Write policy: skip:project_source` 类理由（对齐 run_command） |
| **H8** | session `a`（approve_all）保留；与分层 **叠加取最宽**（已是 a 则不弹） |

### 2.4 敏感路径（默认 deny 放宽）

路径匹配（相对 project_root，大小写不敏感）：

| 模式 | 理由 |
|------|------|
| `TASKS.md` `MAP.md` `PROJECT.md` `ENV.md`（项目三件套 + 计划域） | 走 `plan_partner` + 采纳门 |
| `**/.env` `**/.env.*` | 密钥 |
| `**/credentials*` `**/*secret*` | 密钥启发式 |
| `data/sessions/**` | 会话数据 |
| `.git/**` | 已由 deny-list 拦写 |

**开放**：项目内 `docs/**` 是否免确认？**默认倾向：免**（与 Cursor 改 README 一致）；若与 Plan 域冲突，靠路径表精确列 **仅三件套 + ENV** 不放宽。

### 2.5 分类 API（草案）

```python
def write_requires_confirm(
    *,
    tool: Literal["write_text", "patch_file"],
    path: str,
    project_root: str,
    on_conflict: str = "skip",
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Returns (needs_confirm, reason)."""
```

| `reason` 示例 | 含义 |
|---------------|------|
| `skip:project_patch` | project 内 patch |
| `skip:project_overwrite` | 覆盖已有文件 |
| `confirm:outside_project` | 路径越界 |
| `confirm:plan_domain` | 计划域敏感文件 |
| `confirm:new_file` | write 新建（可选：M1 也放宽新建） |
| `confirm:dry_run_false` | — |

### 2.6 落点

| 文件 | 改动 |
|------|------|
| `agent-core/write_policy.py` | **新建** · 纯函数 + demo |
| `agent-core/tools/executor.py` | `_needs_confirm`：proxy 与 `run_evolved` 的 write_text/patch_file 分支 |
| `agent-core/tool_proxies.py` | 无 schema 变更 |
| `desktop` confirm 预览 | 展示 write policy reason（复用 run_command 行） |

**不改** `tool.toml` 的 `policy.confirm` 默认值（executor 层覆盖，与 run_command 一致）。

### 2.7 非目标（H 轨）

| 非目标 | 说明 |
|--------|------|
| grow / 非 project 会话全面免确认 | 仅 **project 绑定** 放宽 |
| 取消 `write_evolve` confirm | 进化写仍每次确认 |
| 取消 host 写 confirm | 不变 |

### 2.8 DOC-04 · 回归

| 面 | 档位 |
|----|------|
| CONFIRM-PIPELINE · executor | P1 |
| project 计划门 · WRITE-SCOPE | P0 回归 |
| Progress Gate | P1 回归（写≠勾） |

| ID | 场景 |
|----|------|
| **IT-421** | project 内 patch → 不 confirm |
| **IT-422** | project 外 write → confirm |
| **IT-423** | `TASKS.md` patch → confirm |
| **IT-424** | proxy `write_text` 与 `run_evolved` 路径一致 |
| **S-421** | 人工：项目写码一轮 3 patch 无连点确认 |

### 2.9 开放问题

| # | 问题 | 默认 |
|---|------|------|
| H-Q1 | project 内 **新建** `write_text` 是否免确认？ | **M0 仍确认**；M1 可签放宽 |
| H-Q2 | `patch_file` 是否改 `allow_approve_all=true`？ | **否**；靠 policy 分层，不靠 a |
| H-Q3 | default perspective 写 agent-core 文档（非 project）？ | **仍确认** |

### 2.10 任务

| ID | 内容 | 状态 |
|----|------|------|
| T-4210 | 本文 H 轨 + CONFIRM-PIPELINE 指针 | doc |
| T-4211 | `write_policy.py` + IT-421～424 | todo |
| T-4212 | executor + confirm 预览 reason | todo |
| T-4213 | S-421 手工 | **done** |
| T-4214 | M1：新建文件免确认（Pack 2 · =T-5202） | **done** |

### 2.11 提示词（F/E · H 轨）

| 层 | 改什么 | 默认 |
|----|--------|------|
| **F · core.txt** | **不改**执行边界表主句；**保留** Forbidden「不得绕过 confirm」 | 与 `run_command` 分层一致： harness 减卡，prompt 不教豁免 |
| **F · Project mode** | 可选加 1 句：「项目源码 patch 由执行器按策略处理 confirm」 | **M0 省略**（避免模型误以为可跳过计划门） |
| **E · INDEX.md** | 脚注：`project` 绑定下部分写操作 confirm 由 `write_policy` 分层（见 CONFIRM-PIPELINE §11） | **1 行** |
| **E · buckets/write.md** | 可选：「项目内 patch / 覆盖已有文件：执行器或免确认（仍 WRITE-SCOPE）」 | **1 条** |
| **E · loader hints** | **不改**（confirm 非选工具问题） | — |

**验收**：`grep core.txt` 无「项目内写不用 confirm」类长段；IT-421 不依赖 prompt 变更。

| ID | 内容 | 状态 |
|----|------|------|
| T-4215 | H 轨 E 层脚注（INDEX/write 可选）+ 确认 core 不动 | todo（随 T-4212） |

---

## 3. Track I — 代码发现（Glob → 语义）

### 3.1 痛点

| Cursor | my-agent |
|--------|----------|
| `Glob` / `file_search` 按名快速列文件 | 仅 `list_dir`（浅/递归重）+ `grep`（要 pattern） |
| `@codebase` 语义搜 | **无**（Phase 9 T-903 曾 wontfix；现用户重开 **M2**） |

大仓「先找文件再读」比再加 exec 工具更值。

### 3.2 分档

| 档 | 内容 | 默认 |
|----|------|------|
| **I0** | 设计 + DOC-04 | 本文 |
| **I1 M0** | **Builtin `glob_file_search`**（或名 `glob`）：`pattern` + `path` + 上限 | **做** |
| **I2 M1** | 尊重 `.gitignore` / 默认跳过 `node_modules`（与 rg 对齐） | **做** |
| **I3 M2** | 本地语义索引（embedding + sqlite/文件） | **defer** · 单独立项 |

### 3.3 M0：`glob_file_search` 契约（草案）

| 参数 | 说明 |
|------|------|
| `pattern` | glob，如 `**/*.py`、`**/test_*.ts` |
| `path` | 相对 agent root，默认 `.` |
| `max_results` | 默认 200，硬顶 1000 |
| `ignore_case` | 可选 |

| 返回 | 说明 |
|------|------|
| `paths[]` | 相对 path 根的文件路径 |
| `truncated` | 超限截断 |

**实现倾向**：Python 3.12+ `pathlib.Path.rglob` + fnmatch；**优先**调用 `rg --files -g`（与 grep builtin 一致）。

**confirm**：**否**（只读，与 grep 同级）。

**LLM 面**：第 **7** 个 builtin，或替代弱用法 `list_dir(recursive=true)` 的文档引导——**默认新增第 7 builtin**（比挤进 grep 更清晰）。

### 3.4 M2 语义搜（defer 设计摘要）

| 项 | 提案 |
|----|------|
| 索引范围 | 当前 `project_root` 或 agent root 可配置 |
| 存储 | `data/indexes/<project_id>/`（gitignore） |
| 触发 | 手动 `index_refresh` evolved 或 project 确认后后台 |
| 查询 | builtin `codebase_search` query + top_k |
| 成本 | embedding API 或本地小模型 — **签字时选** |

**不在 Phase 42 M0 实现**；仅预留 IT-425 与 TASK id。

### 3.5 DOC-04 · 回归

| 面 | 档位 |
|----|------|
| builtin 列表 / agent.build_llm_tools | P1 |
| paths 越界 | P0 |

| ID | 场景 |
|----|------|
| **IT-430** | glob 基本匹配 + max_results |
| **IT-431** | 越界拒绝 |
| **IT-432** | gitignore 跳过（M1） |
| **S-430** | 人工：「找所有 test_*.py」一轮命中 |

### 3.6 任务

| ID | 内容 | 状态 |
|----|------|------|
| T-4220 | 本文 I 轨 + TOOLS.md §7 草约 | doc |
| T-4221 | M0：`builtin/glob_file_search.py` + IT-430/431 | todo |
| T-4222 | agent 第 7 builtin + loader 摘要 | todo |
| T-4223 | tool-catalog INDEX 一行 | todo |
| T-4224 | M1：gitignore · IT-432 | todo |
| T-4225 | M2：语义搜设计签字（`CODEBASE-SEARCH.md`） | defer |

### 3.7 提示词（F/E · I 轨）

| 层 | 改什么 |
|----|--------|
| **F · core.txt** | 执行边界表加 `glob_file_search`（找文件按 pattern，非内容搜） |
| **F · core.txt** | Tool discipline：大仓找文件 **先 `glob_file_search`**，再 `read_file`；`grep` 仍用于**内容** |
| **F · core.txt** | 并行只读列表 + **只聊**模式列表加入 `glob_file_search` |
| **F · core.txt** | 「6 builtins」→ **7**（含 proxy 表述不变） |
| **E · INDEX.md** | 读/发现行：`read_file` · `list_dir` · **`glob_file_search`** · `grep` … |
| **E · buckets** | 新建 `buckets/discover.md`（**可选**）或只在 INDEX 指向：glob=按名 · grep=按内容 · list_dir=浅列 |
| **E · loader** | `format_capability_hints` 只读行加入 `glob_file_search` |
| **Builtin schema** | `description` 写清 vs `grep` / `list_dir` 分工（LLM 首见靠 function 文案） |

**不改**：`core.txt` 不写 glob 通配符教程（细节在 TOOLS §7.3.1 + schema）。

| ID | 内容 | 状态 |
|----|------|------|
| T-4223 | INDEX + loader hints + 可选 `discover.md` | todo（随 T-4222） |
| T-4226 | `core.txt` 边界表 / discipline / ask 模式 | todo（随 T-4222） |

---

## 4. Track J — 模型路由

**真源**：[LLM-ROUTING.md](./LLM-ROUTING.md)（自 AGENT-HARNESS P3 拆出）。

| 摘要 | |
|------|--|
| 主聊 | flash（`execution_model`） |
| plan_partner | pro（`planning_model`） |
| 不改工具 schema | |

任务：**T-4201～T-4203** · **IT-440～441** · **S-440**

---

## 5. Phase 42 总 DOC-04

| 轨 | STABILIZATION 影响面 |
|----|---------------------|
| H | §3.3 Confirm（写路径分层） |
| I | §3.2 协议（可选新 builtin）；§6 Gate 扩 IT-430 |
| J | §3.8 LLM（模型切换续聊） |
| **E/F 提示词** | §3.2 overlay（INDEX/hints/core）；**I 必回归 grep「glob_file_search」** |

**与 Phase 24 关系**：H **不**绕过 Progress Gate；I **不**替代证据门。

---

## 6. 签字清单

| 轨 | 文档 | 实现 |
|----|------|------|
| H | ✅ H1～H8 + 敏感路径表 + M0 新建仍确认 | 待 T-4211 |
| I | ✅ M0 Glob + M2 语义 defer | 待 T-4221 |
| J | ✅ J0～J6 + 路由表 | 待 T-4202 |

用户确认「文档先行」后进入实现；说 **「开始吧」** 按 **H → J → I(M0)** 开工。

---

## 7. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-04 | 初稿：H/I/J 三轨；承接 CURSOR-ALIGN 后收口 |
| 0.1.1 | 2026-08-04 | §1.1 / §2.11 / §3.7：E/F 提示词分层（H 几乎不动 · I 必改 · J 不动） |
