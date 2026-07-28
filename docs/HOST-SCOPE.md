# 主机托管区设计（HOST-SCOPE）

> 版本 **0.2.9** · 2026-07-11  
> **状态**：**Phase 10 done**（T-1001～T-1008；手工验收见 [TASKS.md](./TASKS.md) §Phase 10）  
> 关联：[TOOLS.md](./TOOLS.md) §2 / §8.1 / §10 · [PROJECT.md](./PROJECT.md) §6.4 · [paths.py](../agent-core/paths.py) · [EXTENSIONS.md](./EXTENSIONS.md) · [DESKTOP.md](./DESKTOP.md)

---

## 0. 已决摘要（2026-07-11）

| ID | 决议 |
|----|------|
| **S1** | 路径参数统一 **`host:<id>/relative`**；confirm / 错误 UI 显示 **绝对路径** |
| **S2** | host 工具内 **不接受** 裸相对路径或绝对路径字符串（须带 `host:` 前缀） |
| **S3** | **拒绝** 将 agent root 或其子路径登记为 host root |
| **S4** | host **只读**工具：**不** confirm；host **写**：每次 confirm，**无** `a` |
| **S5** | **硬拒绝** 敏感路径：整个 `AppData/**`、`.ssh`、`.gnupg`、`.env`、`*credentials*` 等（见 §5.2） |
| **S6** | 工具面：**evolved 为主**；仅当 LLM 选 tool 不稳时再增少量 host builtin |
| **S7** | 配置存 **`data/host_scope.json`**（gitignore），与 `state.json` 分离 |
| **S8** | 首次启动：**wizard** 询问是否添加 **下载 / 桌面**（可选 **只读 / 读写**）；不静默默认全盘 |
| **S9** | 符号链接：`resolve()` 后路径仍须在 host root 内（与现 `paths.py` 一致） |
| **S10** | **允许**跨 host root 的 `copy_move`（P10b）：源 root 须 `read`，目标 root 须 `write`；**每次 confirm**，无 `a`（见 §5.4） |
| **S11** | 配置变更：添加、开启 **write**、删除、**更换路径** → **须 confirm**；wizard 只读批量可一次确认；wizard 读写再确认（见 §7.2） |

---

## 1. 动机

### 1.1 用户侧诉求

当前 Agent 的**有效动手范围**大致是：

| 区域 | 读 | 写 |
|------|----|----|
| `my-agent/`（agent root） | Builtin：`read_file` / `list_dir` / `grep` | 仅 `workspace/` + 有限 `patch_file`（仓内已有文件） |
| 用户电脑其他位置（Downloads、别的项目、Documents…） | **不可** | **不可** |

这与「本地电脑全能管家」的长期愿景不符：整理下载夹、管笔记、批量重命名、跨项目读代码等，都不应困在 `workspace/`。

### 1.2 为何不直接「全盘放开」

| 风险 | 说明 |
|------|------|
| **进化层污染** | `evolve/`、`agent-core/` 需保持 Git 真源与可审计；与「整理 C 盘」混权限会破坏分层 |
| **误操作放大** | LLM 路径幻觉、批量删除、覆盖配置文件——范围越大后果越不可逆 |
| **无技术沙箱** | [PROJECT.md](./PROJECT.md) §6.4 已诚实声明：安全靠 **路径约定 + confirm + dry_run**，不是进程隔离 |
| **敏感数据** | `.ssh`、浏览器配置、`.env`、凭证文件一旦可读，可能进 LLM context 或日志 |

**结论**：扩范围是**方向正确**，但应加 **host scope（主机托管区）** 分层，而不是拆掉 agent root 边界。

### 1.3 与现有设计的关系

```text
┌─────────────────────────────────────────────────────────────┐
│  Tier 0  agent root     evolve/ · docs/ · agent-core/         │  ← 保持现状；写 evolve 仍走 write_evolve
├─────────────────────────────────────────────────────────────┤
│  Tier 1  workspace      my-agent/workspace/                 │  ← 现状；会话可对 workspace_only 工具 `a`
├─────────────────────────────────────────────────────────────┤
│  Tier 2  host roots     用户配置的托管目录（Downloads 等）   │  ← **本 Phase 新增**
├─────────────────────────────────────────────────────────────┤
│  Tier 3  user profile   ~/ 整棵（可选，高风险）              │  ← 远期 / 显式 opt-in
├─────────────────────────────────────────────────────────────┤
│  Tier 4  full machine   任意路径（原则上不做默认）           │  ← 非目标
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 目标与非目标

### 2.1 目标

1. **分区授权**：用户显式登记若干 **host root**；工具仅能在「agent root ∪ host roots − denylist」内解析路径。
2. **先读后写**：第一阶段以托管区内**只读浏览**为主；写操作后续开放且**更严 confirm**。
3. **管线分离**：主机文件操作与 `write_evolve` / `patch_file`（改仓）走不同工具与策略，不混用 `write_text` 往 `C:\` 写。
4. **可审计**：`evolve_log` 记录 host 操作的绝对路径（或规范化的 host-relative 路径）。
5. **桌面可配置**：grow 设置里管理托管目录（增删需 confirm）；CLI 提供等价命令。

### 2.2 非目标（本 Phase 草案）

| 项 | 说明 |
|----|------|
| 进程级沙箱 | 仍 defer（见 TASKS T-905 推迟项） |
| 默认全盘读写 | Tier 4 不做 |
| 自动注册 host root | LLM 不得静默扩大托管区 |
| 替代 Git / evolve 治理 | host 操作不写 `evolve/prompts`（仍走 proposal） |
| 网络盘 / 云同步目录特殊语义 | 先按普通路径处理；冲突策略后补 |
| 二进制编辑、注册表、系统服务 | 不在首版范围 |

---

## 3. 配置模型

### 3.1 存储位置（已决 S7）

**`data/host_scope.json`**（gitignore，与 `data/sessions/` 同级）。**不**放进 `evolve/`（避免误 commit 个人路径）。**不**并入 `state.json`（职责分离：state 管 agent 根发现，host_scope 管托管区）。

### 3.2 Schema

```json
{
  "version": 1,
  "host_roots": [
    {
      "id": "downloads",
      "path": "C:/Users/21136/Downloads",
      "label": "下载",
      "added_at": "2026-07-11T12:00:00Z",
      "read": true,
      "write": false
    }
  ],
  "deny_globs": [
    "**/.ssh/**",
    "**/.gnupg/**",
    "**/AppData/**",
    "**/.env",
    "**/.env.*",
    "**/*credentials*",
    "**/id_rsa",
    "**/id_rsa.pub",
    "**/node_modules/**"
  ],
  "system_deny": true,
  "wizard_completed": true
}
```

| 字段 | 说明 |
|------|------|
| `host_roots[].path` | 绝对路径，规范化后存储；启动时校验存在且为目录 |
| `read` / `write` | 按目录粒度开关（首版可全体 `read:true, write:false`） |
| `deny_globs` | 在 host root **之内**仍拒绝的路径（glob）；命中即 **硬拒绝**，无 confirm 绕过 |
| `system_deny` | `true` 时叠加内置系统目录拒绝（见 §5.2） |
| `wizard_completed` | 可选；桌面 wizard 完成或跳过后为 `true` |

### 3.3 默认种子（已决 S8）

**首次启动 wizard**（桌面；`host_scope.wizard` WS；CLI 等价 `托管目录 添加 …`）：

1. 展示说明：托管区 = 你允许 Agent 读/写的电脑文件夹。
2. 可勾选：**下载**（`app.getPath('downloads')` → id `downloads`）、**桌面**（`desktop` → id `desktop`）。
3. 权限：**只读**（浏览 / grep）或 **读写**（可 `sort_by_extension` 等整理；对话内写仍每次 confirm）。
4. **只读**批量：点「继续」一次完成；**读写**：wizard 内再确认一次（S11）。
5. 「稍后」→ `wizard_completed: true`，`host_roots` 可仍为空。

**不**采用：静默默认 Downloads、也不强制空列表无提示。

---

## 4. 路径解析

### 4.1 与 `paths.py` 的关系

现有 API（保持不变）：

| 方法 | 边界 |
|------|------|
| `resolve_under_agent` | agent root |
| `resolve_under_workspace` | workspace/ |

**新增**（已决 S1～S2）：

| 方法 | 边界 |
|------|------|
| `resolve_under_host` | 解析 `host:<id>/relative`；`read:true` 可读；写另需 `write:true` |
| `is_under_host` | 判断绝对路径是否落在某 host root 内（配置校验用） |

解析规则：

- 拒绝 `..` 逃逸、空路径、NUL
- **仅接受** `host:<id>/...` 形式；裸相对路径、裸绝对路径在 host 工具中 **拒绝**
- `resolve()` 后路径仍须在 host root 内（**S9**；防符号链接逃逸）

### 4.2 路径表示（已决 S1）

| 场景 | 格式 | 示例 |
|------|------|------|
| 工具参数 / 日志 | `host:<id>/relative` | `host:downloads/2026/report.pdf` |
| Confirm / 错误 UI | 绝对路径 + host 标签 | `C:\Users\…\Downloads\…`（`downloads`） |
| LLM 对话引用 | 优先 `host:` 形式；可向用户复述绝对路径 | — |

**不接受**：`@downloads/...`、未带 `host:` 的 `C:/...`（host 工具内）。

### 4.3 agent root 与 host root 重叠（已决 S3）

**拒绝**将 agent root 或其任意子路径登记为 host root（配置写入时校验）。改 my-agent 仓内文件仍只走 agent 边界工具（`patch_file` 等）。

---

## 5. 安全策略

### 5.1 Confirm 分级（相对 workspace）

| 区域 | 读 | 写 | 会话 `a` 免确认 |
|------|----|----|-----------------|
| workspace + `workspace_only=true` evolved | confirm 否 | confirm 是 | **可** `a`（现状） |
| agent root 内 patch / coding | — | 每次 confirm | **否**（现状） |
| **host root 读** | **不** confirm | — | **否** |
| **host root 写** | — | **每次 confirm** | **否**（强制） |

写操作优先 `move_to_trash` 而非硬删（与 workflow 工具一致）。

### 5.2 内置 denylist（已决 S5）

`system_deny: true` 时叠加（Windows，实现时 `Path` 规范化）：

- `C:/Windows/**`
- `C:/Program Files/**`、`C:/Program Files (x86)/**`

`deny_globs`（含用户可扩展项，默认见 §3.2）在 **任意 host root 内** 命中即 **硬拒绝**：

- 整个 **`**/AppData/**`**（不单拆子树）
- `**/.ssh/**`、`**/.gnupg/**`
- `**/.env`、`**/.env.*`、`**/*credentials*`**
- `**/id_rsa`、`**/id_rsa.pub`**

错误码沿用 `PATH_OUT_OF_BOUNDS` 或新增 `PATH_DENIED`（实现时二选一，TOOLS 同步）。

### 5.3 日志与隐私

- `evolve_log` 字段：
  - 单 root：`host_root_id`、`host_relative_path`
  - 跨 root 写：`host_src_id`、`host_src_rel`、`host_dst_id`、`host_dst_rel`
- **不**在默认日志中记完整绝对路径（减泄露面）；confirm UI 仍显示绝对路径
- **禁止**记录大文件正文；与 TOOLS §10 一致
- 敏感路径：**硬拒绝**，不进入 LLM context（**S5**）

### 5.4 跨 host root 写操作（已决 S10）

**允许**单次 `copy` / `move` 的源与目标落在 **不同** `host_roots[].id` 下，例如：

`host:downloads/report.pdf` → `host:documents/归档/report.pdf`

| 检查项 | 规则 |
|--------|------|
| 源路径 | 所属 host root `read: true`；通过 denylist |
| 目标路径 | 所属 host root `write: true`；通过 denylist |
| Confirm | **一次** confirm 覆盖整笔操作；卡片展示 **源 + 目标** 绝对路径及各自 host 标签；**无** `a` |
| 日志 | `host_src_id` + `host_src_rel` + `host_dst_id` + `host_dst_rel`（见 §5.3） |

**不允许**（仍硬拒）：

- 源或目标任一侧未登记为 host root
- 源 root 无读、或目标 root 无写
- 任一侧命中 denylist / `system_deny`
- 经 `workspace` 或 agent root **中转**的跨区操作不在本条款自动豁免——仍按各自边界工具处理

同 root 内操作（`host:downloads/a` → `host:downloads/b`）按 §5.1 普通 host 写处理，confirm 规则相同，UI 可只强调一个 host 标签。

---

## 6. 工具面

### 6.1 原则（已决 S6）

- **不**扩展现有 Builtin `read_file` / `list_dir` / `grep` 到 host（避免默认放大所有对话读范围）。
- **主路径**：`run_evolved` → `host_read` / `host_list` / `host_grep`（名待定，放 `evolve/tools/common/` 或 `workflow/`）及 workflow 写工具扩展。
- **例外**：若实测 LLM 频繁选错 tool，再评估 **1～2 个** host builtin（与 6 Builtin 并列），不先行增加。

### 6.2 分阶段交付

| 阶段 | 能力 | 示例 |
|------|------|------|
| **P10a 只读** | 列目录、读文本、grep | 「Downloads 里最新的 pdf」「搜某关键词」 |
| **P10b 写** | copy/move、rename、归档、软删 | 复用/扩展 `copy_move`、`rename_batch`、`archive_by_date` |
| **P10c 场景** | 托管区专用 memory + prompt 片段 | `workflow` 主题下「整理下载夹」记忆 |

### 6.3 与现有 evolved 工具

| 现有工具 | 现状 | host 扩展方式（草案） |
|----------|------|------------------------|
| `copy_move` | 仅 workspace | `host_copy_move`（或等价）：同 root 与 **跨 root**（S10）均支持 |
| `sort_by_extension` 等 workflow | 路径相对 workspace | 支持 `host:<id>/...` 目标路径 |
| `patch_file` | agent root | **不**扩展到 host（改代码仍指 my-agent 仓） |

`tool.toml` 可能新增 policy 字段（草案）：

```toml
[policy]
workspace_only = false
host_allowed = true   # 新：允许解析 host 路径
host_write = false    # 新：默认 false；写工具显式 true
```

### 6.4 桌面 WebSocket API（T-1008，已实现）

实现：`agent-core/host_scope_api.py` · `server.py` `_dispatch_host_scope` · `desktop/src/host-settings.ts`。

| 客户端 `type` | 说明 | 服务端响应 |
|---------------|------|------------|
| `host_scope.list` | 拉取当前配置 | `host_scope.state` |
| `host_scope.add` | `{ host_id, path, label?, write? }` | `host_scope.updated` |
| `host_scope.remove` | `{ host_id }` | `host_scope.updated` |
| `host_scope.write` | `{ host_id, write: bool }` | `host_scope.updated` |
| `host_scope.repath` | `{ host_id, path }` 更换绑定目录 | `host_scope.updated` |
| `host_scope.wizard` | `{ entries: [{ host_id, path, label?, write? }] }` 或 `{ skip: true }` | `host_scope.updated` |

`host_scope.state` / `host_scope.updated` 载荷：

```json
{
  "type": "host_scope.state",
  "roots": [{ "id": "downloads", "path": "…", "read": true, "write": false, "permissions": "只读", … }],
  "wizard_suggested": false
}
```

`wizard_suggested === true` 当且仅当 `host_roots` 为空且 `wizard_completed !== true`。

**Electron preload**：`pickDirectory()` · `getDownloadsPath()` · `getDesktopPath()`。

---

## 7. 对话层与桌面

### 7.1 System / prompt

`core.txt` 或 `safety` 段补充（实现时）：

- 主机路径须落在已登记托管区
- 不得建议用户把 agent root 加入托管区来「绕过」进化流程
- 写 host 前优先 `dry_run`

### 7.2 桌面 UI（grow）

| 能力 | 说明 |
|------|------|
| 顶栏 **托管区** | 打开 `host-settings` 面板（`desktop/src/host-settings.ts`） |
| 列表 | id、只读/读写、绝对路径 |
| 添加 | 文件夹 picker → 填 id / 权限 → **确认**（S11） |
| 开启写 / 删除 | UI 内 **确认**（S11）；关闭写可直接操作 |
| **更换文件夹** | `host_scope.repath`；**确认**后更新绑定路径 |
| 首次 wizard | 下载 + 桌面勾选；只读/读写；读写再确认 |
| 只读提示 | 「整理文件请开启写权限」 |
| Confirm 卡片（host 写） | **绝对路径** + host 标签；无 `a`；跨 root 时源→目标两行 |
| `turn.notice` | 未登记路径等可读错误 |

CLI 等价：`托管目录 列表` · `托管目录 添加 …` · `托管目录 删除 …` · `托管目录 写 … 开|关`（**无** repath；可删后重加）。

### 7.3 Activity Router

整理类意图（Downloads、桌面、归档）→ 倾向 `workflow` + `daily` shell（T-904g defer 后可联动）。

---

## 8. 未决问题

### 8.1 已决（归档）

| ID | 问题 | 决议 |
|----|------|------|
| Q1 | host 相对路径基准 | 仅 `host:<id>/...`，无裸相对路径 |
| Q2 | 参数路径格式 | 同 S1 |
| Q3 | 与 agent root 重叠 | 拒绝登记 |
| Q4 | 只读 confirm | 不要 |
| Q5 | AppData | 整个 `AppData/**` 硬拒 |
| Q6 | 敏感文件名 | 硬拒（见 §5.2） |
| Q7 | Builtin vs evolved | evolved 为主 |
| Q8 | 配置文件 | `data/host_scope.json` |
| Q9 | 跨 host root `copy_move` | **允许**（S10）；源 read + 目标 write；每次 confirm |
| Q10 | 符号链接 | resolve 后须在 root 内 |
| Q11 | 增删 host root confirm | **S11**：添加 / 开 write / 删除须 confirm；wizard 只读批量可一次；读写 wizard 再确认 |

### 8.2 仍开放

（无——实现阶段若遇边缘情况再开 ADR 或 PATCH 条目。）

---

## 9. 实施顺序

> 正式条目见 [TASKS.md](./TASKS.md) Phase 10。设计评审 **已决**（本文 v0.2.1）。

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-1001 | 设计评审 | `HOST-SCOPE.md` v0.2.1 | Phase 9 | §8 全部已决 | **done** |
| T-1002 | `host_scope.json` 加载与校验 | `agent-core/host_scope.py` | T-1001 | [TASKS](./TASKS.md) §T-1002 | **done** |
| T-1003 | `paths.resolve_under_host` | `paths.py` + `host_scope.resolve_host_path` | T-1002 | [TASKS](./TASKS.md) §T-1003 | **done** |
| T-1004 | CLI / REPL 托管目录管理 | `host_scope_cli.py` + `main.py` | T-1002 | [TASKS](./TASKS.md) §T-1004 | **done** |
| T-1005 | host 只读工具 | `host_tools.py` + `evolve/.../host_*` | T-1003 | [TASKS](./TASKS.md) §T-1005 | **done** |
| T-1006 | host 写工具 + confirm | `host_copy_move` + `executor`/`logging` | T-1005 | [TASKS](./TASKS.md) §T-1006 | **done** |
| T-1007 | workflow 工具适配 host 路径 | `evolve/tools/workflow/*` | T-1006 | [TASKS](./TASKS.md) §T-1007 | **done** |
| T-1008 | 桌面设置 UI | `desktop/src/host-settings.ts` 等 | T-1004 | [TASKS](./TASKS.md) §T-1008 | **done** |

**建议实现顺序**：T-1001 评审 → T-1002～T-1004（配置与解析）→ T-1005（只读闭环）→ 自用一段时间 → T-1006～T-1008（写与 UI）。

---

## 10. 验收场景（行为导向）

评审通过后，至少应能手工演示：

1. **登记**：wizard 或设置添加 `downloads` / `desktop`（只读或读写）；`host_list` 可见文件。
2. **写整理**：`write: true` 后 `sort_by_extension` on `host:downloads`（先 dry_run）。
3. **拒绝**：对未登记路径 `read` → 明确错误；不碰文件。
4. **拒绝**：尝试登记 `my-agent` 仓路径 → 配置层拒绝。
5. **拒绝**：读 `host_root/.ssh/id_rsa`（若在 deny 内）→ 拒绝。
6. **更换路径**：桌面「更换文件夹」→ `host_scope.repath` 更新绑定。
7. **跨 root 写**：`host_copy_move` 源 read + 目标 write；一次 confirm，无 `a`；源仅 read或目标无 write → 拒绝。
8. **分离**：改 `agent-core` 仍走 `patch_file`；整理 Downloads 不走 `write_evolve`。

---

## 11. 文档与代码交叉引用（实现后更新）

| 文档 | 需修订章节 |
|------|------------|
| [TOOLS.md](./TOOLS.md) | §2 路径、§8.1 写入边界、§6.3 confirm、§10 安全 |
| [PROJECT.md](./PROJECT.md) | §6.4 补充 host scope 诚实声明 |
| [MAP.md](./MAP.md) | Phase 10 进度 |
| [DESKTOP.md](./DESKTOP.md) | 设置页、confirm 文案 |
| `agent-core/prompts/core.txt` | 路径与 host 规则 |

---

## 12. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0-draft | 2026-07-11 | 首版粗糙草案 |
| 0.2.0 | 2026-07-11 | 首轮评审：§0 已决摘要；Q1～Q8/Q10 收敛 |
| 0.2.1 | 2026-07-11 | Q9→S10 允许跨 root copy_move；Q11→S11；T-1001 done |
| 0.2.2 | 2026-07-11 | T-1002 实现：`host_scope.py`；TASKS 手工验收清单 |
| 0.2.3 | 2026-07-11 | T-1003：`resolve_under_host` / `resolve_host_path` |
| 0.2.4 | 2026-07-11 | T-1004：`host_scope_cli` REPL + confirm |
| 0.2.5 | 2026-07-11 | T-1005：`host_list` / `host_read` / `host_grep` |
| 0.2.6 | 2026-07-11 | T-1006：`host_copy_move` + evolve_log host 字段 |
| 0.2.7 | 2026-07-11 | T-1007：workflow `host:` 路径 + executor `_arguments_use_host_scope` |
| 0.2.8 | 2026-07-11 | T-1008：桌面托管区 WS API + `host-settings` |
| 0.2.9 | 2026-07-11 | T-1008 增强：wizard 下载/桌面 + 读写；`host_scope.repath`；`set_host_root_path` |
