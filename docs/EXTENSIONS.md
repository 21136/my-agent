# 用户扩展层设计（EXTENSIONS）

> 版本 0.1.0 · 2026-07-10 · 与 `MEMORY.md`、`TOOLS.md`、`EVOLVE.md` 配套  
> **状态**：Phase 8 done（T-801～T-803、T-805）

---

## 1. 动机

### 1.1 现状痛点

当前 `evolve/` 名义上是「进化层」，但与仓库种子内容**共用同一索引文件** `evolve/_index.toml`：

| 操作 | 现状 | 问题 |
|------|------|------|
| 新增主题（如 `data`） | 用户手改 `_index.toml` | 麻烦、易写错 TOML / 路径 |
| 新增主题工具 | `write_evolve` 校验 scope 白名单 | 未注册主题则写不进去 |
| `git pull` 更新内核 | 可能与本地索引改动冲突 | 种子与用户扩展混在一起 |

设计文档（`MEMORY.md` §3.2）要求 **「LLM 不得自动新增 topic」**——本意是 **未经用户同意不能开新领域**，不是 **必须用户亲手编辑 TOML 文件**。

### 1.2 目标

在 **不削弱治理**（仍须用户确认新领域）的前提下：

1. **隔离**：仓库种子 vs 用户后来加的内容，文件级可区分
2. **降摩擦**：`注册主题 data` 一条 REPL 命令代替手改 TOML
3. **可审阅**：`git diff` 一眼看出用户扩展改了什么
4. **向后兼容**：旧单文件 `_index.toml` 在迁移完成前仍可工作

### 1.3 非目标（本 Phase）

- 独立外挂目录（`MY_AGENT_EXTENSIONS`）— 见 §8 远期
- LLM 静默注册主题
- 自动 `git commit`
- 进程沙箱

---

## 2. 两层模型

```text
┌─────────────────────────────────────────────────────────┐
│  agent-core/          内核源码（稳定、少变）              │
├─────────────────────────────────────────────────────────┤
│  evolve/_index.core.toml   种子主题索引（随仓库发布）     │
│  evolve/prompts/{coding,workflow,…}.md                   │
│  evolve/tools/{common,coding,workflow}/                  │
├─────────────────────────────────────────────────────────┤
│  evolve/_index.user.toml   用户扩展主题索引（可为空）     │
│  evolve/prompts/data.md    用户扩展 prompt               │
│  evolve/memories/data/     用户扩展记忆                   │
│  evolve/tools/data/        用户扩展工具                     │
└─────────────────────────────────────────────────────────┘
```

| 层 | 谁维护 | Git 策略 | 典型内容 |
|----|--------|----------|----------|
| **core** | 仓库 / 上游 | `git pull` 可覆盖 | coding、workflow、safety、种子 common 工具 |
| **user** | 用户（经 REPL 或手改） | 用户 diff + commit | `data` 主题、`csv_head` 等 |

**合并规则**：启动时 `load_topic_index()` 读取 **core + user**，按 `id` 合并为单一主题列表注入 S0。

---

## 3. 索引文件

### 3.1 `_index.core.toml`（种子）

由原 `evolve/_index.toml` **改名**而来，内容不变。文件头注释标明只读意图：

```toml
# 种子主题索引 — 随仓库发布；勿在此添加个人主题。
# 个人主题请写入 _index.user.toml 或使用 REPL「注册主题 …」。
# 详见 docs/EXTENSIONS.md

[[topic]]
id = "coding"
name = "开发与代码"
description = "Python、agent-core、Git、工具实现、文档结构"
prompt = "prompts/coding.md"
memory_dirs = ["memories/coding"]
tool_dirs = ["tools/coding"]
llm_model = "deepseek-v4-pro"

# … workflow / writing / safety 同现 _index.toml
```

### 3.2 `_index.user.toml`（用户扩展）

初始可为空或仅含注释。用户扩展主题 **只写在此文件**：

```toml
# 用户扩展主题 — 由你策展；git diff 此处即你的扩展变更。
# 新增主题：REPL「注册主题 <id>」或手改本文件。
# 详见 docs/EXTENSIONS.md

[[topic]]
id = "data"
name = "数据处理"
description = "CSV、JSON、日志等数据文件的读写与分析"
prompt = "prompts/data.md"
memory_dirs = ["memories/data"]
tool_dirs = ["tools/data"]
```

### 3.3 合并语义（已决）

| 规则 | 说明 |
|------|------|
| 加载顺序 | core 先，user 后 |
| `id` 冲突 | **拒绝启动**，报错列出冲突 id；user **不得**覆盖 core |
| 缺失文件 | `_index.core.toml` 缺失 → 回退读 `_index.toml`（兼容）；`_index.user.toml` 缺失 → 视为空 |
| `tools/common/` | 规则不变：不在索引声明，每 session 注入 |
| S0 展示 | 合并后的列表 **不区分** core/user（对用户透明）；可选在行尾标注 `(user)` — **MVP 不做** |

### 3.4 迁移（T-801）

```text
1. evolve/_index.toml  →  evolve/_index.core.toml（内容平移 + 文件头注释）
2. 新建 evolve/_index.user.toml（空壳 + 注释）
3. loader / write_evolve / router 改读合并索引
4. 保留：若仅存在 _index.toml 且无 core/user 文件 → 行为与现网一致（deprecated 日志一行）
```

---

## 4. REPL：`注册主题` 命令

### 4.1 与现有命令的区别

| 命令 | 作用 | 改什么 |
|------|------|--------|
| `主题 data` / `换主题` | **本会话**选用哪些已注册主题 | `meta.json` → `topics[]` |
| `加主题 workflow` | 本会话 **并集** 追加已注册主题 | `meta.json` → `topics[]` |
| **`注册主题 data`**（新） | **永久**向索引注册新主题 | `_index.user.toml` + 脚手架 |

「注册」是一次性策展；「主题 / 加主题」是每次会话的路由。

### 4.2 `注册主题 <id>` 流程

```text
用户: 注册主题 data
  → 校验 id：小写 [a-z][a-z0-9_]*；不得与 core 冲突；不得已存在于 user
  → 若缺 name/description：REPL 追问或接受可选参数「注册主题 data 数据处理 CSV…」
  → 预览将写入的 _index.user.toml 片段 + 将创建的 paths
  → 用户 y/n
  → y：追加 [[topic]]；创建 prompts/data.md（模板）；mkdir memories/data/；mkdir tools/data/
  → 提示：「已注册。本会话可用 主题 data 或 加主题 data 加载。」
```

**禁止**：LLM 在对话中直接调用写 `_index.user.toml`；须经用户 REPL 确认或 `patch_file` / 未来专用 builtin（MVP 用 REPL + 内核写文件）。

### 4.3 `prompts/<id>.md` 脚手架模板

```markdown
# <name>

> 用户扩展主题 · 注册于 <ISO8601>
> 在此写下本主题的硬规则（路径、确认策略、常用模式）。

## 范围

（待填写）

## 硬规则

（待填写）
```

---

## 5. 工具与 `write_evolve`

### 5.1 scope 白名单

`write_evolve` 的 `_allowed_scopes()` 改为：

```text
scopes = {"common"} ∪ tool_dirs(merge(core_index, user_index))
```

注册 `data` 后，`evolve/tools/data/` 自动进入白名单。

### 5.2 工具目录约定

用户扩展工具 **仍放在** `evolve/tools/<topic>/`，与种子工具同目录树；**索引来源**区分 core/user，目录本身不分子树。

可选远期：`evolve/tools/_user/data/` — **本 Phase 不做**，避免双套扫描逻辑。

### 5.3 典型闭环（以 `csv_head` 为例）

```text
注册主题 data          → _index.user.toml + 脚手架
主题 data              → 会话加载 data prompt + tools/data/* 清单
write_evolve           → csv_head/tool.toml + main.py
status: active         → 下一会话或 换主题 后可见
git diff               → 用户审阅 _index.user.toml + tools/data/
git commit             → 用户手动
```

---

## 6. 记忆与 proposal

| 类型 | 路径 | 规则 |
|------|------|------|
| 用户扩展记忆 | `evolve/memories/data/*.md` | 与现网相同；`topics: [data]` |
| proposal 接受 | `EVOLVE.md` 路由 | 目标 topic 须在 **合并后** 索引中存在 |
| 新主题 + 记忆 | 须先 `注册主题` | proposal 不得自动创建 topic |

---

## 7. 安全与治理（不变）

| 原则 | 本设计如何满足 |
|------|----------------|
| 新领域须用户同意 | `注册主题` 须 REPL `y` |
| LLM 不静默扩权 | 无自动写 `_index.user.toml` 的 evolved 工具 |
| `write_evolve` 每次 confirm | 不变 |
| core 不被用户索引覆盖 | `id` 冲突 → 启动失败 |
| Git 真源 | user 层变更仍须用户 commit |

---

## 8. 远期：独立扩展根（方案 B）

若同仓仍不够隔离，可增加环境变量：

```text
MY_AGENT_EXTENSIONS=D:\my-extensions
```

扫描顺序：`evolve/`（core 种子）+ `MY_AGENT_EXTENSIONS/`（整棵 user 树）。  
**依赖 T-801 合并逻辑**；列为 T-804 可选。

---

## 9. 实现任务（→ TASKS.md Phase 8）

| ID | 任务 | 交付物 |
|----|------|--------|
| T-801 | 双索引文件 + 合并加载 | `_index.core.toml`、`_index.user.toml`、`loader.load_topic_index` |
| T-802 | `write_evolve` scope 读合并索引 | `evolve/tools/common/write_evolve/main.py` |
| T-803 | REPL `注册主题 <id>` | `router.py` / `main.py`；写 user 索引 + 脚手架 |
| T-804 | **可选** `MY_AGENT_EXTENSIONS` | `paths.py` + loader |

**Phase 8 完成标志**：用户执行 `注册主题 data` → `y` → `write_evolve` 可写 `evolve/tools/data/csv_head/`；`git diff` 仅 user 相关文件；旧仓仅 `_index.toml` 仍启动。

---

## 10. 验收清单

- [x] `_index.core.toml` 含原 4 主题；`_index.user.toml` 可空
- [x] `load_topic_index` 合并正确；core/user `id` 冲突启动报错
- [x] 仅 `_index.toml` 存在时行为与迁移前一致
- [x] `注册主题 data` 创建索引条目 + `prompts/data.md` + `memories/data/` + `tools/data/`
- [x] `write_evolve` 拒绝未注册 scope；接受已注册 `data`
- [x] `主题 data` 会话可见 `tools/data/` 下 active 工具
- [x] `MEMORY.md` / `MAP.md` / `TOOLS.md` 交叉引用本文件
- [x] **示例**：`csv_head`（T-805）

---

## 11. 文档交叉引用

| 文档 | 修订点 |
|------|--------|
| [MEMORY.md](./MEMORY.md) §3.2 | 索引改为 core + user 合并 |
| [TOOLS.md](./TOOLS.md) §4.2、§8.1 | `write_evolve` scope 来源 |
| [RUNTIME.md](./RUNTIME.md) §2 | 新增 `注册主题` 命令表 |
| [MAP.md](./MAP.md) §2 | Phase 8 进度 |
| [TASKS.md](./TASKS.md) | T-801～T-804 任务表 |

---

## 12. 与「源码 / 扩展」心智模型

```text
你 clone 的 my-agent 仓库  ≈  发行版（agent-core + evolve 种子）
你 git diff 里的 user 部分  ≈  你安装的插件 / 个人配置
注册主题 + write_evolve     ≈  插件商店里点「安装」，但仍要你确认
```

这样 **不用手改 TOML**，也 **不会** 让 LLM 在你不知情时开新领域。
