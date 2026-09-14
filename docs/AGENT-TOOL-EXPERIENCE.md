# Agent 工具体验路线图（Codex / Cursor 对齐）

> 版本 **0.1.8** · 2026-09-08 · 状态：**P0 终端会话、Git 文件级回退和结构化测试已进入 active；原子编辑仍为 experimental，终端会话 UI 已完成设计**
> 关联：[CURSOR-ALIGN.md](./CURSOR-ALIGN.md) · [CURSOR-GAP-NEXT.md](./CURSOR-GAP-NEXT.md) · [TOOL-CATALOG.md](./TOOL-CATALOG.md) · [RUNAWAY-V2.md](./RUNAWAY-V2.md) · [TASKS.md](./TASKS.md)

## 0. 目标与边界

目标不是做第二个 VS Code，也不是复制 Cursor 的完整编辑器壳，而是补齐 Agent 在真实项目中反复使用的开发原语和工作流：

```text
发现上下文 → 修改文件 → 执行命令 → 观察结果 → 检查变更 → 修复/回退 → 交付
```

当前项目已经有较完整的项目闸门、狂奔编排和基础工具面。下一阶段的重点是让这些工具在 Agent 手里具备更低摩擦、更可恢复、更易审计的体验。

特别说明：**狂奔是编排层，不是工具层替代品**。它可以自动推进任务、测试、修复和验证，但最终能力上限仍由终端、编辑、Git、测试和浏览器等底层原语决定。

## 1. 当前能力盘点

以下能力已经存在，本路线不重复建设：

| 能力 | 当前工具/机制 | 当前判断 |
|------|---------------|----------|
| 文件读取与目录发现 | `read_file` · `list_dir` · `grep` · `glob_file_search` · `codebase_search` | 已有；语义搜索仍需看索引质量 |
| 文件写入与补丁 | `write_text` · `patch_file` | 已有；编辑契约和失败恢复仍需收敛 |
| 一次性命令 | `run_command` | 已有；缺面向 LLM 的交互式会话工具 |
| 长驻服务 | `run_service` | 已有；服务管理不是 PTY |
| HTTP / 浏览器入口 | `http_request` · `browser_open` | 已有；缺页面级自动检查 |
| Git 基础操作 | `git_snapshot` · `git_branch` · `git_commit` · `git_push` | 已有；缺清晰的一等 diff/restore 工作流 |
| 项目测试与质量 | `run_tests` · `run_project_tests` · `run_quality` · `structured_test` | 已有执行器；`structured_test` 已开始统一结果协议 |
| 计划、探索、审查 | `plan_partner` · `explore` · `deliverable_review` | 已有；隔离并行和按变更审查仍不完整 |
| 狂奔编排 | 狂奔 v1/v2、checklist、续接、验证出口 | 已有；依赖底层工具的可观察性和可恢复性 |

原则：已有工具的问题优先通过契约、组合和输出结构解决，不因为体验不足就随意新增同义的 `*_exec` 工具。

当前基线是 **12 个 builtin function（核心 8 + 编排 4）**。实现提示词变更时必须同时修正旧文案中“6 Builtin”或“11 个 builtin”的过期说法；这是文档同步任务，不代表新增 builtin。

## 2. 开源优先选型

本项目采用“先找成熟开源实现，再写本项目适配层”的默认顺序。开源项目不是自动可嵌入：引入前仍需核对版本、传递依赖、许可证、运行时资产和安全边界。以下结论是 **2026-09-08** 的方案基线，许可证以官方仓库当前根许可证和发布包元数据为准，不能替代发布前的 license scan。

### 2.1 可直接采用或优先适配的组件

| 能力 | 首选开源组件/基础设施 | 许可证/来源 | 复用边界 | 决策 |
|------|----------------------|------------|----------|------|
| Windows 交互终端 | `pywinpty` + Windows ConPTY | pywinpty：MIT；[pywinpty](https://github.com/andfoy/pywinpty)；ConPTY 为 Windows 系统 API | 只复用 PTY/控制台进程通信；session、权限、确认、日志和孤儿清理仍由本项目负责 | **优先适配** |
| unified diff 解析 | `python-unidiff` | MIT；[unidiff](https://github.com/matiasb/python-unidiff) | 只负责 diff/hunk 解析和定位；原子落盘、base hash、ledger、计划门不交给库 | **直接依赖候选** |
| 浏览器自动化 | Playwright Python | Apache-2.0；[playwright-python](https://github.com/microsoft/playwright-python) | 复用 browser/page 操作和事件；context 隔离、allowlist、凭据清理、结果截断由本项目包住 | **优先适配** |
| 测试结果 | pytest 原生 JUnit XML；必要时 `pytest-json-report` | pytest：MIT；pytest-json-report：MIT；[pytest-json-report](https://github.com/pytest-dev/pytest-json-report) | 复用报告格式；`structured_test` 统一状态、失败定位、超时、取消和 rerun 语义 | **先适配现有工具** |
| AST/符号索引 | Tree-sitter Python bindings | MIT；[py-tree-sitter](https://github.com/tree-sitter/py-tree-sitter) | 复用增量解析和语法树；每种 grammar 单独核许可证，不能默认都 MIT | **优先适配** |
| Python 诊断 | Pyright | MIT；[Pyright](https://github.com/microsoft/pyright) | 复用类型诊断/LSP 能力；不引入 Pylance，不把分析器直接变成写盘工具 | **优先适配** |
| LSP 协议层 | `pygls` + `lsprotocol`，或直接实现最小 JSON-RPC client | pygls/lsprotocol：以发布包和仓库许可证为准 | 优先复用协议模型；服务器生命周期、workspace root、缓存和取消仍由本项目控制 | **适配候选** |
| 本地文本搜索 | ripgrep（当前 `grep` 已优先调用） | MIT/Unlicense 双许可证；[ripgrep](https://github.com/BurntSushi/ripgrep) | 继续作为外部进程使用；输出解析和路径边界由现有 `grep` 负责 | **保持现状** |

这里的“直接依赖候选”仍须经过安装、Python 版本和最小回归验证。本仓库当前 `.venv` 是 Python 3.14，根目录 `requirements.txt` 只声明运行时基础包；新依赖应优先放到可选 extras/专用依赖组，不能因为本地能安装就默认强制所有用户安装。

### 2.2 只借鉴设计，不嵌入整套 runtime

下列成熟 Agent 项目适合作为工具契约、终端交互、patch 工作流、sandbox 和编排的参考实现。它们的仓库许可证允许阅读和按许可证条件复用相应源码，但本项目不直接嵌入完整 Agent runtime、UI、模型路由或云服务：

| 项目 | 官方根许可证（核验入口） | 借鉴内容 | 不直接引入的原因 |
|------|--------------------------|----------|------------------|
| OpenHands | MIT；[LICENSE](https://github.com/OpenHands/OpenHands/blob/main/LICENSE) | runtime/sandbox、事件和 agent 工具边界 | 运行时、Docker/E2B、服务依赖和产品状态模型远大于本项目需求 |
| Aider | Apache-2.0；[LICENSE.txt](https://github.com/Aider-AI/aider/blob/main/LICENSE.txt) | repo map、编辑/提交循环、终端式交互 | 其 CLI、模型适配和 repo map 体系与当前 session/project 架构不同 |
| Continue | Apache-2.0；[LICENSE](https://github.com/continuedev/continue/blob/main/LICENSE) | IDE 扩展协议、上下文 provider、模型/工具分层 | VS Code/JetBrains 扩展壳和完整 provider 生态不属于本项目底层工具层 |
| Cline | Apache-2.0；[LICENSE](https://github.com/cline/cline/blob/main/LICENSE) | 计划、工具调用、审批和浏览器/终端 UX | 扩展 UI、代理循环和外部集成不应绕过现有 executor/confirm 管线 |
| Goose | Apache-2.0；[LICENSE](https://github.com/aaif-goose/goose/blob/main/LICENSE) | MCP/extension 工具发现、会话和 provider 组织 | 直接引入会重复本项目 registry、prompt overlay 和权限模型 |
| SWE-agent | MIT；[LICENSE](https://github.com/SWE-agent/SWE-agent/blob/main/LICENSE) | issue-to-patch 轨迹、测试反馈和 benchmark 评估 | 面向 benchmark 的 agent loop 不等于本项目的交互式项目工作流 |

“根许可证”不代表仓库内每个目录和每个依赖都使用同一许可证。若复制源码而不是仅调用外部命令/库，必须保留版权和许可证文本、检查 NOTICE/商标限制，并为修改文件保留变更声明；正式发布前还要生成依赖清单和许可证报告。Git CLI 作为用户环境中的外部进程调用，不等于把 Git 源码链接进本项目；如果以后随安装包捆绑 Git，必须另行遵守 Git 及其组件的 GPLv2/NOTICE 等分发要求。

### 2.3 暂不采用或不作为默认依赖

| 方案 | 原因 | 替代 |
|------|------|------|
| 直接嵌入 OpenHands/Aider/Continue/Cline/Goose/SWE-agent 全套 | 重复 runtime、状态、UI、权限和模型适配；升级面及供应链面过大 | 借鉴局部契约，保留本项目 executor/registry/session |
| 用完整编辑器库替代 `patch_file` | 编辑器状态模型和本项目原子落盘、计划域、ledger 不同 | `unidiff`/Tree-sitter 做窄适配，写盘继续走 `patch_file` |
| 用 pygit2/libgit2 立即替代 Git CLI | 许可证、二进制打包和行为差异需要单独评估；当前 Git CLI 已满足主要需求 | 继续调用 Git CLI；需要纯 Python 时另评估 Dulwich |
| 把 Pylance 当作开源 LSP | Pylance 不应作为开源依赖或默认分发组件 | Pyright；其它语言使用对应开源 LSP |
| 未核验许可证的 grammar、浏览器插件、下载二进制 | 许可证和运行时资产可能与主库不同 | 引入前逐项登记 SPDX、来源、版本、校验和 |

### 2.4 引入门与供应链记录

每个新组件在进入 `requirements.txt`、npm 依赖或发布包前，必须在任务或 `VENDOR.md` 记录：

1. 官方仓库、发布包名、固定版本和来源 URL；
2. SPDX 许可证、版权/NOTICE 要求、是否包含额外 runtime 或 grammar；
3. 传递依赖许可证和已知安全公告检查结果；
4. Python 3.14、Windows、Desktop、Terminal 和无该可选依赖时的退化行为；
5. 最小 smoke test、超时/取消/资源上限和卸载回退方案。

依赖缺失不能让工具伪装成可用：工具应返回 `unsupported` 或 `blocked` 及安装/启用提示。安装依赖本身仍经过现有确认管线，不能由工具借机扩大网络或宿主目录权限。只有完成上述记录和回归后，工具才能从 `planned` 变成 `experimental`，再从 `experimental` 变成 `active`。

### 2.5 开源复用后的落地顺序

```text
pywinpty/ConPTY 适配交互终端
    → unidiff 适配 patch_file v2
    → Git CLI 保持现状并补 diff/restore 契约
    → JUnit XML/pytest-json-report 适配 structured_test
    → Pyright + Tree-sitter 适配 diagnostics/symbol
    → Playwright 适配 browser_inspect
```

OpenHands/Aider/Continue/Cline/Goose/SWE-agent 的调研结论只作为每一阶段的设计输入，不改变这条实现顺序，也不改变“默认单项目单活线、单一 executor 权限入口”的约束。

## 2. 缺口总表

| 优先级 | 能力 | 建议工具/契约 | 价值 | 状态 |
|--------|------|---------------|------|------|
| P0 | LLM 交互终端 | `interactive_terminal` | 支持 stdin、实时输出、Ctrl-C、attach/resume | **active**；长期存活的 agent/server 宿主托管 worker；pywinpty 缺失时明确 unsupported |
| P0 | 结构化原子编辑 | `patch_file` v2 契约 | 多 hunk、上下文校验、dry-run、失败不落盘 | **experimental**；结构化唯一锚点已实现，统一 diff 解析待后续适配 |
| P0 | 一等 Git 变更与回退 | `git_snapshot` diff 扩展 · `git_restore` | 逐文件/hunk 检查，精确恢复 | **active**；文件级切片已完成，hunk 级恢复待做 |
| P0 | 统一测试结果协议 | `structured_test` protocol | 按文件/测试/失败项重跑并解析结果 | **active**；适配层已完成，优先复用现有工具输出 |
| P1 | 语言服务原语 | `diagnostics` · `symbol` | 定义、引用、符号、诊断、重命名 | 缺口 |
| P1 | 页面自动验收 | `browser_inspect` | DOM、截图、console/network、断言 | 缺口；F2 defer |
| P1 | 隔离并行工作区 | worktree contract（不新增默认并行入口） | 显式隔离后的探索、审查、合并和冲突检测 | 缺口；受单活线约束 |
| P1 | 变更审查 | `deliverable_review` diff profile / `change_review` | 按 hunk 审查风险、测试和接受/拒绝 | 部分已有；落地形态待定 |
| P2 | 代码托管与 CI | GitHub/GitLab/CI connector | issue、PR、review、CI 回链 | 缺口 |
| P2 | 容器与数据库入口 | container/db adapters | 日志、exec、迁移、schema、查询 | 缺口 |
| P2 | 上下文预算管理 | loader/session context protocol | 按符号/错误收集上下文并避免重复读取 | 基础能力已有，规划器缺口 |

## 3. P0：日常编码闭环

### 3.1 LLM 交互终端 `interactive_terminal`

**现状**：代码已有 `create_terminal_session()` / `resume_terminal_session()`，它们属于 Terminal harness 的会话管理，不是 LLM 可调用的 PTY 工具。`run_command` 适合一次性命令，`run_service` 适合登记长驻进程；当前缺的是面向 LLM 的交互式 stdin/stdout 工具。

**目标**：提供有限而可靠的会话原语，而不是做完整 xterm 产品。

- 创建、写入 stdin、读取增量 stdout/stderr、发送 Ctrl-C；
- 会话状态、退出码、最近输出和启动命令可查询；
- 支持 attach/resume，终端 UI 和桌面可消费同一状态；
- 会话超时、用户取消、进程自然退出均有明确原因；
- 写入、网络、安装依赖和宿主机范围仍沿用现有确认/权限管线。

**命名与落地决策**：本阶段采用独立 evolved 工具 `interactive_terminal`，不新增 flat proxy；通过 `run_evolved` 的工具注册链路发现，当前 `status=active`，进入对应主题的默认工具清单。它与现有 Terminal harness 的 `terminal_session` 概念分开。

**边界**：不做远程多机、不做无边界系统 shell、不把所有 `run_command` 自动升级成长驻会话。

**实现契约**：Windows 下的 Ctrl-C 必须定义为对目标会话发送中断信号，不得依赖杀掉宿主 Agent 进程；实现需明确 PTY 与 pipe 的适用场景、stdin EOF、UTF-8/本地编码转换、并发读写顺序和 session lock。会话必须有最大数量、空闲/总时长上限、输出保留上限、孤儿进程清理和重启后的恢复策略；恢复失败要返回 `orphaned` 或 `lost`，不能伪装成仍在运行。

**验收**：启动交互命令后能写 stdin；实时收到输出；Ctrl-C 后能得到稳定退出状态；断开再 attach 不丢失会话；超过上限、关闭 stdin、进程崩溃和服务重启都有可解释结果；狂奔暂停时可停止会话并说明原因。

**当前实现切片（IT-6201）**：会话目录位于 `data/interactive-terminals/<session_id>/`，worker 通过 request/response JSON 文件与工具调用通信，输出保留在有上限的合并 PTY 流日志中。为保证跨调用 attach/resume，`run_evolved` 在长期存活的 agent/server 进程内直接托管该工具，worker 不再挂在短命 evolved wrapper 下；直接执行 `main.py` 或一次性 CLI 进程不能提供跨进程持久保证。`read` 使用逻辑字节游标；日志淘汰后返回 `cursor_reset`。PTY 的潜在阻塞读取由 reader 线程隔离，主 worker 仍可处理 `input`、`signal` 和 `close`。请求在 session lock 内分配单调序号，按序处理。Windows 输入将 LF 规范化为 CRLF；`signal=eof` 发送 Ctrl-D 输入约定，具体程序是否将其解释为 EOF 由目标控制台程序决定。缺少 `pywinpty` 时状态为 `unsupported`，不会伪装成可用会话。worker 消失后，`status/list` 会尝试按已记录的 PTY PID 清理进程树，并将清理结果写入状态；状态读写对 Windows 文件占用做了重试和串行化。

当前仍未完成：ConPTY/版本兼容矩阵、服务重启后的主动恢复，以及 Terminal/Desktop 专用消费端接入。桌面端的终端会话可见性设计见 [INTERACTIVE-TERMINAL-UI.md](./INTERACTIVE-TERMINAL-UI.md)，实现仍是后续 T-6225。孤儿清理目前是 best-effort，不能替代带进程身份校验的完整恢复方案；宿主进程不可用时返回 `lost`，不伪装成持久会话。

### 3.2 结构化原子编辑 `patch_file` v2

**现状**：已有 `write_text` 和 `patch_file`，但 Agent 主工具面缺一个统一的原子编辑契约，尤其是多 hunk、版本冲突和失败恢复。这里的 v2 首选是扩展 `patch_file`，不是再增加第四个 flat proxy。

**目标**：保留现有 `patch_file` 调用兼容性，收敛底层编辑行为；旧的 `replacement` / 唯一锚点输入继续可用，新能力通过可选字段增加。

- 接受统一 diff 或结构化 hunks；
- 校验目标文件存在、上下文和可选 base hash；
- 支持 dry-run 和预览；
- 任一 hunk 失败时不落盘，返回失败 hunk、文件和上下文摘要；
- 成功结果返回修改文件、行数和可供审查的 patch id；
- 为后续撤销、审查和狂奔重试提供稳定引用。

**边界**：不做多文件事务提交，不做完整 IDE diff 编辑器；多文件操作先逐文件原子化并返回整体结果。计划域文件仍只能由 `plan_partner` 产生提案并采纳。

**验收**：正常 patch、多 hunk patch、上下文漂移、base hash 冲突、dry-run 和路径越界都有可预测结果；失败场景检查磁盘内容保持不变。

**当前实现切片（IT-6202）**：保留旧的 `start_line`/`end_line`/`find`/`replacement` 调用；新增 `hunks`（以及兼容别名 `replacements`），每个 hunk 使用唯一 `find` 锚点并在内存中按顺序应用。目标文件读取后可用 SHA-256 `base_hash` 做冲突检查；成功写盘返回 `base_hash`、`result_hash` 和 `patch_id`，并向 `data/patch-ledger.jsonl` 追加记录。显式或自动 `patch_id` 的重复请求在文件哈希仍为上次结果时幂等跳过；按目标文件锁串行化读、写和 ledger 记录。`dry_run` 只计算结果，不写文件和 ledger；二进制、非 UTF-8、超限结果和任一失败 hunk 均拒绝。

本切片尚未接入 `python-unidiff`，也没有跨文件事务：只保证单文件原子落盘，后续多文件调用仍由上层逐文件处理。写盘成功但 ledger 持久化失败会明确返回错误，文件可能已经改变，调用方必须重新读取并核对 hash；这也是保持 `experimental` 的原因。

**实现契约**：多文件请求按“每个文件原子化、整体结果可部分成功”实现，是否提供跨文件事务必须另行决策，不能暗示自动回滚所有文件；LF/CRLF 规范化沿用现有写盘约定，不得因 patch 放大换行。二进制文件默认拒绝。相同 `patch_id` 的重试必须可识别并避免重复应用；`patch_id` 需持久化到可审计日志。`base_hash` 是文件内容/版本冲突校验，不能与 `plan_patch` 的提案基线或采纳队列状态混为一谈。

### 3.3 一等 Git 变更与回退 `git_snapshot` diff / `git_restore`

**现状**：已经能做快照、分支、commit、push，但 `git_snapshot` 目前主要返回状态和 diff stat；“我改了什么”和“只撤销这一块”还没有清晰的一等工作流。

**目标**：围绕 Agent 频繁的检查、修复和回退提供窄而安全的 Git 原语。

- 工作树、staged、某个 commit 之间的 diff；
- 逐文件和 hunk 级摘要，包含二进制/未跟踪文件提示；
- 恢复指定文件或指定 hunk，默认不触碰其他改动；
- 恢复前 dry-run 和明确的影响范围；
- 永远拒绝隐式丢弃未确认的用户改动；用户改动与 Agent 改动的归属必须由 baseline/ledger 支撑，不能声称 Git 原生知道编辑者。

**边界**：不做 force checkout、交互式 rebase、改 Git 配置和隐式清理。

**验收**：用户已有改动与 Agent 改动可区分；单文件/单 hunk 恢复不影响旁邻改动；状态、diff 和恢复结果可被日志复现。

**变更归属契约**：每个回合开始记录 project root 的 baseline（文件指纹、Git HEAD、工作树状态）；Agent 每次成功写入追加 patch ledger，至少记录回合/调用 ID、路径、旧新指纹、patch id 和来源。baseline 不是永久真相：若用户在回合中编辑文件、外部工具改动、回退或 rebase，下一次 diff 前必须重新采样并把无法归属的部分标为 `unknown`/`mixed`，暂停自动 restore。只有 ledger 能证明归属时才允许按 Agent hunk 局部恢复；Git 本身不提供编辑者身份。

**当前实现切片（IT-6203）**：`git_snapshot` 保持只读，返回工作树/staged 的状态、diff stat、未跟踪路径，并可按路径返回有上限的完整 diff；可选 `base_ref` 用于工作树 diff。`git_restore` 当前按单个已跟踪文件恢复 `worktree` 或 `staged` 状态，默认 dry-run，真实执行必须提供当前文件 SHA-256 `expected_hash`；已删除的 tracked 文件用 `expected_hash="deleted"` 表示当前状态。路径越界、哈希漂移和未跟踪文件均拒绝；不支持 force checkout、主动删除文件或 hunk 级选择。Git CLI 是系统依赖，不额外引入 Python Git 库；当前回归覆盖临时仓库中的 staged/worktree、已删除 tracked 文件、路径过滤、未跟踪路径、dry-run 和冲突保护。

### 3.4 统一结构化测试结果协议 `structured_test`

**现状**：已有 `run_tests`、`run_project_tests` 和 `run_quality`，但不同入口的输出字段、失败解析和重跑能力需要统一。`structured_test` 首先是结果协议/适配层，不默认新建一个同义工具。

**目标**：让主 Agent、狂奔和 Desktop 消费同一种测试结果。

统一返回至少包含：

```json
{
  "status": "passed|failed|blocked|timeout",
  "exit_code": 0,
  "duration_ms": 0,
  "failed": [{"name": "...", "file": "...", "line": 0, "message": "..."}],
  "stdout_tail": "...",
  "stderr_tail": "...",
  "rerun": {"file": "...", "test": "..."}
}
```

`exit_code` 在未启动、超时或被取消时可以为 `null`；`status=passed` 才要求 `exit_code=0`。`rerun` 在没有可靠定位时应为 `null`，不能凭空生成测试名。

**当前实现切片（IT-6204）**：`agent-core/structured_test.py` 将现有 `run_project_tests` 结果归一化为 `status`、`exit_code`、`duration_ms`、`failed`、`stdout_tail`、`stderr_tail` 和 `rerun`。测试通过要求退出码为 0 且没有结构化失败；命令超时返回 `timeout`；缺少测试命令/模块返回 `blocked` 和 `missing_dependency`；dry-run 返回 `blocked/dry_run`，不伪装成通过。适配器保留 `suite`、`working_dir`、`command`、`summary` 等上下文，不负责执行第二套测试引擎，也不会在无法可靠定位时生成重跑测试名。

**验收**：可按项目、测试文件、测试名和失败项重跑；常见 Python/JS 测试输出能解析 `file:line`；退出码、超时和缺依赖不会被误报为通过；狂奔能据此选择修复、重试或暂停。

**狂奔边界**：测试适配器返回结构化事实；acceptance/controller 根据 `status`、失败项、重试计数和证据推进 checklist。prompt 只说明模型如何解释失败及下一步，不能由动态 overlay 或 assistant 自述直接推进任务，`turn_end` 也不能以口述结果代替工具结果。

## 4. P1：扩大定位和验证能力

### 4.1 语言服务与诊断

接入语言服务器或等价的本地分析器，优先提供 Agent 原语：跳转定义、查找引用、符号搜索、类型/编译诊断和受约束的重命名。

这不是转向完整 IDE。第一阶段只要求结构化结果：符号名、文件、行列、关系和诊断等级。没有可用语言服务器时应明确返回“不支持该语言”，不能退化成不可靠的文本猜测。

**实现契约**：明确 LSP/分析器发现、启动、版本和缓存策略；workspace root 必须绑定当前 project root，不能把宿主目录作为工作区。启动失败、缺少依赖、初始化超时和语言不支持都返回可区分的 `unsupported`/`blocked`，诊断与符号查询默认只读。重命名只能产出经 base hash 校验的 `patch_file` 提案，复用现有写入、计划门和确认管线，不能由 symbol 工具直接写盘。

### 4.2 浏览器页面检查 `browser_inspect`

**现状**：`browser_open` 负责把页面交给人看，`http_request` 负责探活和读响应；`BROWSER.md` 已将无头浏览器列为 F2 defer。

**目标**：在可选依赖存在时接入 Playwright 或等价能力：

- 打开本地页面并等待稳定；
- DOM 查询、点击、输入和页面断言；
- 截图；
- console 和 network 日志；
- 返回失败元素、选择器、截图路径和页面 URL。

**边界**：外部网站、登录态、真实发布和敏感数据仍需确认；不把浏览器自动化变成新的通用脚本执行器。

**实现契约**：需定义 browser/page/context 的生命周期和 cookie/profile 隔离；截图、trace、console/network 日志要有稳定保存目录和保留策略。默认只允许当前项目的 loopback 地址，外部网络使用 allowlist；selector、导航和下载/上传都必须有超时与大小上限。会话结束或失败时清理敏感页面状态，不把凭据、cookie 或下载内容无界回灌给模型。

### 4.3 并行代理与隔离工作区

已有 `explore` 和 `deliverable_review`，但要逼近成熟 Agent 工具体验，还需要把并行执行的边界做成产品契约。当前项目的默认模型是**单项目、单活线**：

- 同一回合允许并行只读工具调用，但不自动创建多个活跃 Agent 线或多个 worktree；
- 每个只读任务有明确目标、输入快照和输出类型，结果是报告/提案，不是已写入；
- 只有用户显式开启隔离 worktree，并为写型子任务指定范围时，才允许子任务写盘；
- 汇总时提供变更列表、冲突列表和可采纳结果，未采纳结果不污染主工作区；
- 狂奔默认仍只有一条活跃写入线；隔离并行属于未来显式 opt-in 能力，不能在普通狂奔中隐式开启。

### 4.4 变更审查 `change_review`

`deliverable_review` 解决交付物级审查，但还需要一个面向实际 patch 的轻量审查入口：按文件/hunk 展示变更意图、风险、受影响测试和待确认项，并允许接受、拒绝或要求修正。

其结果应与 `git_snapshot` 的 diff 扩展、`patch_file` v2 和项目 VERIFY 证据关联，不另造一套孤立的审查状态。

## 5. P2：生态与上下文

### 5.1 GitHub / GitLab / CI

优先只做只读和回链：issue、PR、review comment、CI 状态、失败日志和 commit 关联。创建 PR、写评论、重跑 CI 等外部写操作必须沿用确认管线。

### 5.2 容器与数据库

提供统一适配层读取容器日志、执行受限命令、查看数据库 schema、迁移状态和查询结果。数据库写操作、迁移和容器生命周期必须有明确范围和确认。

### 5.3 上下文管理

把当前 digest/session 机制继续收敛为 Agent 可用的上下文规划：根据错误、符号和任务自动收集最小相关文件，显示预算，避免重复 `read_file`，并能在压缩后保留当前任务、失败证据和未提交变更。

## 6. 推荐实施顺序

```text
interactive_terminal
    → patch_file v2
    → git_snapshot diff / git_restore
    → structured_test protocol
    → diagnostics / symbol
    → browser_inspect
    → 隔离并行 / change_review
    → CI、容器数据库、上下文预算
```

排序依据是日常闭环的依赖关系：没有可恢复的终端和编辑，狂奔只能“自动重复”；没有 diff 和结构化测试，自动修复无法稳定判断边界；语言服务和浏览器检查则分别扩大代码定位与 UI 验收范围。

## 7. 与狂奔模式的关系

狂奔当前已经能覆盖：

- 一句授权后的项目内连续任务推进；
- 计划、任务、测试、修复、验证和 release_wait 的阶段流转；
- checklist、续接、失败升级和高风险暂停；
- 通过现有 `run_command`、`run_project_tests`、`run_quality` 等工具完成真实项目回合。

它当前的主要体验上限是：

1. LLM-facing 交互式命令工具无法像真正终端一样持续输入和中断；Terminal harness 自身已有会话恢复，但不等于 LLM 工具契约；
2. patch 冲突时缺少统一的原子编辑和重试标识；
3. 用户难以快速查看 Agent 与自己改动的精确差异并局部回退，且当前没有可靠的变更归属 ledger；
4. 测试结果虽然已有结构化入口，但跨工具协议尚未完全统一；
5. 页面和语言级验证还主要依赖文本、HTTP 或人工浏览器。

因此，狂奔不应继续叠加更多阶段状态来掩盖这些问题。优先补底层原语，再让狂奔消费这些原语的稳定结果。

## 8. 系统提示词与注入链路

### 8.1 当前实际装配顺序

当前实现由 `agent-core/loader.py::build_system_prompt()` 组装 system。以下是 `include_overlay=True` 时的实际 section 顺序；`include_overlay=False` 只从前五个基础 section 候选中组装，不加载 safety、topic prompt、工具目录、项目 overlay 或 digest，最终非空结果以 `LoadedSystem.section_names` 为准。文档和实现必须以这个顺序为基线，不在某个 prompt 文件里假设另一个文件一定已经注入：

```text
静态段
  1. agent-core/prompts/core.txt（终端 harness 则是 terminal prompt）
  2. [主题索引]              ← evolve/_index.*.toml
  3. [记忆索引]              ← evolve/memories/
  4. [builtin 摘要]
  5. [host scope]

动态段（include_overlay=true）
  6. [会话]
  7. [终端 scope]            ← terminal harness 才有
  8. [turn discipline]
  9. [safety]                ← evolve/prompts/safety.md，overlay 开启时加载
 10. [topic_prompt:<id>]     ← 已确认主题的 prompt
 11. [tool_workshop]         ← 满足条件的普通会话
 12. [evolved_catalog]        ← tool-catalog/INDEX.md + format_capability_hints()
 13. [scaffold_tool]          ← 本轮创建 evolved 工具时
 14. [evolve_escalation]
 15. [subagent_summary]
 16. [project_prompt]         ← 项目绑定时
 17. [project_mode]           ← 阶段、计划门、狂奔、开放任务和验证出口
 18. [digest]                 ← 压缩摘要
```

源码锚点：

| 责任 | 当前落点 | 允许承载的内容 |
|------|----------|----------------|
| 硬边界与不可伪造 | `agent-core/prompts/core.txt` | 身份、工具边界、禁止假装、路径大原则、模式原则 |
| 工具怎么选 | `evolve/tool-catalog/INDEX.md`、`buckets/*.md` | 工具名称、适用场景、参数摘要、替代关系 |
| 通用安全 | `evolve/prompts/safety.md` | 秘密、网络、宿主目录、狂奔授权的硬边界 |
| 语言/开发习惯 | `evolve/prompts/coding.md`、`workflow.md`、`data.md` | 任务分流、工具组合、项目开发习惯 |
| 项目计划与阶段 | `project-boundaries.md`、`project-delivery-*.md` | 计划门、产物范围、阶段权限、验证出口 |
| 每轮状态 | `loader.py`、`project_mode.py`、`runaway_v2/` | 当前项目、任务、失败、预算、狂奔 checkpoint、digest 修正 |
| 真正强制 | `executor.py`、策略模块、工具实现 | confirm、WRITE-SCOPE、schema、超时、失败预算、工具可见性 |

### 8.2 提示词分层纪律

1. **代码强制的规则不只写在 prompt 里**。confirm、路径、计划门、工具隐藏、失败预算和外部写操作必须由 executor/代码执行；prompt 只负责让模型选对工具和理解结果。
2. **`core.txt` 不变成工具说明书**。它只保留稳定边界；详细参数放 `INDEX`、bucket 和函数 schema。
3. **静态 prompt 不写动态事实**。当前任务、当前阶段、失败次数、是否狂奔由动态 overlay 提供，不写死在 `coding.md`。
4. **不要重复注入同一条规则**。同一规则最多有一个强制实现点、一个选择提示点、一个用户可见结果点。
5. **明确主路径和退化路径**。例如 `browser_open` 是现有人工查看入口，`browser_inspect` 只有实现后才能出现在主荐工具列表；不存在的工具不能提前写进“可调用工具”。
6. **每条新提示词都必须声明范围**：agent root 还是 project root、只读还是写入、是否 confirm、狂奔是否覆盖、失败时下一步是什么。

### 8.3 执行前后的提示词状态

Phase 61 在实现前允许写“设计提案”，但不能提前修改运行时 prompt 让模型调用不存在的工具。每项能力分三档：

| 阶段 | `core.txt` / prompt | INDEX / bucket | schema / loader | 状态 |
|------|---------------------|----------------|-----------------|------|
| 设计中 | 只写“当前缺口/暂不可用”说明 | 可写“规划项”但标 `planned` | 不暴露 function | 本文当前状态 |
| 已实现但未默认 | 写清启用条件和退化路径 | 标 `experimental` | 受 feature flag 控制 | 需单独验收 |
| 已稳定 | 主路径文案、替代关系、失败动作 | `active` 且可发现 | 默认暴露并有回归 | 才能标 done |

不得出现以下漂移：

- bucket 写着 `interactive_terminal`，但 schema 没有该工具；
- `core.txt` 要求使用尚未注册的 patch 工具，实际设计却选择扩展 `patch_file`；
- 狂奔 prompt 宣称“所有项目内操作免确认”，但 executor 仍会暂停外部写入、敏感文件和发布；
- `structured_test` 返回失败，prompt 却教模型根据文字猜测“可能通过”；
- `browser_inspect` 尚未安装时仍把它列为默认验收工具。

## 9. P0 提示词变更矩阵

以下是实现 P0 时需要落盘的具体 prompt 改动。代码、schema 和文案必须同一批提交；只改文案不算完成。

### 9.1 LLM 交互终端 `interactive_terminal`

| 文件/位置 | 改动 | 建议文案或契约 |
|-----------|------|----------------|
| `agent-core/prompts/core.txt` · Execution boundary 表 | 工具实现后增加一行 | `交互式命令 / stdin / Ctrl-C → interactive_terminal（或 run_command session action）；一次性命令 → run_command；长驻服务 → run_service。` |
| `agent-core/prompts/core.txt` · Tool discipline | 增加选择规则，不写参数教程 | `不要用 run_command 反复轮询交互式进程；需要持续输入、实时输出或中断时使用交互终端会话。` |
| `evolve/tool-catalog/INDEX.md` | run 桶增加主路径 | `交互终端：需要 stdin、实时输出、Ctrl-C、attach/resume → buckets/run.md` |
| `evolve/tool-catalog/buckets/run.md` | 增加工具表和替代关系 | `interactive_terminal（若启用）= LLM 可交互会话；run_command = 一次性命令；run_service = 托管长驻服务。` |
| `evolve/prompts/coding.md` · 项目用户场景 | 增加开发分流 | `交互式安装器、REPL 或需要输入的命令用交互终端；build/test 用 run_command；dev server 用 run_service。` |
| `evolve/prompts/project-boundaries.md` · 构建/测前端纪律 | 补 cwd 和会话归属 | `交互终端必须绑定 project_root；不得把交互会话作为绕过阶段权限的入口。` |
| `evolve/prompts/safety.md` | 保留权限边界 | `interactive_terminal 或 run_command session 不扩大 WRITE-SCOPE；宿主目录、网络、秘密、删除和发布仍暂停。` |
| `loader.py::format_capability_hints()` | 只增加一条短选择提示 | `- 交互执行：交互终端会话；一次性命令 run_command；长驻 run_service。` |
| `tool_proxies.py` / `agent.py` | 先完成兼容性评估再暴露 | 优先扩展现有 `run_command` schema；若独立暴露 `interactive_terminal`，必须走 `run_evolved`/feature flag，不得无评审突破 3 个 flat proxy。 |

**不改**：不在 `coding.md` 写 PTY 实现细节；不把交互终端当成所有命令的默认替代；不在 `core.txt` 写 Windows shell 教程。

**终端结果回灌文案**：工具结果必须告诉模型 `session_id`、`state`、增量输出、`exit_code`、`signal` 和下一可用动作。退出后不要再提示“继续读取日志”；需要继续输入就再次调用同一 `session_id`。该 `session_id` 是 LLM 工具会话 ID，与现有 Terminal harness 的 `conversation_id` 分开定义。

### 9.2 `patch_file` v2

| 文件/位置 | 改动 | 建议文案或契约 |
|-----------|------|----------------|
| `agent-core/prompts/core.txt` · Tool discipline | 明确编辑选择 | `改已有文本：小范围唯一锚点可用 patch_file；多 hunk、需要上下文/base hash 或希望预览时仍用 patch_file 的原子模式；新文件用 write_text。` |
| `evolve/tool-catalog/INDEX.md` | write 桶增加分流 | `写/改文件：write_text（新建）· patch_file（小修或原子多 hunk）。` |
| `evolve/tool-catalog/buckets/write.md` | 增加原子编辑表 | 必须写清 `dry_run`、`base_hash`、hunk 失败不落盘、`patch_id`、路径相对 agent root。 |
| `evolve/prompts/coding.md` | 让模型先读再改 | `先 read_file/grep 确认当前内容；不要凭旧上下文覆盖；patch 失败时重新读取目标文件，不要盲目重复同一 patch。` |
| `evolve/prompts/project-boundaries.md` · 路径 | 保持 project root 限制 | `patch_file v2 只能修改当前 project_root 内已允许的文件；计划域文件仍走 plan_partner。` |
| `evolve/prompts/safety.md` | 保留敏感文件暂停 | `patch_file v2 不改变敏感文件、秘密文件、删除和外部写操作的确认要求。` |
| `loader.py` | 只在契约 active 后更新短提示 | 将 `patch_file（改已有）` 扩成 `patch_file（小修/原子多 hunk）`。 |
| `tool_proxies.py` | 不默认新增 proxy | 若评审后仍需 flat function，必须同步更新 AGENT-HARNESS 的“最多 3 个 proxy”约束、工具总数和 IT。 |

**推荐 schema**：在现有 `patch_file` 上增加可选 `hunks[]`、`base_hash`、`dry_run`、`on_conflict`。返回 `applied`、`patch_id`、`changed_files[]`、`failed_hunks[]`、`preview`。`replacement`、唯一锚点和既有行号参数继续兼容，但新 prompt 不应主推多行 `start_line/end_line` 覆盖。

**不改**：不在 prompt 里教模型手工计算行号；不让失败 patch 自动降级成整文件覆盖；不把 `patch_file v2` 用作 `TASKS.md`、`MAP.md`、`PROJECT.md`、`ENV.md` 的计划域写入口。

### 9.3 `git_snapshot` diff / `git_restore`

| 文件/位置 | 改动 | 建议文案或契约 |
|-----------|------|----------------|
| `agent-core/prompts/core.txt` · Execution boundary 表 | 增加只读/恢复分流 | `查看变更用 git_snapshot 的 diff 模式；局部撤销用 git_restore；不要用 shell 猜测或强制 checkout。` |
| `evolve/tool-catalog/buckets/run.md` | Git 工具表增加恢复行 | `git_snapshot：工作树/staged/commit 间差异；git_restore：dry-run 后恢复指定文件/hunk，默认须确认。` |
| `evolve/prompts/coding.md` | 修改交付前工作流 | `写入后先用 git_snapshot diff 检查范围，再跑对口测试；只撤销 Agent 变更时指定文件/hunk，不清理用户已有改动。` |
| `evolve/prompts/project-boundaries.md` | 增加 Git 边界 | `git_snapshot 只读；git_restore 不得绕过用户改动保护；commit/push 仍使用既有 git_commit/git_push。` |
| `evolve/prompts/safety.md` | 明确高风险动作 | `git_restore、commit、push、分支切换和删除默认不由狂奔隐式执行；restore 需要影响范围可解释。` |
| `loader.py::format_capability_hints()` | 增加一行选择提示 | `- 变更检查：git_snapshot diff；局部回退：git_restore；提交/推送仍单独确认。` |

**schema 重点**：`scope`（worktree/staged/commit）、`path`、`hunk_id`、`base_ref`、`dry_run`。工具返回 `requires_confirmation`、影响范围和内部调用 ID；executor/confirm pipeline 生成并管理确认卡，模型不负责生成确认凭据。返回必须区分 `untracked`、`user_modified`、`agent_modified`、`unknown`、`mixed` 和 `restored`，禁止只返回一段不可解析文本。

### 9.4 `structured_test`

| 文件/位置 | 改动 | 建议文案或契约 |
|-----------|------|----------------|
| `evolve/tool-catalog/buckets/run.md` | 把测试工具统一为结果协议 | `测试优先使用 structured_test / run_project_tests；run_command 只在没有结构化适配器或需要项目自定义命令时使用。` |
| `evolve/tool-catalog/buckets/project.md` | 项目测试和质量分工 | `structured_test = 统一结果协议；run_project_tests = 底层测试；run_quality = lint/quality；run_command = 自定义构建/脚本。` |
| `evolve/prompts/coding.md` | 增加验证顺序 | `改动后先跑最小对口测试，失败按 file:line 定位；修复后重跑失败项，再扩大到项目套件。` |
| `evolve/prompts/project-boundaries.md` · 构建纪律 | 增加退出状态规则 | `exit 0 才能视为通过；缺依赖、timeout、被取消和无结果均为 blocked/failed，不得口述为通过。` |
| `evolve/prompts/safety.md` | 保留失败诚实性 | `测试输出与工具结果冲突时，以结构化 exit/status 为准；不得根据日志尾部猜测成功。` |
| `loader.py` / `user_copy.py` | 动态失败提示只说明动作 | 失败时注入“读取失败项/重跑指定测试/修复后复验”，不要把完整 stderr 重复写进 core。 |

**狂奔专用动态 overlay**：当项目狂奔开启时，给 Harness 的结果只提供 `status`、失败项、重试计数、验证证据和暂停原因；acceptance/controller 负责依据这些结构化事实推进 checklist，prompt 只提供解释失败和选择下一步的规则；不要重复注入完整测试教程，也不能依据 assistant 自述推进 `turn_end`。

## 10. P1 提示词变更矩阵

### 10.1 `diagnostics` / `symbol`

| 文件/位置 | 改动 | 建议文案或契约 |
|-----------|------|----------------|
| `agent-core/prompts/core.txt` · Execution boundary 表 | 新 builtin/工具实现后增加 | `按符号定位用 symbol；类型、编译和 lint 问题用 diagnostics；grep 仅用于文本匹配。` |
| `evolve/tool-catalog/INDEX.md` | 新建或扩 `discover.md` | 按名 `glob_file_search`、按内容 `grep`、按语义 `codebase_search`、按符号 `symbol`、按诊断 `diagnostics`。 |
| `evolve/tool-catalog/buckets/discover.md` | 写清退化路径 | 无语言服务器时返回 unsupported，不把文本 grep 伪装成定义/引用结果。 |
| `evolve/prompts/coding.md` | 增加定位顺序 | `先 glob/grep 找候选；有语言服务时用 symbol/diagnostics 取得定义、引用和错误；再 read_file 最小上下文。` |
| `evolve/prompts/project-boundaries.md` | 保持项目范围 | 索引和诊断默认限于当前 project_root；不得因为分析工具读取宿主目录。 |
| `evolve/prompts/safety.md` | 保持只读默认 | 诊断、符号和索引默认只读；重命名属于写操作，必须复用编辑/计划门。 |

### 10.2 `browser_inspect`

| 文件/位置 | 改动 | 建议文案或契约 |
|-----------|------|----------------|
| `docs/BROWSER.md` | 将 F2 从 defer 变为设计/实现分片 | 保留 `browser_open` 人工查看和 `http_request` 探活；新增 DOM/截图/console/network/断言契约。 |
| `evolve/tool-catalog/buckets/run.md` | 增加分流 | `http_request = API/探活；browser_open = 给人看；browser_inspect = 自动页面验收。` |
| `evolve/prompts/coding.md` | 增加前端验证顺序 | `起服用 run_service，探活用 http_request，需要页面行为证据才用 browser_inspect，最后可 browser_open 给人复核。` |
| `evolve/prompts/project-boundaries.md` | 增加本地页面范围 | 默认仅当前项目启动的 loopback；外网、登录态、上传、发布和真实用户数据必须暂停。 |
| `evolve/prompts/safety.md` | 明确浏览器风险 | 不把 `browser_inspect` 当任意 JS/shell 执行器；外部页面和敏感凭据不自动操作。 |
| `loader.py::format_capability_hints()` | 只增加三路选择 | `页面：http_request 探活 / browser_open 人看 / browser_inspect 自动断言（启用后）。` |

### 10.3 并行隔离与 `change_review`

| 文件/位置 | 改动 | 建议文案或契约 |
|-----------|------|----------------|
| `agent-core/prompts/core.txt` · Turn discipline | 增加并行规则 | `默认单项目单活线；同回合只读调用可并行；写型子任务只有在用户显式开启隔离 workspace/worktree 后才可执行，父 Agent 汇总前不得声称主工作区已修改。` |
| `evolve/prompts/coding.md` | 增加父子任务分工 | `explore 负责只读发现；deliverable_review 负责只读审查；写操作由父 Agent 或显式隔离工作区执行。` |
| `evolve/prompts/project-boundaries.md` | 增加采纳边界 | 子代理结果是提案/报告，不是已写入；冲突、未采纳 patch 和主工作区污染必须显式返回；默认不创建多个活跃 worktree。 |
| `evolve/tool-catalog/buckets/project.md` | 增加 change_review | `deliverable_review = 交付级审查；change_review = patch/hunk 级审查；二者都不自动改代码。` |
| `evolve/prompts/safety.md` | 保留外部写边界 | 子代理不得借隔离工作区绕过 confirm、WRITE-SCOPE、Git push 或宿主目录限制；隔离 worktree 仅在用户显式开启后可用。 |

## 11. P2 提示词变更矩阵

P2 不进入首批实现，但文档必须预先规定注入方式，避免以后把第三方 API 教程灌进 `core.txt`：

| 能力 | 进入哪一层 | 不应进入哪一层 | 必须声明 |
|------|------------|----------------|----------|
| GitHub/GitLab/CI | `buckets/integrations.md` + `coding.md` 短分流 | `core.txt` 长教程 | token、仓库范围、只读/外部写、CI 状态来源 |
| 容器 | `buckets/run.md` 或 `container.md` | `core.txt` | container id、命令范围、日志上限、生命周期确认 |
| 数据库 | `buckets/data.md` + `project.md` | `core.txt` | schema/只读查询/迁移/写入确认、结果截断 |
| 上下文预算 | `loader.py` 动态 overlay + `AGENT-HARNESS.md` | topic prompt 长篇重复 | token 预算、已读文件、失败证据、digest 优先级 |

## 12. 各层应如何改：最终清单

### 12.1 `agent-core/prompts/core.txt`

只做以下四类修改：

1. Execution boundary 表增加已实现且稳定的工具分流；
2. Tool discipline 增加“什么时候选哪个原语”的短规则；
3. Turn discipline 增加并行写隔离和失败后重新读取的原则；
4. 对计划域、用户改动、工具真实结果的硬边界保持不变。

禁止：把所有 schema 参数、Playwright 操作教程、Git 命令教程、Windows shell 语法、狂奔动态状态复制进来。

### 12.2 `evolve/tool-catalog/INDEX.md` 与 buckets

INDEX 只保留一屏可读的 L0 路由，推荐最终增加这些行：

```markdown
| 发现 | 按名 glob_file_search · 按内容 grep · 按语义 codebase_search · 按符号 symbol/diagnostics | buckets/discover.md |
| 写/改 | write_text（新建）· patch_file（小修/原子多 hunk） | buckets/write.md |
| 执行 | interactive_terminal（若启用，交互）· run_command（一次性/可扩展会话）· run_service（长驻） | buckets/run.md |
| 验证 | structured_test · run_project_tests · run_quality · browser_inspect | buckets/run.md / project.md |
| 变更 | git_snapshot（含 diff）· git_restore · git_commit · git_push | buckets/run.md |
```

bucket 必须提供：用途、最小 schema、返回字段、confirm、路径范围、狂奔行为、失败后的下一步、已归档替代品。工具未 active 前只写在本路线图和任务台账，不能提前写进默认 INDEX。

### 12.3 `evolve/prompts/coding.md` / `workflow.md` / `data.md`

- `coding.md`：写“开发场景分流”和“验证顺序”，不重复完整工具 schema；
- `workflow.md`：只覆盖目录整理，不把编辑器 patch、测试或 Git 回退规则混进来；
- `data.md`：只覆盖数据格式和数据工具，数据库适配器单独放 `data.md` 或 bucket；
- 每个主题 prompt 开头保留“active 工具不受主题硬锁，细节看 INDEX”的事实，避免重新制造 Phase 23 的主题门禁。

### 12.4 `project-boundaries.md` / delivery prompts

- `project-boundaries.md` 继续承载计划门、project_root、狂奔覆盖和 verification 出口；
- `project-delivery-solo.md` / `ritual.md` 只承载交付节奏，不教底层 shell；
- 新增工具必须说明：confirmed/draft/verification/release_wait 各阶段是否可见；
- 任何工具结果都不能替代 VERIFY 证据，尤其是测试文字摘要、review 意见和 browser 截图。

### 12.5 `loader.py` 动态 overlay

`format_capability_hints()` 只放“选工具的短句”；`format_project_overlay()` 只放当前项目事实；`format_turn_discipline_overlay()` 只放当前回合纪律；`digest_profile_note` 只修正旧摘要冲突。建议新增的动态字段：

```text
[工具协议]
- 当前 feature flags：...
- 交互会话：未创建 / session_id=... / state=...
- 测试结果协议：structured_test v1
- 页面检查：browser_inspect disabled|enabled

[失败后动作]
- patch conflict → 重新读取目标文件，再生成新 patch
- test failed → 读取 failed[].file:line，再跑最小重试
- terminal stopped → 使用同 session_id 查询状态或重新创建
```

这些字段只有在对应能力已实现并且状态真实可读时才注入。当前项目没有通用 feature flag 注册表；实现前必须先定义 flag 的来源、默认值、命名空间和 `loader` 读取入口，不能在静态 prompt 中写“当前 feature flags 已开启”。

## 13. 提示词回归矩阵

| ID | 场景 | 断言 |
|----|------|------|
| IT-6210 | 普通会话 system 组装 | 断言 `LoadedSystem.section_names`、`static_section_names`、`dynamic_section_names` 与 `loader.py` 实际结果一致；没有不存在工具名；`include_overlay=False` 只含基础段 |
| IT-6211 | 项目 confirmed | project prompt、open task、工具目录和阶段 overlay 同时存在；不重复注入完整工具手册 |
| IT-6212 | 项目 draft | prompt 明确禁止源码写入；测试/构建权限与代码一致；不因文案绕过 executor |
| IT-6213 | 狂奔 implementing | 只出现真实项目内授权和暂停条件；不出现普通模式“请确认计划/继续”噪声 |
| IT-6214 | 狂奔 verification | 结构化验收结果优先；主 Agent 不被提示直写 `ENV.md` / `PROJECT.md` 脱困 |
| IT-6215 | `topics=[]` | active 工具仍可调；不出现“确认 coding/workflow 主题后才能调用” |
| IT-6216 | 能力 feature flag 关闭 | prompt、INDEX、LLM function schema 都不把 disabled 工具当主路径 |
| IT-6217 | 工具失败 | prompt 引导重新读/重跑/暂停，不诱导相同失败盲重试，不宣称成功 |
| IT-6218 | 用户已有改动 | git_snapshot diff/git_restore 文案要求保护用户改动；baseline/ledger 无法归属时标 `unknown`/`mixed` 并暂停；不能建议 reset/force checkout |
| IT-6219 | 只聊模式 | 所有写/执行 proxy 和 evolved 执行入口不暴露；只读工具仍可用 |
| S-6210 | 真实 coding 回合 | 读文件 → 选择工具 → 修改 → diff → 最小测试的主路径无额外主题确认 |
| S-6211 | 真实狂奔回合 | 交互/patch/测试失败时能停在真实原因，不出现工具缺失幻觉 |

## 14. Phase 61 设计验收

本文件完成后才允许进入实现评审。实现前必须补充：

- 每个 P0/P1 能力的工具 schema 和 confirm/WRITE-SCOPE 边界；
- `STABILIZATION` 影响矩阵和独立 IT/S 验收编号；
- 与现有工具的兼容、归档或复用关系；
- Desktop、Terminal、狂奔三类消费者的最小回归路径；
- 对缺依赖、超时、取消、用户已有改动和外部写操作的失败契约。
- T-6220/T-6221 的开源组件清单和引入门；任何新依赖先完成许可证、传递依赖和运行时资产核验。

推荐首批只做 P0，不同时启动 P1/P2。语义搜索、Playwright、远程协作和数据库适配器都不应以“顺手”名义混入首批实现。

## 15. 修订记录

| 版本 | 日期 | 说明 |
|------|------|------|
| 0.1.1 | 2026-09-08 | 完成 Phase 61 缺口、契约、系统提示词矩阵和狂奔边界盘点 |
| 0.1.2 | 2026-09-08 | 增加开源优先选型；核对 OpenHands/Aider/Continue/Cline/Goose/SWE-agent 根许可证；补充组件复用边界、供应链引入门和任务台账 |
| 0.1.3 | 2026-09-08 | 完成 `interactive_terminal` experimental 首个实现切片；补充可选 `pywinpty` 依赖、阻塞读取隔离、EOF/游标语义和 IT-6201 验收记录 |
| 0.1.4 | 2026-09-08 | 完成 `patch_file` v2 experimental 首个实现切片；补充结构化多 hunk、base hash、dry-run、patch ledger、幂等和 IT-6202 验收记录 |
| 0.1.5 | 2026-09-08 | 完成 `git_snapshot` diff 扩展与 `git_restore` 文件级 experimental 切片；补充 expected hash、staged/worktree、未跟踪路径和 IT-6203 回归记录 |
| 0.1.6 | 2026-09-08 | 完成 `structured_test` v1 experimental 协议适配；统一状态、失败定位、尾部输出、超时/缺依赖语义和 IT-6204 回归记录 |
| 0.1.7 | 2026-09-08 | 完成 Windows PTY 宿主生命周期修复、状态文件并发保护和三个工具验收；`interactive_terminal`、`git_restore`、`structured_test` 升格为 active |
| 0.1.8 | 2026-09-08 | 新增 `INTERACTIVE-TERMINAL-UI.md`；明确终端会话可见性、状态同步、输出查看、重连、关闭权限和 S-6224 验收标准 |
