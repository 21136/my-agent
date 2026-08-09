# Terminal 模式（TERMINAL-MODE）

> 版本 **0.2.0** · 2026-08-08 · **状态：M0 done · M1 done · Bottom TUI 已落地**  
> **读者**：产品负责人、实现者  
> 关联：[DESKTOP.md](./DESKTOP.md) · [PROJECT-MODE.md](./PROJECT-MODE.md) · [HOST-SCOPE.md](./HOST-SCOPE.md) · [LOCAL-DELIVERY-MODEL.md](./LOCAL-DELIVERY-MODEL.md) · [CLI-DESKTOP-PARITY.md](./CLI-DESKTOP-PARITY.md) · [AGENT-HARNESS.md](./AGENT-HARNESS.md)

---

## 0. 一句话

**Terminal = 狂野 cwd coding agent**（对齐 **Claude Code 终端**）：你先 `cd` 进仓库，再启动；agent 只认 **当前工作目录**，不认 `project_id`。  
**Desktop = 项目工作台 + LDM 纪律**（`project_id` · 计划 · 采纳）。  
二者 **会话分离、不可切换**；换界面只能 **exit → 在专门入口重启**。

### Claude 式心智模型

```text
cd D:\my-agent\workspace\huiyi    # 任意 git 仓库目录
my-agent terminal                 # 无参数 = 就用这个 cwd

≠  项目 切换 huiyi
≠  meta.project_id
≠  project_sessions
```

---

## 1. 动机

| 现象 | 说明 |
|------|------|
| `start.bat` 继承桌面 project 门 | `项目 确认` · plan_dirty · 采纳队列 · task_paused |
| 用户要 CLI「像 Claude 终端」 | cwd 内直接改代码、跑命令，**不要**繁琐流程 |
| 与 Desktop 混会话 | `meta` 打架（plan 态、壳线、采纳） |

**结论**：Terminal 不是「瘦桌面」，是 **第二条产品线**；与 [ROADMAP-PACK-1245](./ROADMAP-PACK-1245.md) 正交。

---

## 2. 产品冻结（TM 系列 · 签字项）

| ID | 决议 |
|----|------|
| **TM-1** | 新入口 **`my-agent terminal [path?]`**（及 `start-terminal.bat`）；**默认无参 = 进程 `getcwd()`**（Claude 同款）；**不**改变 `start.bat` |
| **TM-2** | 会话创建时写入 **`meta.harness`**：`"terminal"` \| `"desktop"`；**终身不可变** |
| **TM-3** | **禁止中途切换**：无「接管会话」「改用终端保留当前 chat」；换界面 = **exit 结束** → 另一入口 **新建或续接同 harness 会话** |
| **TM-4** | **禁止跨 harness 续接**：`resume` 时 `meta.harness` 与入口不匹配 → **硬拒绝** + 可读文案 |
| **TM-5** | Terminal 档位 **1（纯狂野）**：**cwd 树内**写/跑 **默认免 confirm**；**不**维护 `project_plan_status` / 不 gate 写码 |
| **TM-6** | Terminal **不**注入 TASKS 里程碑压力；可读目录内 `README`/`ENV.md` 作 **软提示**，不 gate |
| **TM-7** | **安全底线保留**：deny-list · `evolve/` 写仍走既有规则；**effective root 外**写仍 confirm |
| **TM-14** | **Host 狂野（1A）**：R2 `host` 会话在 **effective root 子树内**写/跑与 R1 同档 **免 confirm**；越界仍 confirm |
| **TM-15** | **M0 默认续接（2A）**：`terminal` 入口 **续接** `state.terminal_last_session`（同 harness）；`新会话` → 新建 id；exit 时写回 last |
| **TM-16** | **无 segment cap（3A）**：Terminal **关闭** project segment cap / `segment_cap_pause` |
| **TM-17** | **计划域狂野（4A）**：`docs/TASKS.md` · `docs/MAP.md` 等 **允许直写**，无 plan_partner / 采纳 |
| **TM-18** | **桌面列表过滤（5A）**：Desktop `session.list` **不展示** `harness=terminal` 会话 |
| **TM-19** | **锁第三档（6A）**：`interface.lock.ui = "terminal"`；与 `electron` / `cli` **互斥、无 takeover** |
| **TM-20** | **只读 host 可开（7A）**：R2 只读 host 可启动；写工具在 executor **硬拒** + 可读文案 |
| **TM-21** | **路径相对 cwd（8A）**：`terminal [path]` 相对 **当前 shell cwd**；绝对路径原样 |
| **TM-22** | **R3-[2] 全登记（9A）**：选 `[2]` 走现有 host 登记（id / label / write），成功后 R2 进入 |
| **TM-23** | **Q1-A · 固定 agent**：`turn_mode` **恒为 `agent`**；banner **无** `只聊`/`动手`；输入 `只聊`/`动手`/`ask` → 提示「Terminal 仅 agent 模式」；M1 defer `plan` / `/btw` |
| **TM-8** | 同一磁盘目录可被 desktop 会话与 terminal 会话 **同时打开**（不同 `conversation_id`）；**无** `project_sessions` 绑定 |
| **TM-9** | **废止** DESKTOP §3.8「CLI ↔ Electron 接管同一 session」 |
| **TM-10** | **禁止 project_id 模型**：不读写 `meta.project_id` · `project_root` · `active_shell=project` · `project_sessions`；**不提供** `项目 …` 元命令 |
| **TM-11** | **effective root**（见 §4.2）：工具默认 scope = 启动解析后的工作根；`codebase_search` 索引 **effective root**，非 project_id |
| **TM-12** | **TM-Q4 已关闭 · R1～R4**（§4.3）：agent 内直接开干；已登记 host 直接开干；**agent 外须人点 R3 三选一**（**禁止 LLM / 静默扩 scope**） |
| **TM-13** | **M0 含 R3-[1]「仅本次」**：`meta.terminal_foreign_root` 绝对路径；**不写入** `host_scope.json`；会话结束即失效 |

### 非目标

| 非目标 | 说明 |
|--------|------|
| Terminal 走 `project_id` / `项目 切换` | Claude 不认项目 id，只认 cwd |
| Terminal 保证 TASKS/MAP 与代码同步 | Git 为工程真源 |
| 全盘无 confirm | 仅 **effective root 树内** 放宽 |
| **Tier 4 默认全盘** | 不做 `MY_AGENT_TERMINAL_ANY_DIR=1` 默认真 |
| LLM 运行时扩大 scope | 仅 **Terminal 启动时** 人可选 R3 |
| 与 `my-agent tool run` 合并 | `tool run` 仍无会话、无 harness |

---

## 3. 与 Desktop / 旧 CLI 对照

| 维度 | Desktop | Terminal（Claude 式） | `start.bat`（旧 REPL） |
|------|---------|----------------------|------------------------|
| 入口 | `start-desktop.bat` | **先 `cd` 仓库 →** `my-agent terminal` | `start.bat` |
| 工作区 | **`project_id`** → `workspace/<id>` | **`getcwd()`**（可选 `[path]` 覆盖） | agent root |
| harness | `desktop` | `terminal` | 默认 `desktop`（遗留） |
| `项目 …` 命令 | 有 | **无** | 有（遗留） |
| 计划 / 采纳 | 有 | **无** | 有（遗留） |
| 写 confirm | Accept 卡 | effective root 内 **自动**（含 host R2） | 同 desktop |
| 续接 | `project_sessions` | **`terminal_last_session` 默认续接** | 遗留 desktop 续接 |
| 会话列表 | 全 desktop 会话 | Desktop **过滤** terminal 会话 | — |
| Segment cap | 有 | **无** | 有 |
| `只聊`/`动手` | 有 | **无**（固定 `agent` · TM-23） | 有 |
| Stop | 按钮 | **Ctrl+C** | 弱 |

---

## 4. 会话与状态

### 4.1 `meta`（Terminal 会话 · 示例）

**agent 内 cwd（R1）**

```json
{
  "harness": "terminal",
  "terminal_scope_kind": "agent",
  "terminal_cwd": "workspace/huiyi"
}
```

**agent 外 · 仅本次（R3-[1]）**

```json
{
  "harness": "terminal",
  "terminal_scope_kind": "foreign",
  "terminal_foreign_root": "D:/other-clone/huiyi"
}
```

**已登记 host（R2）**

```json
{
  "harness": "terminal",
  "terminal_scope_kind": "host",
  "terminal_host_id": "projects",
  "terminal_cwd": "huiyi"
}
```

| 字段 | 说明 |
|------|------|
| `harness` | `"terminal"` \| `"desktop"`；创建后 **只读** |
| `terminal_scope_kind` | `"agent"` \| `"host"` \| `"foreign"` |
| `terminal_cwd` | `agent` / `host`：相对 effective root 的 posix 路径 |
| `terminal_foreign_root` | 仅 `foreign`：启动时 cwd 绝对路径（规范化） |
| `terminal_host_id` | 仅 `host`：命中之 `host_roots[].id` |

**刻意不存在**：`project_id` · `project_root` · `project_plan_status` · `active_shell=project`。

**缺省**：历史无 `harness` → **`desktop`**。

### 4.2 Effective root（工具真源）

| `terminal_scope_kind` | effective root 解析 |
|----------------------|---------------------|
| `agent` | `agent_root / terminal_cwd`（启动 cwd，须在 agent 内） |
| `host` | `host_roots[id].path / terminal_cwd` |
| `foreign` | `terminal_foreign_root`（绝对路径） |

`read_file` · `write_text` · `run_command` · `codebase_search` 默认限定在 **effective root 子树**（+ 既有 deny）。  
写 **`evolve/`** 仍仅 agent 内且走 evolve 规则（foreign 会话一般不写 evolve）。

### 4.3 TM-Q4 · 启动 scope 决议（R1～R4 · **已签**）

启动 `terminal` 时解析 **候选 cwd**（无参 = `getcwd()`；有参 = 该路径）：

| 规则 | 条件 | 行为 |
|------|------|------|
| **R4** | cwd 命中 deny（`.ssh` · `.env` · `AppData`…） | **硬拒绝**，exit |
| **R1** | cwd ⊆ **agent root** | **直接开干** → `terminal_scope_kind=agent` |
| **R2** | cwd ⊆ 某 **host root** 且 `read:true` | **直接开干** → `terminal_scope_kind=host`（**只读 host 亦可开**；写工具由 executor 按 `write` 拒） |
| **R3** | cwd 在 agent 外且未命中 R2 | **终端启动时人选一**（非 LLM）： |

**R3 提示文案（冻结）**

```text
当前目录不在 my-agent 内，也未登记托管区：
  <绝对路径>

  [1] 仅本次使用此目录（不写入托管区配置）
  [2] 登记为托管区（读写）后进入
  [3] 取消

请选择 1/2/3：
```

| 选择 | 行为 |
|------|------|
| **1** | `terminal_scope_kind=foreign` · `terminal_foreign_root=<cwd>`；**M0 必做** |
| **2** | 调现有 host 登记（`托管目录 添加` 等价逻辑）→ 成功后按 **R2** 进入 |
| **3** | exit 0 |

**禁止**：环境变量默认跳过 R3（`MY_AGENT_TERMINAL_SKIP_GATE` 仅 **M2 defer** power-user）。

### 4.4 `state.json`（M0 · **2A**）

| 键 | harness | M0 行为 |
|----|---------|---------|
| `project_sessions` | desktop | **保持**；项目线续接 |
| `terminal_last_session` | terminal | **M0**：`terminal` 入口 **默认 resume** 此 id；exit / 正常结束时写回；`新会话` 新建并更新 |
| `preferred_ui` | — | 仅记录偏好；**不**用于跨 harness 续接 |

**注意**：续接仅 **harness=terminal** 且 **meta 仍有效**；跨 harness 仍 **TM-4 硬拒**。

### 4.5 `interface.lock`（**6A**）

```json
{ "ui": "terminal", "pid": N, "since": "ISO" }
```

| 场景 | 行为 |
|------|------|
| `terminal` 启动 | 无锁或 stale → `ui=terminal`；活锁 `electron`/`cli`/`terminal` → **硬拒**（无 `--takeover`） |
| `start.bat` / desktop | 活锁 `terminal` → **硬拒** 或提示 exit terminal |
| **废止** | DESKTOP §3.8 CLI ↔ Electron **接管**；terminal 为第三档，**永不 takeover** |

### 4.6 生命周期（冻结）

```text
start-terminal → create/resume (harness=terminal only)
  → 对话 …
  → exit → 释放 interface_lock → 进程结束

start-desktop → create/resume (harness=desktop only)
  → …
  → 托盘退出

禁止：desktop 进程 resume terminal 的 conversation_id（反之亦然）
```

### 4.7 UI 显示 vs 会话上下文（重要）

| 维度 | Terminal UI（transcript） | Agent 会话（磁盘） |
|------|---------------------------|-------------------|
| 存储 | Bottom TUI 内存 / scroll 模式终端回滚 | `data/sessions/<id>/messages.jsonl` |
| 重启后 | **不显示**历史（仅 Welcome + 空 transcript） | **默认续接** `terminal_last_session`，历史仍在 |
| 是否计入 LLM 上下文 | **否** | **是** |

**上下文上限**与界面显示无关，按每轮发给模型的 payload 估算（`context.py`）：

- `system prompt` + `build_llm_messages(session)`（锚定块 + `compact_before_index` 之后的原文 + `digest.md` 摘要）
- 估算：`len(text) // 4`；达模型上限 × **85%**（`CONTEXT_COMPACT_RATIO`）时自动压缩
- Flash 默认 **128k**（≈108.8k 触发）；Pro **1M**（≈850k 触发）；保留最近 **8** 轮完整对话（`CONTEXT_KEEP_TURNS`）
- 手动：`/compact` · `压缩`；换线：`新会话`

详见 [MAP.md](./MAP.md) §9.13 · `context.py`。

---

## 5. Harness 行为（Terminal · 档位 1）

### 5.1 跳过的门（executor / run_turn）

- `project_plan_status` 写码门 · `plan_dirty` 拦截  
- `main_agent_plan_domain_write_block`（TASKS/MAP/PROJECT/ENV **直写允许** · TM-17）  
- `task_paused` / Progress Gate 口语勾 TASKS  
- project segment **task stop** 一停  
- **segment cap / `segment_cap_pause`**（TM-16）  
- `deliverable_review` / `bug_promote` **自动** spawn（用户显式调工具除外）

### 5.2 保留

- Builtin 工具面（含 `codebase_search` · `run_command` · `write_text` · `patch_file`）  
- `run_service` + wait（无 G13「口头拖延」说教，或 terminal 专用短 prompt）  
- deny-list · 路径越界 · `evolve/` 写策略  
- `explore` / `plan_partner`：用户或模型 **可显式调用**，但 **无**采纳队列依赖  

### 5.3 写范围

- **默认** = §4.2 **effective root** 子树内 **免 confirm**（狂野 · **含 R2 host** · TM-14）  
- effective root **外**（含 agent 内其他路径）：write_policy + confirm  
- **host 只读**（`write:false`）：在 root 内 **读**允许；`write_text` / `patch_file` 等 **executor 硬拒**（TM-20）  
- **foreign** / **host** 树内仍受 [HOST-SCOPE](./HOST-SCOPE.md) deny_globs

### 5.4 TM-Q1 · `turn_mode`（**已签 · Q1-A**）

#### Claude Code 实际怎么做

Claude **没有** my-agent 的 `turn_mode=ask|agent` 二元开关：

| Claude 机制 | 作用 | 类比 my-agent |
|-------------|------|----------------|
| **默认会话** | 始终 **agent loop**（可读文件 · 跑命令 · 改代码） | ≈ 永远 `turn_mode=agent` |
| **Permission modes**（`Shift+Tab` 循环） | `default` · `acceptEdits` · **`plan`** · `auto`… | **无**直接等价；`plan` ≈「探查 + 聊，不改源文件」 |
| **`plan` 模式** | 可研究、出方案；**不自动批准文件编辑** | ≠ `只聊`（仍有工具，只是不写盘） |
| **`/btw`** | 旁路问答：**无工具** · 不进主 transcript · 基于已有上下文 | ≠ `只聊`（`只聊` 仍 5 builtin + 短循环） |
| 纯问答句 | 模型常 **不调工具** 直接答，**无需**先切模式 | 无门控 |

#### 签字决议（**Q1-A** → TM-23）

| 项 | M0 行为 |
|----|---------|
| `meta.turn_mode` | 创建与续接时 **强制 `agent`**；持久化只写 `agent` |
| Banner / help | **不列** `只聊` · `动手` |
| 用户输入 `只聊`/`动手`/`ask`/`agent` | 打印一行说明后 **忽略**（不切 mode） |
| 纯问答 | 由模型自行决定是否调工具（与 Claude 默认一致） |

**M1 defer**：`plan` 权限档 · `/btw` 式旁路问答。

## 6. 入口与 UX（M0 / M1）

### M0（Claude 式启动）

```powershell
# 1. 进入你的仓库（和 Claude Code 一样）
cd D:\my-agent\workspace\huiyi

# 2. 启动（无参 = 当前目录）
python D:\my-agent\my-agent terminal
# 或仓库内提供的 start-terminal.bat（不 chdir 走，保留你的 cwd）

# 可选：显式指定目录（覆盖 cwd）
my-agent terminal path/to/other-dir
```

**`start-terminal.bat` 约定**：只设 UTF-8 + 调 `my-agent terminal`；**不要** `cd` 到 agent 根（否则不像 Claude）。

- Banner 一行：`Terminal · <cwd 短路径> · exit 结束`  
- **无** `项目 …` · **无** `只聊`/`动手`（TM-23）；保留 `exit` · `新会话`  
- **新会话** → 新 id，`harness=terminal`，**cwd 不变**（TM-15）  
- **Ctrl+C** → `turn.cancel`  
- **`[path]`** 相对当前 shell cwd（TM-21）

**cwd 解析**：按 §4.3 **R1～R4**；agent 外 **必须** R3 人选一（含 **[1] 仅本次**）。

### M1（T-5710 · **done**）

> **详设**：§6.4（Claude Code 式 Terminal TUI）· §6.5（Bottom 布局实现）  
> **非目标**：Electron 第二聊天窗 · 复制 Anthropic 品牌资产 · 改动 M0 harness 语义

- **Bottom-pinned 全屏 TUI**（`prompt_toolkit`）：欢迎卡 · 可滚动 transcript · 底栏输入 · 状态栏
- Rich / 纯文本 **双后端**；assistant 正文 **Rich Markdown** 渲染（对标 Aider）
- `/model` · `/clear` · `/compact`
- `terminal_cwd` 常驻状态栏（非一行 banner）

### 6.4 T-5710 · Terminal TUI（Claude Code 式界面）

> **版本** 0.2.0 · 2026-08-08 · **状态：实现 done**（S-572 手工 smoke 待做）  
> **对标**：Claude Code 终端（欢迎面板 + 对话区 + 产品化输入/状态栏）  
> **原则**：**同一 agent 事件管线** as 桌面 sidecar；仅 **渲染层** 不同（TUI vs DOM）

#### 6.4.1 目标与非目标

| 目标 | 说明 |
|------|------|
| **G1** | 启动即有 **产品感**（非 `you>` 裸 REPL） |
| **G2** | 回合内 **流式 assistant** + **结构化工具块**（读/写/命令） |
| **G3** | **状态栏** 常驻：模型 · harness · effective_root 短路径 |
| **G4** | **Windows Terminal** 下默认体验；非 TTY **可降级** 到 M0 纯文本 |
| **G5** | 复用 `Agent.on_turn_event` · `StreamHandlers` · `executor.on_event` |

| 非目标 | 说明 |
|--------|------|
| NG1 | 不做 Electron / WebView Terminal |
| NG2 | 不抄 Claude **官方**商标与品牌 mascot（自有「打工仔」像素图 OK） |
| NG3 | M1 **不改** harness / scope / 门控 / `terminal.txt` 语义 |
| NG4 | M1 **不做** `plan` 权限档 · `/btw`（仍 defer TM-Q1 M1） |
| NG5 | M1 **不替换** `start.bat`（desktop harness CLI 保持 M0 文本） |

#### 6.4.2 参考布局（线框 · **默认 bottom**）

默认 `MY_AGENT_TERMINAL_LAYOUT=bottom`：欢迎卡 + transcript + **输入钉在屏幕底部**。  
降级原生回滚：`MY_AGENT_TERMINAL_LAYOUT=scroll`（WT 原生滚轮/选中；无全屏欢迎钉底栏）。

Windows 上 bottom 默认 `MY_AGENT_TERMINAL_MOUSE=0`（避免抢走选中）；可用 PgUp/↑↓/`ScrollUp` 滚 transcript，`Ctrl+O` 复制全文。

```text
┌─ Welcome（琥珀色双栏 + 打工仔 mascot）────────────────────────────┐
│ 你好，忆梦。                    [32 列 truecolor 半块像素 mascot] │
│ 打工仔已就位…                   model · root · scope · tips      │
└──────────────────────────────────────────────────────────────────┘
┌─ Transcript（可滚动）────────────────────────────────────────────┐
│ ❯ 用户问题                                                        │
│ ◆ 打工仔 …                                                        │
└──────────────────────────────────────────────────────────────────┘
────────────────────────────────────────────────────────────────────
> 输入…（Shift+Enter 换行 · Enter 发送）
────────────────────────────────────────────────────────────────────
flash · D:/…/huiyi · agent · ● idle
```

有对话后 Welcome 可 **收成 3 行琥珀条**（省纵向空间）；`/clear` 或 `新会话` 后 **重新挂载完整 Welcome**。

**与 Claude Code / Aider 映射**：

| 参考产品 | my-agent 对应 |
|----------|----------------|
| Claude Code 底栏输入 + 上滚 transcript | `BottomPinnedTerminal`（`terminal_app.py`） |
| Claude Welcome 双栏 | `build_welcome_formatted` + `welcome_mascot` |
| Aider `rich.markdown.Markdown` 助手正文 | `format_terminal_assistant_text` → Rich；失败回退 regex |
| OpenTUI 分层块（标题/代码/引用） | Rich Markdown + plain 回退框线代码块 |
| Claude `Ctrl+C` 停回合 | `ReplTurnCancelGuard` · 状态栏 `cancelled` |

#### 6.4.3 模块落点

| 模块 | 文件 | 职责 |
|------|------|------|
| **Bottom 布局壳** | `agent-core/terminal_app.py` | `BottomPinnedTerminal`：HSplit · transcript · 底栏输入 · 状态栏 · `/model` picker |
| TUI 渲染 | `agent-core/terminal_ui.py` | Welcome · StatusBar · Transcript 格式化 · `TerminalEventSink` |
| Welcome mascot | `agent-core/welcome_mascot.py` · `welcome_mascot_data.py` | 32 列 truecolor 半块像素图；`scripts/gen_welcome_mascot_data.py` 生成 |
| REPL 接入 | `agent-core/cli_terminal.py` | `TerminalRepl` · bottom 循环 · slash 命令 |
| 事件桥 | `terminal_ui.py` | `TerminalEventSink`：`on_turn_event` + `stream_handlers` + `executor.on_event` |
| 降级 | `terminal_ui.py` | `PlainTerminalBackend` vs `RichTerminalBackend`；`MY_AGENT_TERMINAL_LAYOUT=scroll\|plain` → 非 bottom |
| Prompt 工具 | `terminal_prompt.py` · `terminal_picker.py` | `prompt_toolkit` 检测 · `/model` 交互 |
| Scope | `terminal_scope.py` | R1～R4 · effective root |
| 依赖 | `requirements.txt` | **`rich>=13.7`** · **`prompt_toolkit`**（终端 TUI） |
| 测试 | `tests/test_terminal_*.py` · `test_welcome_greeting.py` | IT-577～579 · bottom 布局单测 |

**不新建** sidecar WS；Terminal 进程内直接挂 Agent 回调（同 `server._patch_repl` 思路，无 bridge 队列）。

#### 6.4.4 事件映射（真源 = 桌面已用事件）

| 事件 / 回调 | TUI 行为 |
|-------------|----------|
| `turn.start` | StatusBar → `working`；Transcript 新回合分隔 |
| `turn.notice` | 黄/灰 **Notice** 条（`level=warn` 高亮） |
| `turn.end` | StatusBar → `idle`；`finish_reason=cancelled` 显示 `(stopped)` |
| `assistant.delta` | bottom：**流式纯文本**（`◆` 标题 + raw body）；`MY_AGENT_TERMINAL_MARKDOWN=0` 时同样流式、不替换 |
| `assistant.done` | `MARKDOWN=1` 时用格式化块 **替换** 流式区间；无 delta 时整块写入 `◆ 打工仔` + 正文 |
| `reasoning.delta` | **默认开**（`MY_AGENT_TERMINAL_REASONING=1`）：`╭─ 思考` 框内流式 |
| `tool.start` / `tool.end` | **默认关**（`MY_AGENT_TERMINAL_TOOL_PANELS=0`）；`1` 时 Rich Panel |
| `confirm.request` | 内联 **Confirm** 条（y/n；与 stdin 仍兼容） |
| `confirm.done` | 关闭 confirm 条 |
| Ctrl+C | 已有 `ReplTurnCancelGuard` → Panel 标 `cancelled` |

**executor `on_event`** 与 `server.WsBridge.on_executor_event` 对齐的子集即可（M1 不做 `session.banner` WS 专有事件）。

#### 6.4.5 终端能力与降级

| 条件 | 模式 | 行为 |
|------|------|------|
| TTY + `prompt_toolkit` OK + `LAYOUT` 非 `scroll`/`plain` | **bottom** | **默认**：全屏 Welcome + 底栏输入 |
| `MY_AGENT_TERMINAL_LAYOUT=scroll` | **scroll** | 原生回滚缓冲 + Claude 式输入（无钉底全屏欢迎） |
| `stdout.isatty()` 且 Rich import OK + `MY_AGENT_TERMINAL_UI=rich` | **rich** | 滚动式 Rich 输出（非 bottom 时） |
| `WT_SESSION` 存在（Windows Terminal） | **推荐** | `start-terminal.bat` 自动尝试 `wt` 重开 |
| 否则（cmd 重定向 / CI / `NO_COLOR`） | **plain** | M0 文本：`Terminal · <abs-root> · exit` + `❯` |

环境变量（M1 · **现行默认**）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `MY_AGENT_TERMINAL_UI` | `auto` | `auto` \| `rich` \| `plain`（backend 选择） |
| `MY_AGENT_TERMINAL_LAYOUT` | `bottom` | `bottom`（默认·欢迎+钉底输入）\| `scroll`（原生滚动）\| `plain` |
| `MY_AGENT_TERMINAL_MOUSE` | win32=`0` / 其它=`1` | 仅 bottom：是否 `mouse_support`（Windows 默认关） |
| `MY_AGENT_TERMINAL_INTERACTIVE` | `1` | `0` 禁用 `/model` ↑↓ 菜单（非 TTY 自动文字列表） |
| `MY_AGENT_TERMINAL_PROMPT` | `auto` | `auto` \| `1` \| `0` — `prompt_toolkit` 输入行 |
| `MY_AGENT_TERMINAL_WELCOME` | `1` | `0` 跳过欢迎卡 |
| `MY_AGENT_TERMINAL_REASONING` | `1` | `1` 显示思考块（`╭─ 思考` 框）；`0` 隐藏 |
| `MY_AGENT_TERMINAL_TOOL_PANELS` | `0` | `1` 显示工具调用 Panel；**默认关**（终端更干净） |
| `MY_AGENT_TERMINAL_MARKDOWN` | `1` | `1` Rich Markdown 格式化 assistant；`0` 流式纯文本 |
| `MY_AGENT_TERMINAL_TURN_SEP` | `0` | `1` 回合间 `─` 分隔线 |
| `MY_AGENT_TERMINAL_USER_NAME` | `忆梦` | Welcome 问候用户名 |
| `MY_AGENT_TERMINAL_ASSISTANT_NAME` | `打工仔` | Welcome / 正文 `◆` 角色名 |

**cwd 显示规则（修 M0 `. . .` 问题）**：Welcome + StatusBar **始终 prefer 绝对 `effective_root`**；仅当列宽不足时缩略中间段（`D:/…/huiyi`），**禁止**单独显示 `.`。

#### 6.4.6 实现分期（一动一停）

| ID | 范围 | 交付 | 验收 |
|----|------|------|------|
| **T-5710a** | Rich 基础 + Transcript | `terminal_ui.py` · Tool Panel · `assistant.delta` | IT-577 |
| **T-5710b** | Welcome + StatusBar + 绝对路径 | 启动屏 · 状态随回合更新 | IT-578 · S-572 |
| **T-5710c** | `/clear` `/compact` · reasoning 开关 · plain 降级 | cli 斜杠命令 · env | IT-579 |

**实施顺序**：`5710a → 5710b → 5710c`（每步 pytest + 更新 §9 状态）。

#### 6.4.7 DOC-04 增补（M1）

| 面 | 档位 | ID |
|----|------|-----|
| Rich 模式 tool.start/end 渲染 | P1 | IT-577 |
| Welcome + StatusBar 字段 | P1 | IT-578 |
| plain 降级与绝对 root 显示 | P2 | IT-579 |
| 手工：WT 下全屏 TUI smoke | P1 | **S-572** |

**IT-577**：mock `tool.start`/`tool.end` → Panel 含工具名且 `ok=True` 显示 ✓  
**IT-578**：`build_welcome()` 含 `effective_root` 绝对路径 · `llm_model` · `terminal_scope_kind`  
**IT-579**：`MY_AGENT_TERMINAL_UI=plain` → 无 Rich import 亦可启动；输出含绝对 root  

**S-572**：Windows Terminal · `cd workspace/huiyi` · `start-terminal.bat` → 见 Welcome + 一轮对话流式 + 状态栏 idle

#### 6.4.8 开放项（M1 内可签）

| ID | 问题 | 建议默认 |
|----|------|----------|
| TM-Q15 | Welcome 右侧「What's new」读什么？ | `CHANGELOG.md` 最近 **1 个**版本标题 + 最多 5 bullet；无则显示 Terminal 快捷键 |
| TM-Q16 | Confirm 在 TUI 用键盘还是仍 stdin？ | M1：**仍 stdin** `y/n`（与 M0 一致）；M2 再考虑 Rich Prompt |
| TM-Q2（M1） | `plan_partner` 工具列表 | M1 **保留**；Welcome tips 不写 plan；M2 可 hide |

### 6.5 Bottom TUI 实现说明（T-5710d · 2026-08-08）

> **读者**：日常用户 + 维护者  
> **代码真源**：`terminal_app.py` · `terminal_ui.py` · `cli_terminal.py`

#### 6.5.1 启动与推荐环境

```powershell
cd D:\my-agent\workspace\huiyi    # 你的仓库
.\start-terminal.bat               # 或: python ..\my-agent terminal
```

- `start-terminal.bat`：**不** `cd` 到 agent 根；保留当前 shell cwd（Claude 同款）
- 检测到未在 Windows Terminal 内时，会尝试 `wt -d "%CD%"` 重开（更好的 Unicode / 鼠标）
- 推荐 **Windows Terminal** + 字体支持中文与框线字符

#### 6.5.2 交互速查

| 操作 | 行为 |
|------|------|
| **Enter** | 发送（底栏 `>` 输入框） |
| **Shift+Enter** / **Esc Enter** | 多行输入 |
| **Ctrl+C** | 停止当前回合 / 退出 picker |
| **点击 transcript 拖选** | 选中历史正文 |
| **Ctrl+C**（有选区时） | 复制选中内容到系统剪贴板 |
| **Ctrl+Insert** / **Ctrl+Shift+Insert** | 复制选中内容到系统剪贴板 |
| **Esc**（transcript 聚焦时） | 取消选中并回到输入框 |
| **直接打字**（transcript 聚焦时） | 自动回到输入框并插入字符 |
| **滚轮 / ↑↓ / PgUp·PgDn**（transcript；输入框时也可用 PgUp/PgDn） | 翻阅历史（**输出中也可上滑**）；新输出默认 **跟到底**；手动上滑暂停跟尾，滑回底部或发送下一条消息后恢复 |
| **Ctrl+C**（无选区 · 回合中） | 取消当前回合（不退出 Terminal） |
| **`/clear`** | 清空 transcript + **重新挂载 Welcome** |
| **`新会话`** | 新 session id；cwd 不变；重新挂载 Welcome |
| **`/model`** | 底栏 ↑↓ picker（不 dismiss Welcome） |

#### 6.5.3 输出格式

| 元素 | 格式 | 配置 |
|------|------|------|
| 用户 | `❯ 问题` | — |
| 助手 | `◆ 打工仔` + Rich Markdown 正文（标题/列表/引用/代码块） | `MY_AGENT_TERMINAL_MARKDOWN`（默认 `1`） |
| 思考 | `╭─ 思考` 框 + 流式正文 | `MY_AGENT_TERMINAL_REASONING`（默认 `1`） |
| 工具 | Rich Panel（⎿ tool ✓） | `MY_AGENT_TERMINAL_TOOL_PANELS`（默认 `0`） |
| Welcome | 琥珀色双栏 + truecolor mascot | `MY_AGENT_TERMINAL_WELCOME`；`MY_AGENT_TERMINAL_USER_NAME` / `ASSISTANT_NAME` |

**格式化管线**（assistant）：

1. 流式 `assistant.delta`：**先写** `◆` 标题 + raw markdown 正文（可见边出边长）
2. `assistant.done` 且 `MY_AGENT_TERMINAL_MARKDOWN=1`：flush 后 **替换** 流式区间为 `format_terminal_assistant_block` → `format_terminal_assistant_text`
3. 优先 **Rich `Markdown`**（对标 [Aider](https://github.com/Aider-AI/aider)）；异常时回退 regex + Unicode 代码框
4. transcript 为 `prompt_toolkit` 纯文本 `TextArea`（**无 ANSI 颜色**）；框线与层级靠 Unicode
5. 大块 finalize 替换时：用户已上滑（`follow=False`）**不抢滚动**；跟尾时替换后贴底

**Terminal 专属 agent 行为**：跳过桌面 G14 服务后置条件提醒（`agent.py` terminal harness 分支）。

#### 6.5.4 焦点与复制（实现要点）

| 问题 | 对策 |
|------|------|
| 点击 transcript 后无法输入 | transcript **可聚焦**以支持拖选；`always_hide_cursor=to_filter(True)` 隐藏插入符 |
| 选中后如何复制 | 有选区时 **Ctrl+C** / `c-insert` / `c-s-insert` → `_copy_to_system_clipboard`（Windows `clip`） |
| Enter 误提交 transcript | `c-m` 绑定加 `input_focused` 过滤，仅底栏输入可提交 |
| 新输出把滚动位置顶乱 | `_transcript_follow_tail` + `_invalidate_preserve_scroll` |
| 答案半截 / 须再提问才刷完 | worker 经 **`loop.call_soon_threadsafe`** 改 Buffer；回合结束 **`end_turn_output`** flush 残留流式块，**仍在跟尾时**才贴底 |
| 方向键/滚轮滑到一半卡住 | `wrap_lines` 下 Window **跟光标**；滚动改为移动 cursor 行；**流式中也可上滑**（上滑暂停跟尾，滑回底部自动恢复） |
| 流式输出卡顿 | token 合批：`_schedule_raw_flush` 延迟 50ms 合并突发 chunk，一次 Document 重建；`refresh_interval=0.1` 保持及时重绘；finalize 替换时 pinned scroll 由 `_restore_browse_scroll` 保持 |
| Windows 无法选中/复制 | bottom 默认关 `mouse_support`；`Ctrl+O` 复制全文；或 `LAYOUT=scroll` 用 WT 原生选区 |

#### 6.5.5 布局降级

| `MY_AGENT_TERMINAL_LAYOUT` | 行为 |
|--------------------------|------|
| `bottom`（**默认**） | Welcome + 钉底输入全屏 TUI |
| `scroll` | 原生回滚缓冲 + Claude 式输入（可滚轮/拖选/Ctrl+C） |
| `plain` / `0` / `off` | 退回最简 plain REPL |

#### 6.5.6 Mascot 资源

- 像素图嵌入 `welcome_mascot_data.py`（32×15 · 半块字符 · truecolor ANSI）
- 重新生成：`python agent-core/scripts/gen_welcome_mascot_data.py <参考图路径>`
- **不要**把用户参考图 commit 进仓库；仅提交生成后的 data 模块

#### 6.5.7 开放 / defer

| 项 | 说明 |
|----|------|
| transcript 内 ANSI 彩色 Markdown | defer；需 FormattedText transcript 或 Rich Live 区 |
| 流式格式化（边生成边渲染 MD） | defer；当前 done 时整块渲染 |
| S-572 手工 smoke | Welcome · 对话 · 复制 · `/model` · Ctrl+C |

---

## 7. 实现落点

| 模块 | 文件 | 状态 |
|------|------|------|
| 设计 | 本文 | **v0.2.0** |
| meta | `session.py` | `harness` · scope 字段 |
| scope 解析 | `terminal_scope.py` | R1～R4 · R3 提示 |
| 入口 | `main.py` · `cli_terminal.py` | `terminal` 子命令 |
| 启动脚本 | `start-terminal.bat` | WT 自动重开 |
| 续接守卫 | `session.py` · `interface_lock.py` | harness 校验；`ui=terminal` |
| 门控 | `tools/executor.py` · `agent.py` | terminal 分支 · 跳过 G14 提醒 |
| prompt | `prompts/terminal.txt` · `loader.py` | 短 system |
| **Bottom TUI** | `terminal_app.py` | `BottomPinnedTerminal` |
| **TUI 渲染** | `terminal_ui.py` | Welcome · 格式化 · EventSink |
| **Mascot** | `welcome_mascot*.py` · `scripts/gen_welcome_mascot_data.py` | Welcome 右侧像素图 |
| Prompt 工具 | `terminal_prompt.py` · `terminal_picker.py` | 输入行 · `/model` |
| 桌面 | `DESKTOP.md` · `electron/main.ts` | resume 拒 terminal 会话 |
| 测试 | `tests/test_terminal_*.py` · `test_welcome_greeting.py` | IT-570～579 |

---

## 8. DOC-04

| 面 | 档位 | ID |
|----|------|-----|
| harness 创建不可变 | P0 | IT-570 |
| 跨 harness resume 拒绝 | P0 | IT-571 |
| terminal effective root 写免 confirm | P1 | IT-572 |
| 无 plan gate 写码 | P1 | IT-573 |
| Ctrl+C cancel | P1 | IT-574 |
| R3 外目录 · [1] foreign root | P1 | IT-575 |
| R3 deny 硬拒 | P0 | IT-576 |
| 手工 smoke | P1 | **S-570** · **S-571** |
| TUI 工具块 | P1 | IT-577 |
| Welcome / StatusBar | P1 | IT-578 |
| plain 降级 | P2 | IT-579 |
| TUI 手工 smoke | P1 | **S-572** |

### IT-570

- 创建 terminal 会话 → `meta.harness=="terminal"`；尝试 API 改为 desktop → 拒绝

### IT-571

- terminal 会话 id → `start-desktop` resume 路径拒绝；desktop 会话 → `terminal` 入口拒绝

### IT-572 / IT-573

- `cd workspace/huiyi` + `terminal` + `write_text` 改 `backend/...` → 无 confirm；**无** `项目 确认`；**meta 无 project_id**

### IT-574

- 模拟长 turn + cancel → `turn.cancel` 或等价中止

### IT-575

- cwd 在 agent 外 → 模拟输入 `1` → `terminal_foreign_root` 落盘 · 写文件无 host 登记

### IT-576

- cwd 在 `.ssh` 或 deny 路径 → 启动拒绝

### S-570

- `cd workspace/huiyi`（R1）→ `terminal` → 改代码 + 跑测试

### S-571

- `cd` 到 agent 外另一 git  clone → R3 选 **1** → 改代码；**不**改 `host_scope.json`

### IT-577 / IT-578 / IT-579

见 §6.4.7

### S-572

- Windows Terminal · `cd workspace/huiyi` · `start-terminal.bat` → Welcome 双栏 · 一轮流式 · 状态栏 idle

---

## 9. 任务表

| ID | 内容 | 状态 |
|----|------|------|
| T-5700 | 本文 + TASKS Phase 57 | **doc** |
| T-5701 | `SessionMeta.harness` + scope 字段 + 创建/保存 | **done** |
| T-5702 | `my-agent terminal` + `start-terminal.bat` + `terminal_last_session` | **done** |
| T-5708 | `terminal_scope.py` · R1～R4 · R3 提示 | **done** |
| T-5703 | executor/agent terminal 门控 + effective root | **done** |
| T-5704 | 废止跨 harness 接管；resume 硬拒 | **done** |
| T-5705 | `prompts/terminal.txt` + loader 分支 | **done** |
| T-5706 | Terminal REPL Ctrl+C → `turn.cancel` | **done** |
| T-5707 | DESKTOP.md / CLI-DESKTOP-PARITY 绕行更新 | **done** |
| S-570 | 手工：R1 agent 内狂野链 | todo |
| S-571 | 手工：R3-[1] agent 外仅本次 | todo |
| T-5710 | M1：Terminal TUI（§6.4 总览） | **done** |
| T-5710a | Rich Transcript + 工具块 | **done** |
| T-5710b | Welcome + StatusBar | **done** |
| T-5710c | `/clear` `/compact` + plain 降级 | **done** |
| T-5710d | Bottom 布局 · Welcome mascot · Markdown 格式化 · 复制/焦点 | **done** |
| S-572 | 手工：WT 全屏 TUI smoke | todo |

---

## 10. 开放问题

| # | 问题 | 决议 |
|---|------|------|
| ~~TM-Q1~~ | terminal `turn_mode` / `只聊` | **Q1-A** → TM-23 |
| TM-Q2 | `plan_partner` 在 terminal 是否从工具列表隐藏？ | M0 **保留可调用**；**M1 仍保留**（§6.4.8）；M2 可 hide |
| TM-Q3 | 旧 `start.bat` 何时 deprecated？ | M0 不动 |
| ~~TM-Q4~~ | agent 外 cwd | **已关闭** → TM-12/13 · §4.3 |
| TM-Q5 | `MY_AGENT_TERMINAL_SKIP_GATE` 跳过 R3？ | **M2 defer** |
| ~~TM-Q6~~ | Host 写 confirm | **1A** → TM-14 |
| ~~TM-Q7~~ | M0 续接 | **2A** → TM-15 |
| ~~TM-Q8~~ | Segment cap | **3A** → TM-16 |
| ~~TM-Q9~~ | TASKS/MAP 直写 | **4A** → TM-17 |
| ~~TM-Q10~~ | 桌面会话列表 | **5A** → TM-18 |
| ~~TM-Q11~~ | interface_lock | **6A** → TM-19 |
| ~~TM-Q12~~ | 只读 host 启动 | **7A** → TM-20 |
| ~~TM-Q13~~ | `[path]` 基准 | **8A** → TM-21 |
| ~~TM-Q14~~ | R3-[2] | **9A** → TM-22 |
| TM-Q15 | Welcome 右侧内容源 | **§6.4.8** → CHANGELOG 摘要 + 快捷键 |
| TM-Q16 | TUI confirm 交互 | **§6.4.8** → M1 仍 stdin `y/n` |

---

## 11. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-07 | 初版：狂野档位 1 · harness 分离 · 禁止切换/接管 |
| 0.1.1 | 2026-08-07 | Claude 对齐：`getcwd()` · TM-10 禁止 project_id |
| 0.1.2 | 2026-08-07 | **TM-Q4 关闭**：R1～R4 · R3 三选一 · **M0 含 [1] 仅本次** |
| 0.1.3 | 2026-08-07 | **封板 1A～9A**（TM-14～22）；§5.4 Claude vs `turn_mode`；**TM-Q1 待签** |
| 0.1.4 | 2026-08-07 | **TM-Q7 改 2A**：M0 `terminal_last_session` 默认续接 |
| 0.1.5 | 2026-08-07 | **TM-Q1 关闭 · Q1-A**：固定 `agent` · 无 `只聊`/`动手` |
| 0.1.6 | 2026-08-07 | **T-5710 设计签**：§6.4 Terminal TUI（Claude Code 式）· IT-577～579 · S-572 |
| 0.2.0 | 2026-08-08 | **T-5710d 落地**：§6.5 Bottom TUI · Rich Markdown 输出 · Welcome mascot · 复制/焦点 · 环境变量表更新 |

---

## 12. 实现开工（新会话粘贴）

**纪律**：一动一停 · 每轮 **一个** task · 跑测试 · 更新 `TASKS.md` / 本文 §9 · **不要** git commit（除非用户明确要求）

**建议顺序**：T-5701 → … → T-5707 → S-570/571 → **T-5710a → 5710b → 5710c** → S-572

### 粘贴给 Cursor 的开场（复制下方整段）

```text
Phase 57 Terminal 实现。设计真源：docs/TERMINAL-MODE.md v0.1.5（已封板，勿改产品决议）。

纪律：
- 一动一停：本轮只做 T-5701
- 先读 TERMINAL-MODE.md §2 TM 系列 + §4 meta + §7 落点
- 实现 SessionMeta.harness + terminal_scope_kind / terminal_cwd / terminal_foreign_root / terminal_host_id
- 创建/保存/加载；历史无 harness → desktop；terminal 强制 turn_mode=agent（TM-23）
- 补 tests/test_terminal_harness.py 覆盖 IT-570
- 跑 pytest 相关用例
- 更新 docs/TASKS.md T-5701 → done、TERMINAL-MODE §9
- 不要 git commit
- 做完停，等我「继续」再开 T-5708

不要顺手做 T-5702/5703/入口/executor。
```

后续轮次把 `T-5701` 换成下一项即可（见 §9 任务表）。
