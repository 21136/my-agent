# 对齐 Cursor 剩余面（CURSOR-ALIGN）

> 版本 **0.6.0** · 2026-08-04 · **状态：Track A～G 主体 done；后续见 Phase 42**  
> 用户：「对齐 Cursor 还缺的都做，先列文档」  
> 基线：Phase 28 [SHELL-CHANNEL.md](./SHELL-CHANNEL.md) **M0+M1 done**（`run_command` + 归档 `mvn_exec`/`npm_exec`/`jshell_exec`）  
> 关联：[CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) · [WORKBENCH-UI.md](./WORKBENCH-UI.md) · [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md) · [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) · [PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md) · [GIT-VENDOR.md](./GIT-VENDOR.md) · [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) · [UX-POLISH.md](./UX-POLISH.md)

---

## 0. 一句话

Cursor 写项目靠 **少原语 + 低摩擦确认 + 终端一体 + 打开即干活**。  
my-agent 工具筋（读改 + 通用命令 + 长驻）已立；本清单收齐 **还差的 7 条面**，按 Phase 推进，**先签后写**。

---

## 1. 已对齐（不再重做）

| Cursor 感 | my-agent | 状态 |
|-----------|----------|------|
| 读 / 列 / 搜 | builtin `read_file` · `list_dir` · `grep` · `glob_file_search`（Phase 42） | done / **42-I** |
| 写 / 补丁 | `write_text` · `append_text` · `patch_file` | done（可再收敛，见 Track C） |
| 通用终端（一次性） | `run_command` | Phase 28 M0+M1 |
| 长驻进程 | `run_service` + 侧栏 Services | Phase 25～27 |
| HTTP 探活 | `http_request` | Phase 26 |
| 受控 commit | `git_commit` | Phase 26 |

**刻意不做成第二个 IDE**（完整 LSP、多文件 diff 编辑器、远程 SSH 等）——对齐的是 **Agent 原语与工作流**，不是 VS Code 壳。

---

## 2. 剩余 7 轨（总表）

| 轨 | 名称 | 建议 Phase | 主文档 | 依赖 |
|----|------|------------|--------|------|
| **A** | 确认放宽（先严后松 · M2） | **29** | 扩 [SHELL-CHANNEL.md](./SHELL-CHANNEL.md) §M2 | Phase 28 |
| **B** | 收尾归档 `run_python` / `pip_install` | **29**（M1.5） | 同 SHELL-CHANNEL | A 可并行；RUNTIME-GUARDS |
| **C** | 编辑原语收敛 | **30** | 本文 §4.C | **C1 done**（`append_text` archived） |
| **D** | 终端深度（后台一体 / PTY） | **31** | 本文 §4.D → 或扩 SHELL-CHANNEL | A；run_service |
| **E** | Git 写侧扩展（branch / push） | **32** | 本文 §4.E → 或扩 PROJECT-DEV-TOOLS | git_commit |
| **F** | 浏览器 / 页面验证 | **33** | 本文 §4.F → 落地拆 `BROWSER.md` | confirm；可选 |
| **G** | 工作台 UI | **34** | [WORKBENCH-UI.md](./WORKBENCH-UI.md) | **M0 done**；可与 C 交错 |

**推荐实施顺序**：A → B → G（可插队）→ C → D → E → F。  
（手感与入口先改善；重终端 / 浏览器后置。）

---

## 3. 产品纪律（全轨共用）

1. **少原语**：能被 `run_command` / 读写覆盖的，不新增分域 `*_exec`。  
2. **先文档后实现**；每轨 DOC-04（矩阵行 + IT/S id）。  
3. **确认先严后松** 的「松」只在 Track A 显式签字后改；其它轨默认沿用当前 confirm。  
4. **长驻 ≠ 拉长超时**：不退出进程仍走 `run_service`（或 Track D 升格后的统一后台通道）。  
5. **不做** 远程多机、无边界系统盘 shell、完整 xterm 产品化（D 仅到「够用」）。

---

## 4. 各轨设计草案

### 4.A 确认放宽（Phase 29 · SHELL-CHANNEL M2）

**痛点**：每条 `run_command` 必确认，写项目节奏远慢于 Cursor。

**目标**

| 策略（待签选一主策略） | 说明 |
|------------------------|------|
| **A1 项目内免确认** | `working_dir` 落在当前 `project_root` 下且命令非破坏启发式 → 免确认 |
| **A2 分层** | 读类静默；构建/测试免确认；写盘/网络/装依赖确认；破坏性永远确认 |
| **A3 会话信任** | 首次确认后「本会话信任 run_command」；危险模式仍确认 |
| **默认提案** | **A2 + 危险永远确认**；`allow_approve_all` 仅对非危险生效 |

**非目标**：关闭全部确认；对 `run_service start/kill_port` 一并免确认（长驻仍确认）。

**里程碑**

| | 内容 |
|--|------|
| D0 | 本文 + SHELL-CHANNEL §M2 签字（策略表） |
| M0 | 启发式分类器 + executor 门 + IT |
| M1 | 桌面确认卡展示「为何免确认 / 为何仍要」一句 |

**回归预留**：IT-110～112 · S-110

**开放问题**

| # | 问题 | 默认 |
|---|------|------|
| A-Q1 | 主策略 A1/A2/A3？ | **A2** |
| A-Q2 | `npm install` / `pip install` 算哪层？ | **须确认** |
| A-Q3 | 出 `project_root` 但在 agent root？ | **须确认** |

---

### 4.B 收尾归档（Phase 29 · M1.5）

**目标**：`run_python` → archived 或仅 scaffold 内核路径；`pip_install` → archived（改 `run_command`：`python -m pip …`）。

**前置**：RUNTIME-GUARDS 窄域拒调从 `run_python` **迁到**对 `run_command` 的 demo 路径启发式，或改为只认内核 `run_scaffold_demo`（推荐后者，模型不再跑 demo）。

**里程碑**

| | 内容 |
|--|------|
| M0 | guard 迁移 + IT 回归 RUNTIME-GUARDS |
| M1 | `pip_install` archived；INDEX 更新 |
| M2 | `run_python` archived；IT-103 扩覆盖 |

**回归**：既有 test_runtime_guards_m1 · 扩 IT-103

**开放问题**

| # | 问题 | 默认 |
|---|------|------|
| B-Q1 | `run_demo` / `run_tests`？ | **暂留**（coding 糖；非 Cursor 缺口） |

---

### 4.C 编辑原语收敛（Phase 30）

**痛点**：Cursor 侧偏少编辑工具；my-agent 有 write / append / patch 三件套（外加 copy_move）。

**目标（二选一，待签）**

| 方案 | 说明 |
|------|------|
| **C1 薄收敛** | 提示词 + INDEX 只主荐 `patch_file`（改已有）+ `write_text`（新建）；`append_text` → archived |
| **C2 统一 `apply_edit`** | 新工具：path + 旧片段/新片段或 unified diff；底层仍调现有写盘；旧三件套归档 |

**默认提案**：**C1**（改动小、风险低）— **已落地（2026-08-02）**：`append_text` → `archived`；INDEX/prompts 主荐 `write_text` + `patch_file`；IT-120。  
C2 留作后续若模型仍乱调再开。

**非目标**：做完整 IDE diff UI；多文件事务提交。

**回归预留**：IT-120～121

**开放问题**

| # | 问题 | 默认 |
|---|------|------|
| C-Q1 | C1 还是 C2？ | **C1** |
| C-Q2 | `append_text` 归档时机 | **与 C1 同批** |

---

### 4.D 终端深度（Phase 31）

**痛点**：Cursor 一条终端可前台/后台；my-agent 拆成 `run_command`（死超时）+ `run_service`（登记名）。

**目标（分档）**

| 档 | 内容 |
|----|------|
| **D0 文档** | 统一心智：`run_command` 会结束；后台 = `run_service` 或「升格」API |
| **D1 升格** | `run_command` 超时前可选 `background:true` → 内部转 `run_service` 登记，返回 name + 日志尾 |
| **D2 PTY** | 真交互（npm create 问答等）— **高成本**；默认 **defer**，除非签字升为做 |

**默认提案**：做 **D1**；**D2 defer**。  
**状态（2026-08-02）**：**D1 done**（IT-130）；D2 仍 defer。

**非目标**：嵌入式 xterm 产品窗；多 tab 终端模拟器。

**回归预留**：IT-130～132 · S-130

**开放问题**

| # | 问题 | 默认 |
|---|------|------|
| D-Q1 | 做 D1 还是连 D2？ | **仅 D1** |
| D-Q2 | 升格后的 service `name` 自动规则 | `cmd-<short-hash>` |

---

### 4.E Git 写侧扩展（Phase 32）

**现状**：`git_snapshot`（读）· `git_commit`（add+commit）· `git_clone`。

**目标**

| 工具/动作 | confirm | 约束 |
|-----------|---------|------|
| `git_branch`（create/switch/list） | switch/create 要 | 禁改 `git config`；禁 force checkout 丢改 |
| `git_push` | **永远确认** | 仅当前分支；禁 `--force` / `--force-with-lease` 默认关；lease 若做须另签 |
| PR（可选） | 确认 | 经 `gh`；无 `gh` 则明确失败；**M1 可 defer** |

**非目标**：交互 rebase、改 config、钩子安装、多 remote 复杂策略。

**回归预留**：IT-140～142

**开放问题**

| # | 问题 | 默认 |
|---|------|------|
| E-Q1 | M0 是否含 push？ | **含**（永远确认） |
| E-Q2 | PR / `gh`？ | **M1 defer** |
| E-Q3 | force-with-lease？ | **禁止** |

**状态（2026-08-02）**：**E M0 done**（`git_branch` · `git_push` · IT-140/141）；PR/`gh` 仍 defer。

---

### 4.F 浏览器 / 页面验证（Phase 33）

**痛点**：Cursor 可看页面；my-agent 只有 HTTP / fetch 文本。

**目标（分档）**

| 档 | 内容 |
|----|------|
| **F0** | 文档：验收以 `http_request` + 用户浏览器为主 |
| **F1** | `browser_open`：用系统默认浏览器打开 URL（loopback 可免确认；外网确认） |
| **F2** | 无头截图 / DOM 断言（Playwright 等）— **重依赖**；默认 **defer** |

**默认提案**：做 **F1**；**F2 defer**（除非你要自动化 UI 验收再升）。

**回归预留**：IT-150 · S-150

**开放问题**

| # | 问题 | 默认 |
|---|------|------|
| F-Q1 | F1 还是连 F2？ | **仅 F1** |

**状态（2026-08-02）**：**F1 done**（`browser_open` · IT-150）；F2 Playwright 仍 defer。

---

### 4.G 工作台 UI（Phase 34）

**文档已有**：[WORKBENCH-UI.md](./WORKBENCH-UI.md) **v0.3.0**（入口 / 侧栏加速器 / Q4 空态「先聊聊」）。

**本清单补强**

- 与 Track A 联动：免确认时 UI 仍显示「已执行（策略放行）」短条，避免黑盒。  
- 与 Phase 27 对齐：RunningCard / Services 保留。  
- WORKBENCH **Q1～Q4** 已签（Q4 = 空态「先聊聊」→ grow 无绑；实现 T-3410～3413）。

**里程碑**：沿 WORKBENCH-UI §4（M0 done · **M1 = Q4 待实现** · M2）。

---

## 5. Phase / 任务草表（落 TASKS）

| Phase | 轨 | 设计交付 | 实现入口 task（草） |
|-------|-----|----------|---------------------|
| **29** | A+B | 本文 §4.A/B + SHELL-CHANNEL M2 修订 | T-2901 文档签字 → T-2902 确认策略 → T-2903 guard/归档 |
| **30** | C | 本文 §4.C（或 `EDIT-PRIMITIVES.md`） | T-3001… |
| **31** | D | 本文 §4.D / SHELL-CHANNEL 扩 | T-3101… |
| **32** | E | 本文 §4.E / PROJECT-DEV-TOOLS 扩 | T-3201… |
| **33** | F | 本文 §4.F（或 `BROWSER.md`） | T-3301… |
| **34** | G | WORKBENCH-UI 签字 + 实现 | T-3401… |

**DOC-04（总）**

| 面 | 可能影响 |
|----|----------|
| confirm 管线 | A |
| evolve 工具 / catalog | B C E F |
| executor / run_service | D |
| 桌面壳 / 入口 | G |
| RUNTIME-GUARDS | B |
| Progress Gate | B（证据工具名） |

各 Phase 开工前在本表对应节勾 IT/S 具体编号。

---

## 6. 签字清单 → **已决**（2026-08-02 用户：「开始吧」采纳默认）

| 轨 | 已决 |
|----|------|
| A | **A2** 分层 + 装依赖确认 + 危险永远确认 |
| B | 迁 guard；归档 `pip_install` + `run_python`；留 `run_demo`/`run_tests` |
| C | **C1** 薄收敛（Phase 30） |
| D | 仅 **D1** 升格；PTY defer（Phase 31） |
| E | branch + push 禁 force；PR defer（Phase 32） |
| F | 仅系统浏览器打开（Phase 33） |
| G | WORKBENCH Q1～Q3 用该文档建议默认（Phase 34） |

---

## 7. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.0 | 2026-08-02 | 初稿：7 轨路线图 + 默认提案 + Phase 29～34 草表 |
| 0.2.0 | 2026-08-02 | §6 已决；Phase 29 Track A+B 开工 |
| 0.3.0 | 2026-08-02 | Phase 31 D1 done（IT-130）；下一焦点 Phase 32 E |
| 0.4.0 | 2026-08-02 | Phase 32 E done（IT-140/141）；下一焦点 Phase 33 F |
| 0.5.0 | 2026-08-02 | Phase 33 F1 done（IT-150）；下一焦点 Phase 34 G M1/M2 |
| 0.6.0 | 2026-08-04 | Track A～G 主体 done；**Phase 42** 承接写确认分层 · Glob · 模型路由 → [CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) |

---

## 8. 后续：Phase 42（2026-08-04）

CURSOR-ALIGN 七轨解决 **「能跑」**；[CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) 收 **写确认分层（H）** · **Glob/语义搜（I）** · **模型路由（J）**。

| 轨 | 对标缺口 | 任务段 |
|----|----------|--------|
| H | `write_text`/`patch_file` 未像 `run_command` 分层免确认 | T-4210～4214 |
| I | 大仓按名找文件（M0 Glob；M2 语义 defer） | T-4220～4225 |
| J | Harness P3：规划 pro / 执行 flash | T-4201～4203 · [LLM-ROUTING.md](./LLM-ROUTING.md) |

**推荐顺序**：H → J → I(M0)。签字清单见 CURSOR-GAP-NEXT §6。
