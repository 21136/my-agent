# 通用执行通道（SHELL-CHANNEL）

> 版本 **0.4.0** · 2026-08-02 · **状态：M0+M1 done + Phase 31 D1**（IT-100～103 · IT-130；S-100 手工）  
> Phase **28** · 关联：[RUN-SERVICE.md](./RUN-SERVICE.md) · [PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md) · [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md) · [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · [TOOL-CATALOG.md](./TOOL-CATALOG.md) · [UX-POLISH.md](./UX-POLISH.md) §6.3.3 / 治本 7 · [WRITE-SCOPE.md](./WRITE-SCOPE.md)

## 0. 为什么开这个 Phase

Cursor 写项目靠 **少原语**：读/改文件 + **一条通用终端**。  
my-agent 今天是 **多条分域跑命令工具**（`mvn_exec` / `npm_exec` / `run_python` / `pip_install` / `repl`…）+ **一条长驻通道**（`run_service`）。模型要挑对工具；每多一个运行时就多一个 `*_exec`（见 UX-POLISH §6.3.3）。

产品定筋（思考会话 2026-08-02）：

| # | 已决 |
|---|------|
| D1 | 目标是 **Cursor 式通用执行通道**，不是长期双轨 |
| D2 | 旧分域工具 **逐步归档**（先砍跑命令类） |
| D3 | 通道形态 **接近真 shell**（`cwd` + `command`；stdout/stderr/exit；长跑可后台 + 日志尾巴） |
| D4 | 安全 **先严后松**：M0 **每条命令必确认**；跑稳后再按习惯放宽 |

**相对 Phase 26 的修订**：[`PROJECT-DEV-TOOLS.md`](./PROJECT-DEV-TOOLS.md) §1.2 / §2.1 曾把「裸 shell」标为 defer、并写「不引入无边界的通用 shell」。本 Phase **显式废止该条**——仍 **有边界**（路径、超时、确认、与 `run_service` 分工），但允许 **接近真 shell 的一条通道** 替代分域 `*_exec`。

UI 工作台（[`WORKBENCH-UI.md`](./WORKBENCH-UI.md)）仍次要：先立工具筋。

---

## 1. 目标与非目标

### 1.1 目标

1. 提供 **一条** 前台通用命令通道（工作名 **`run_command`**；与 UX-POLISH 的 `shell_exec` 同义，落地择一名，INDEX 只暴露一个）。
2. 覆盖今日 `mvn_exec` / `npm_exec` / `run_python` / `pip_install` / 多数 `repl` 一次性用法。
3. **长驻不退出**进程继续走 **`run_service`**（不把超时无限拉长塞进 `run_command`）。
4. 归档节奏：先从 **目录 / 提示词** 摘掉旧跑命令工具推荐，再 `status=archived`（可执行面先 deprecate 再删）。
5. 复用 Phase 27 可观测：RunningCard · `tool.progress` · `logs_tail`。

### 1.2 非目标（本 Phase）

- 完整嵌入式 xterm / PTY 交互（可日后；M0 只要捕获输出）
- 远程/多机 shell、无确认任意系统盘读写
- 一次归档全部 workflow 工具（`flatten_dir` 等 **暂留**）
- 用 `run_command` 永久替代 `run_service` 的托管登记与侧栏 Services
- M0 就做「项目内免确认」等宽松策略（见 D4）

---

## 2. 分工（一张表）

| 场景 | 走哪条 | 说明 |
|------|--------|------|
| 会结束的命令（build / test / npm i / 脚本） | **`run_command`** | 有超时；stdout/stderr/exit |
| 不退出的服务（spring-boot:run / npm run dev） | **`run_service`** | 后台 + 状态文件 + 侧栏 |
| 读写改文件 | builtin + write/patch 类 | 不变 |
| HTTP 探活 | `http_request` | 不变 |
| 主机托管区 | host scope + 桌面设置 | 不变；shell **默认不**扩到任意 host 盘（除非日后单独门） |

```text
LLM
 ├─ run_command     → 前台子进程（超时杀）→ tool.end
 ├─ run_service     → 后台托管 → services.* / 侧栏
 └─ 其它 evolved    → 既有语义
```

---

## 3. 契约草案 · `run_command`

### 3.1 输入 / 输出

```text
输入：
  command: string          # 接近真 shell：整行交给平台 shell（Win: cmd/PowerShell 策略见 §3.3）
  working_dir?: string     # 相对 agent root 或已解析绝对路径；默认当前 project_root 或 agent root
  timeout_sec?: number     # 默认 120；硬顶建议 ≤ 600（与 policy 对齐）
  env?: object             # 可选增量环境变量（禁止覆盖敏感内核变量清单，实现时列 denylist）

输出（ToolResult data）：
  ok: bool                 # exit_code == 0
  exit_code: int
  stdout: string           # 截断 + truncated 标志
  stderr: string
  cwd: string              # 实际工作目录
  elapsed_ms: int
  truncated?: bool
```

### 3.2 Policy（M0）

| 项 | 值 |
|----|-----|
| `status` | `active`（新工具） |
| `[policy].confirm` | **`true`（永远；M0 无动作级豁免）** |
| `allow_approve_all` | **`false`（M0）** — 即使会话点过「全部允许」也不跳过本工具 |
| `timeout_sec`（policy） | ≥ 输入硬顶 + 余量（如 610） |
| dry_run | 可选：只打印将执行的 cwd/command，不 spawn |

**先严后松（D4）**：放宽（项目内免确认、approve_all、启发式分层）**另开里程碑 / 修订本文**，不在 M0 夹带。

### 3.3 平台与「真 shell」边界

| 项 | M0 已决倾向 |
|----|-------------|
| 调用方式 | **shell 模式**执行整行 `command`（Cursor 体感）；实现注明 Win 用的解释器（建议 `powershell -NoProfile -Command` 或 `cmd /c`，**定一种并写进 tool 描述**） |
| argv 模式 | **可选后续**：`argv: string[]` 免 shell 注入面；M0 可不做 |
| `cwd` | 必须经 `paths` 解析落在 **agent root**（与 WRITE-SCOPE 一致）；越界拒绝 |
| deny | 不额外解析「危险命令黑名单」作硬拒（M0 靠确认）；可在 preview 里高亮 `rm`/`Remove-Item`/`format` 等（软提示） |
| 输出顶 | stdout/stderr 各硬顶（如 64KiB）+ 超长落盘指针（复用 executor 超长结果机制） |

### 3.4 与长驻的边界（硬）

- 无 `background` 的 `run_command` **不得**用于「预期永不退出」的服务（会超时被杀）。  
- 提示词 / `tool-catalog/buckets/run.md`：**长驻 → `run_service`**，或 **`run_command` + `background:true`（D1 升格）**。  
- **D1（Phase 31）**：`background:true` → 内部 `run_service` start；默认名 `cmd-<8hex>`；永远 confirm；不计入 Progress Gate 证据。

### 3.5 确认预览

`confirm.request` preview 至少含：

- 实际 `cwd`
- 完整 `command`（截断过长行）
- `timeout_sec`
- 一句：「完成后退出；长驻请用 run_service」

复用 Phase 14/15/27：同意后 RunningCard「已同意，执行中…」；`tool.progress` 心跳；`tool.end` + 可选 `logs_tail`。

---

## 4. 归档计划（跑命令类优先）

### 4.1 第一波（本 Phase 主路径）

| 工具 | M1 状态 | 备注 |
|------|---------|------|
| `mvn_exec` | **archived** | → `run_command` |
| `npm_exec` | **archived** | → `run_command` |
| `jshell_exec` | **archived** | 原 suspect |
| `repl` | **archived** | → `run_command`（T-4308 · IT-437） |
| `pip_install` | active（晚半拍） | 仍可用 |
| `run_python` | active（M1.5） | scaffold guard 仍依赖 |

**手段**：`tool.toml` `status=archived`（出执行面 + INDEX）；**不**立刻删目录（便于回滚）。

### 4.2 暂留

- `run_service` / `dev_start` / `http_request` / `git_*` / `db_query`
- workflow 类（`flatten_dir` / `rename_batch` / `study_note`…）— **第二波**，本 Phase 不强制

### 4.3 提示词与目录

- `evolve/tool-catalog/buckets/run.md`：主荐 `run_command` + `run_service`
- `evolve/prompts` / `core.txt`：禁再主推 `mvn_exec`/`npm_exec` 作默认跑命令
- Progress Gate 证据映射：把 compile/test/build 类从旧工具名迁到 `run_command`（实现时改分类器 + IT）

---

## 5. 里程碑

| 里程碑 | 内容 | 完成标志 |
|--------|------|----------|
| **D0** | 本文 + MAP/TASKS DOC-04 | **done** |
| **M0** | `run_command` 前台 + 超时 + **全确认** + INDEX/提示 + IT-100～102 | **done** |
| **M1** | 旧 `*_exec` archived + IT-103 + S-100 | **done** |
| **M2** | 确认策略放宽（A2 分层） | **done**（Phase 29 · `run_command_policy` · IT-110） |
| **defer** | PTY、argv 模式、workflow 归档、host 盘 shell | → Track D/F 等（见 CURSOR-ALIGN） |

---

## 6. DOC-04 准入

### 6.1 影响矩阵行（STABILIZATION §3）

| 面 | 档位 | 说明 |
|----|------|------|
| **evolve 工具执行** | 新增 / 变更 | 新 `run_command`；跑命令类归档 |
| **confirm 管线** | 变更（策略） | M0 对本工具强制 confirm；暂不放宽 approve_all |
| **tool-catalog / 提示词** | 变更 | run bucket 主路径替换 |
| **Progress Gate 证据类** | 可能变更 | 旧工具名 → `run_command` |
| 桌面壳 / host / 计划门 / 换线 | **无**（仅复用 RunningCard 事件） | — |
| `run_service` 语义 | **无破坏** | 长驻仍独占 |

实现前：在 [STABILIZATION.md](./STABILIZATION.md) §3 补一行「通用 shell 通道」或并入 evolve 执行面备注。

### 6.2 回归 ID（预留）

| ID | 内容 |
|----|------|
| **IT-100** | `run_command` 成功：exit 0 + stdout；cwd 越界拒绝 |
| **IT-101** | M0：**无 confirm 不得执行**；`allow_approve_all` 会话仍要确认 |
| **IT-102** | 超时杀进程（短 sleep 超 timeout） |
| **IT-103** | 归档后 `mvn_exec`/`npm_exec` 不可经 `run_evolved` 调用（或 status 过滤） |
| **S-100** | 桌面：确认 → RunningCard → 完成；长驻仍用 `run_service` 冒烟 |

回归既有：**IT-75/76**（`run_service`）· **IT-90～93**（可观测）· confirm 90s / Stop。

---

## 7. 任务草表（落 TASKS 时用）

| ID | 任务 | 依赖 | 状态 |
|----|------|------|------|
| T-2801 | 设计落盘 + MAP/TASKS 挂钩 | — | **doc**（本文） |
| T-2802 | 开放问题签字（§8） | T-2801 | todo |
| T-2803 | M0：`run_command` 工具 + policy | T-2802 | todo |
| T-2804 | M0：executor confirm 硬门（本工具不可 a 跳过） | T-2803 | todo |
| T-2805 | M0：INDEX / prompts / Progress Gate 映射 | T-2803 | todo |
| T-2806 | M0：IT-100～102 | T-2803,T-2804 | todo |
| T-2807 | M1：归档第一波 `*_exec` + IT-103 | T-2806 | todo |
| T-2808 | M1：S-100 + 文档修订 v0.2 | T-2807 | todo |
| T-2809 | M2：确认放宽（另签） | T-2808 | defer |

---

## 8. 开放问题 → **已决**（2026-08-02 用户：「可以」采纳默认）

| # | 问题 | 已决 |
|---|------|------|
| Q1 | 工具正式名 | **`run_command`**（与 UX-POLISH `shell_exec` 同义；INDEX 只暴露一名） |
| Q2 | Windows shell | **`powershell -NoProfile -NonInteractive -Command`** |
| Q3 | `run_python` / RUNTIME-GUARDS | **M1 先 INDEX 降权**；guard 迁到 `run_command` 或保留薄封装至 M1.5 |
| Q4 | M0 是否允许 `env` | **允许增量**；敏感键 denylist（实现时列清单） |
| Q5 | 输出硬顶 | **stdout/stderr 各 64KiB** + truncated / 超长落盘 |

原默认提案作审计底稿；实现以本表为准。

---

### S-100 手工冒烟（桌面）

1. 项目会话调 `run_command`（如 `echo hi`）→ 确认卡 → RunningCard → 完成。
2. 故意调 `mvn_exec` → 应失败（status=archived / 不在清单）。
3. 长驻：`run_service` start，或 `run_command` + `background:true`（升格登记）。

---

## 9. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-02 | 初稿：D1～D4；废止 Phase 26「不做裸 shell」；M0 全确认真 shell；与 `run_service` 分工；归档第一波；DOC-04 / IT-100+ |
| 0.2.0 | 2026-08-02 | §8 Q1～Q5 已决；状态改为设计已签 |
| 0.2.1 | 2026-08-02 | M0：`run_command` + confirm 硬门 + catalog/prompts + IT-100～102 |
| 0.3.0 | 2026-08-02 | M1：归档 mvn/npm/jshell；repl→suspect；非 active 硬拒；IT-103 |
| 0.4.0 | 2026-08-02 | Phase 31 D1：`background:true` → run_service 升格；IT-130 |
