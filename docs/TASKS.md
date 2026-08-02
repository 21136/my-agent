# 任务清单（TASKS）

> 版本 0.1.0 · 2026-07-09 · 细分到每个 task，**先文档评审再动手**  
> **新会话**：先读 [MAP.md](./MAP.md) 了解目录与当前进度。  
> **当前 Phase**：**Phase 27** 执行可观测 **M0 done**（[EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md)）；Phase 26 done；Phase 24 剩余项；已解冻 · [DOC-04](./TASKS.md)  
> 顺序：**工具设计 → 工具实现 → 对话壳 → 进化（memory/tool）→ skill 最后**

**图例**：`状态` = `todo` | `doc` | `done` | `defer`  
**依赖**：必须先完成的 task id

## done 定义（DOC-03 · [STABILIZATION.md](./STABILIZATION.md) §9.2）

> **落盘**：T-1800-07 · **DOC-03 核对**：T-1806-doc-03（2026-07-18）— 与 §9.2 四条一致，无需重写。

一项标 **done** 须同时满足四条：

- [ ] 代码合入
- [ ] 相关 IT 绿，或声明「仅手工」并挂 smoke ID
- [ ] `stabilization-log.md` 或等价记录至少 1 次相关路径 pass
- [ ] `BUGS.md` / `CHANGELOG.md` 已更新（若修缺陷）

**禁止**：仅「实现了」+「待桌面验收」长期挂账（Phase 9～17 的教训，E 类根因）。

## 新 Phase 准入（DOC-04 · [STABILIZATION.md](./STABILIZATION.md) §9.3 · 解冻后生效）

> **落盘**：T-1800-08 · **DOC-04 核对**：T-1806-doc-04（2026-07-18）— 与 §9.3 一致（§3 矩阵行 + S/IT id），无需重写。

开新功能 Phase 前，Phase 提案须写明：

- [ ] 影响 [STABILIZATION.md](./STABILIZATION.md) §3 覆盖矩阵的**哪些行**（新增面须补矩阵行 + 档位）
- [ ] 回归哪些 **S-xx smoke / IT-xx 自动化** ID（既有 ID 或新增 ID）

**缺省 = 评审驳回**。Phase 18 **已解冻**（T-1890-10）；新功能 Phase 按上表准入。

---

## Phase 0 — 文档与基线（当前）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-001 | 项目总览文档 | `docs/PROJECT.md` v0.2.1 | — | 目标/非目标/里程碑可读 | done |
| T-002 | 四轮评审整合 | `docs/REVIEW-SUMMARY.md` | T-001 | 决议可追溯 | done |
| T-003 | 分层说明（先 tool 后 skill） | `docs/LAYERS.md` | T-001 | 建设顺序无歧义 | done |
| T-004 | 工具系统设计 | `docs/TOOLS.md` | T-003 | §11 检查项可勾选 | done |
| T-005 | 本任务清单 | `docs/TASKS.md` | T-003,T-004 | 每条 task 可独立执行 | done |
| T-005b | 记忆系统设计（三件套 + 主题路由） | `docs/MEMORY.md` | T-003 | §10 检查项可勾选 | done |
| T-005c | 工具系统修订（6 Builtin + 主题 tools + common） | `docs/TOOLS.md` v0.2、`evolve/_index.toml` | T-005b | §14 检查项可勾选 | done |
| T-005d | 对话层设计（RUNTIME） | `docs/RUNTIME.md` | T-005b,T-005c | §11 检查项可勾选 | done |
| T-005e | 进化写入设计（proposal / 防重复） | `docs/EVOLVE.md` | T-005d | §13 检查项可勾选 | done |
| T-006 | Git 首次 push 私有远端 + `requirements.txt` | remote + 首 commit | T-001～T-005e 评审后 | `git push` 成功；`httpx>=0.27` | done |

---

## Phase 1 — M1a：工具层（**最先实现**，无 LLM）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-101 | 定义统一 result/error JSON | `agent-core/tools/schema.py` | T-004 评审通过 | 单测或手工示例 | done |
| T-102 | 路径解析：agent 根 + workspace 边界 | `agent-core/paths.py` | T-101 | 越界路径拒绝 | done |
| T-103 | Builtin `read_file` | `agent-core/tools/builtin/read_file.py` | T-101,T-102 | 读 workspace 下文本；超大小拒绝 | done |
| T-104 | Builtin `list_dir` | `agent-core/tools/builtin/list_dir.py` | T-102 | 列出一层；recursive 可选 | done |
| T-104a | Builtin `grep` | `agent-core/tools/builtin/grep.py` | T-102 | 本地搜；结果上限 | done |
| T-104b | Builtin `web_search` | `agent-core/tools/builtin/web_search.py` | T-101 | 默认 DeepSeek（`LLM_API_KEY` + Anthropic 子调用）；可选 `brave`；§7.4 schema | done |
| T-104c | Builtin `fetch_url` | `agent-core/tools/builtin/fetch_url.py` | T-101 | `httpx`；HTML→文本；SSRF；32k/2MB 上限；§7.5 | done |
| T-105 | 扫描 `evolve/tools/**/tool.toml` | `agent-core/tools/registry.py` | T-004,T-005c | 含 common/ 与 topic 子目录 | done |
| T-106 | `tool.toml` 解析与校验 | `registry.py` 扩展 | T-105 | 非法 schema 启动失败并提示 | done |
| T-107 | Evolved 执行器 + Builtin `run_evolved` | `agent-core/tools/builtin/run_evolved.py` | T-105,T-106 | stdin/stdout JSON；示例跑通 | done |
| T-108 | ToolExecutor：confirm 交互 | `agent-core/tools/executor.py` | T-103～T-107 | y/n/a；`a` 仅 workspace_only evolved；§6.3 | done |
| T-109 | ToolExecutor：dry_run + 超长结果落盘 | `executor.py` | T-108 | dry_run 不写文件；>8k 落盘 §6.4 | done |
| T-110 | `evolve_log.jsonl` 写入 | `agent-core/tools/logging.py` | T-108 | 每次调用一行 JSON | done |
| T-111 | 种子 `write_text` | `evolve/tools/common/write_text/` | T-107 | active；CLI dry-run 通过 | done |
| T-112 | **CLI** `my-agent tool run ...` | `agent-core/cli_tools.py` | T-108～T-111 | 可调 6 builtin + 任意 evolved | done |

**Phase 1 完成标志**：无 LLM 也能 `tool run grep`、`tool run evolved write_text`，并写 log。

---

## Phase 2 — M1b：对话壳 + LLM（见 [RUNTIME.md](./RUNTIME.md)）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-201 | LLM 薄封装（DeepSeek / OpenAI 兼容） | `agent-core/llm_client.py` | T-006 | flash/pro 模型；`LLM_TIMEOUT_SEC=120`；context 上限 §6.1；`python llm_client.py` | done |
| T-202 | **6 Builtin** → LLM functions | `agent-core/agent.py` | T-105,T-201 | 无平铺 evolved；`python agent.py` | done |
| T-203 | `session.py` 续接 + 持久化 | `agent-core/session.py` | T-201 | 默认 resume 最近 id；`create_new` 新建；`python session.py` | done |
| T-204 | `loader.py` system 基础 + overlay | `agent-core/loader.py` | T-203 | 含 safety 始终加载；digest 段；`python loader.py` | done |
| T-205 | `router.py` 主题 JSON（新会话/换主题）+ `加主题` 并集 | `agent-core/router.py` | T-204 | topics 替换 vs 追加；快捷 `主题 x`；确认后 `llm_model`；`python router.py` | done |
| T-206 | `agent.py` 主循环 + tool 内循环（≤10） | `agent-core/agent.py` | T-202,T-108 | 锚定块 + messages.jsonl；`python agent.py` | done |
| T-207 | `main.py` REPL + 命令 | `agent-core/main.py` | T-206 | 新会话/换主题/压缩/exit；`--record`；`python main.py --demo` | done |
| T-208 | `context.py` digest 压缩 | `context.py` | T-206 | 85% 自动 + `压缩` 手动；K=8；digest≤8k；messages.jsonl 不截断 | done |
| T-209 | `agent-core/prompts/core.txt` | `prompts/core.txt` | T-204 | 身份、边界、禁止假装执行 | done |
| T-210 | `start.bat` | 根目录 | T-207 | 双击进 CLI | done |

**Phase 2 完成标志**：续接 thread → 对话调 grep → `run_evolved write_text` 经 confirm；thread 落盘。

**与 Phase 3 衔接**：T-301～T-308 将 loader/router 与 `_index`、evolved 清单对齐（可先 stub topics=[]）。

**Phase 2 完成标志**：对话中说「列出 workspace」「读某文件」，LLM 能正确选 tool 且经过 confirm。

---

## Phase 3 — M1c：记忆三件套（紧随 M1b，不阻塞首版）

> 设计详见 [MEMORY.md](./MEMORY.md)。**已决**：M1a+M1b 先交付可运行 tool 环；本 Phase 完成后才有完整主题路由与记忆索引。

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-301 | 解析 `evolve/_index.toml`，启动注入主题列表 | `agent-core/loader.py` | T-203 | system 含 topic id/name/description/tool_dirs | done |
| T-302 | 扫描 `evolve/memories/**/*.md` frontmatter，注入 id+summary 索引 | `loader.py` | T-301 | archived 不注入；格式见 MEMORY §5 | done |
| T-303 | Session `goal.md` + 对话上下文（`prompt_and_set_goal` 保留；**新会话默认不问**） | `main.py` 或 `session.py` | T-203 | `新会话` 直接 S4；显式 goal API 可写 `goal.md` 并注入 anchor | done |
| T-304 | 主题路由阶段1：LLM 输出 `topics[]` + 用户确认 | `agent-core/router.py` 或 `loader.py` | T-301,T-303 | 用户可改/否决提议主题 | done |
| T-305 | 主题路由阶段2：加载命中主题的 `prompts/<topic>.md` 全文 | `loader.py` | T-304 | 确认后 system 含 coding 等全文 | done |
| T-306 | 启动/主题确认事件写 evolve_log | `loader.py` | T-110,T-305 | log 含 memory_ids、topics_confirmed | done |
| T-307 | 示例：`prompts/coding.md` + `memories/coding/example.md` | `evolve/` 样例 | T-305 | 对话体现主题规则与记忆索引 | done |
| T-308 | 主题确认后注入 evolved 清单（common + 命中 tool_dirs） | `loader.py` | T-305,T-105 | run_evolved 仅允许清单内 name | done |

**Phase 3 完成标志**：主题确认后注入 prompt + memory 索引 + evolved 清单；`read_file evolve/memories/**` 写 L2 `entity_used`。

**T-301 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python loader.py
```

exit 0；含 `[PASS] T-301: _index.toml → id/name/description/tool_dirs in system`。详见 [MAP.md](./MAP.md) §9.16。

**T-302 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python loader.py
```

exit 0；含 `[PASS] T-302: scan memories; archived skipped` 与 `[PASS] T-302: memory index in S0`。详见 [MAP.md](./MAP.md) §9.17。

**T-303 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python session.py
python main.py --demo
```

exit 0；含 T-303 相关 `[PASS]`（goal 问答、`goal.md`、anchor + system 注入、续接不重问）。详见 [MAP.md](./MAP.md) §9.18。

**T-304 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python router.py
python main.py --demo
```

exit 0；含 T-304 `[PASS]`（accept / reject / override / S3→S4）。详见 [MAP.md](./MAP.md) §9.19。

**T-305 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python loader.py
```

exit 0；含 `[PASS] T-305: confirmed topics inject full prompt` 与 `[PASS] T-305: real repo coding.md full text in system overlay`。详见 [MAP.md](./MAP.md) §9.20。

**T-306 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python tools\logging.py
python loader.py
```

exit 0；含 `[PASS] T-306: evolve_log session_start + topics_confirmed`。交互式 REPL 启动或 `新会话` 后可用 `Get-Content ..\data\evolve_log.jsonl -Tail 3` 查看新行。详见 [MAP.md](./MAP.md) §9.21。

**T-307 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python loader.py
```

exit 0；含 `[PASS] T-307: coding prompt + memory index in system`。确认 coding 主题后 REPL 对话应能遵守 `evolve/prompts/coding.md` 规则，并在 system 看到 `project-my-agent` 记忆索引。详见 [MAP.md](./MAP.md) §9.22。

**T-308 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python loader.py
python agent.py
```

exit 0；含 `[PASS] T-308: evolved catalog (common+topic) + run_evolved allowlist`。详见 [MAP.md](./MAP.md) §9.23。

**Phase 3（M1c）已全部完成**；**Phase 4（M2）已完成**（`T-401`～`T-407`）。**Phase 5（M3）已完成**（`T-501`～`T-504`）。**Phase 6（M4）已完成**（`T-601`～`T-604`）；**`T-006` 远端 push done**。可选：`T-601b` / `T-605` skill。

**T-401 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python boundaries.py
python main.py --demo
```

exit 0；含 3 条 `[PASS] T-401:`（exit + session_end、Ctrl+C 阻断检查点）。交互式：`exit` 保存并退出；`Ctrl+C` 不结束 thread。详见 [MAP.md](./MAP.md) §9.24。

**T-402 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python evolve.py
python main.py --demo
```

exit 0；`evolve.py` 4 条 `[PASS]`；`main.py --demo` 含 `[PASS] T-402:`。REPL 输入 `记住 …` 应写入 `evolve/proposals/*.md`。详见 [MAP.md](./MAP.md) §9.25。

**T-403 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python boundaries.py
python evolve.py
python loader.py
python main.py --demo
```

exit 0；含 3 条 `[PASS] T-403:`。助手口头问是否写入 evolve 后输入 `好` → `llm_offer` 检查点；无 pending 时 `好` 仅为普通对话。详见 [MAP.md](./MAP.md) §9.26。

**T-404 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python evolve.py
python main.py --demo
```

exit 0；`evolve.py` 含 8 条 `[PASS] T-404:`；`main.py --demo` 含 4 条 `[PASS] T-404:`。REPL：`proposals` 列 pending；`proposals accept <id>` 路由写入 evolve；`proposals reject <id>` 归档至 `proposals/archive/`；检查点后可输入 `y` 当轮接受。详见 [MAP.md](./MAP.md) §9.27。

**T-405 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python evolve.py
```

exit 0；含 5 条 `[PASS] T-405:`（corpus 匹配、拒绝自评/改写、≤2 条、digest 来源、`parse_proposal_batch` 校验）。详见 [MAP.md](./MAP.md) §9.28。

**T-407 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python evolve.py
```

exit 0；含 5 条 `[PASS] T-407:`（accepted evidence_fp 硬拦、pending supersede、memory id / tool 名硬拦、checkpoint `dedup: blocked`）。详见 [MAP.md](./MAP.md) §9.29。

---

## Phase 4 — M2：进化写入（先 memory / tool，**仍不做 skill**）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-401 | 对话边界：`exit` 结束 session | `main.py` + `boundaries.py` | T-203 | Ctrl+C 不生成 proposal、不触发检查点 | done |
| T-402 | Proposal 生成（L1 prompt/memory / tool 建议） | `agent-core/evolve.py` | T-401,T-201,T-005e | 写入 `evolve/proposals/*.md`；见 EVOLVE §4 | done |
| T-403 | 触发降噪：显式话术 + 升格两跳；≤2/检查点；口头升格 ≤1/会话 | `evolve.py` + `boundaries.py` + `loader.py` | T-402 | 无 exit/任务成功/新会话软问 | done |
| T-404 | 审阅：接受/拒绝/稍后；离线 `proposals` 命令 | `evolve.py` + `main.py` | T-402 | 接受后路由；memory update 追加修订 | done |
| T-405 | evidence 仅存对话原文摘录 | `evolve.py` | T-402 | 每条 proposal ≤2 条 evidence | done |
| T-406 | tool 接受 = spec 归档；不自动生成代码 | `docs/EVOLVE.md` §7 | T-404 | 用户手放 `tools/<topic>/` | done |
| T-407 | 防重复：id / evidence_fingerprint（全局）/ supersede pending | `evolve.py` | T-402 | 见 EVOLVE §6 | done |

**Phase 4 完成标志**：对话中说 `记住` → 产生 1 条 memory proposal → 接受 → 下次启动见索引；重复同句不再生成第二条；**无需 skill**。

---

## Phase 5 — M3：从使用中固化（tool 优先）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-501 | 选 1 个真实任务 | — | Phase 2 | 你指定场景 | done |
| T-502 | 为该任务写 evolved tool + tool.toml | `evolve/tools/workflow/sort_by_extension/` | T-111,T-501 | Phase 1 CLI 能跑通 | done |
| T-503 | 对话中通过 LLM 调度该 tool 完成 1 次任务 | — | T-205,T-502 | evolve_log 有记录 | done |
| T-504 | 可选：固化 1 条 memory 而非 tool | `evolve/memories/workflow/downloads-sort.md` | T-404 | 二选一即可 | done |
| T-505 | **P1 common** 文件工具：`append_text` / `copy_move` / `move_to_trash` | `evolve/tools/common/*` | T-111 | 各 `main.py demo` exit 0；registry 扫描 5 active | done |
| T-506 | **P2 workflow** 整理工具：`rename_batch` / `flatten_dir` / `dedupe_by_name` / `archive_by_date` | `evolve/tools/workflow/*` | T-502,T-505 | 各 demo exit 0；workflow 主题清单 5 件 | done |
| T-507 | **P3 coding** 工具：`run_demo` / `run_tests` / `git_snapshot` / `patch_file` | `evolve/tools/coding/*` | T-505 | 各 demo exit 0；coding 主题清单 4 件 | done |
| T-508 | **`write_evolve`**：向 `evolve/tools/` 落地新工具文件 | `evolve/tools/common/write_evolve/` | T-505 | 路径沙箱 + demo；`workspace_only=false` | done |

**Phase 5d 完成标志**（T-507）：coding 主题可跑验收脚本、看 git 快照、打补丁；`workspace_only=false`（每次 confirm）。

**T-507 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python ..\evolve\tools\coding\run_demo\main.py demo
python ..\evolve\tools\coding\run_tests\main.py demo
python ..\evolve\tools\coding\git_snapshot\main.py demo
python ..\evolve\tools\coding\patch_file\main.py demo
python loader.py   # coding session allowlist 含 common + 3 coding 工具
```

详见 [MAP.md](./MAP.md) §9.4d。

**T-508 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python ..\evolve\tools\common\write_evolve\main.py demo
python tools\registry.py   # 含 write_evolve；live evolved 14 个 active
```

详见 [MAP.md](./MAP.md) §9.4e。

**Phase 5b 完成标志**（T-505）：common 除 `write_text` 外具备追加、复制/移动、软删除；均 `workspace_only` + `dry_run` + confirm。**T-508**：`write_evolve` 可写 `evolve/tools/`（每次 confirm，无 `a`）。

**Phase 5c 完成标志**（T-506）：workflow 主题具备扩展名整理之外的批量重命名、扁平化、查重报告、按日期归档。

**T-506 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python ..\evolve\tools\workflow\rename_batch\main.py demo
python ..\evolve\tools\workflow\flatten_dir\main.py demo
python ..\evolve\tools\workflow\dedupe_by_name\main.py demo
python ..\evolve\tools\workflow\archive_by_date\main.py demo
python loader.py   # workflow session allowlist 含 5 件主题工具
```

详见 [MAP.md](./MAP.md) §9.4c。

**T-505 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python ..\evolve\tools\common\append_text\main.py demo
python ..\evolve\tools\common\copy_move\main.py demo
python ..\evolve\tools\common\move_to_trash\main.py demo
python loader.py   # repo catalog 含四件 common
```

详见 [MAP.md](./MAP.md) §9.4b。

**Phase 5 完成标志**（M3 / PROJECT §7.2）：workflow 场景 → `sort_by_extension` tool（T-502）→ 对话/CLI 调度（T-503）→ 久远记忆 `downloads-sort`（T-504，与 tool 配套固化习惯）。

**T-504**：`evolve/memories/workflow/downloads-sort.md` — 启动索引一行 summary；正文按需 `read_file`。

**T-501 场景（已选）**：workflow 主题 — **按扩展名整理 workspace 子目录**（模拟「下载夹分类」）。输入目录路径，将顶层文件移入 `pdf/`、`jpg/`、`_no_ext/` 等子文件夹；支持 `dry_run` 预览。

**M3 验收**：§PROJECT 7.2 之「真实任务更省事」+ log 有 tool 引用；**仍不要求 skill**。

**T-502 手工验收**（仓库根 + `agent-core/`）：

```powershell
cd D:\my-agent\agent-core
python ..\evolve\tools\workflow\sort_by_extension\main.py demo

# PowerShell：外层单引号 + 内层 \" 转义（勿用 "{\"path\":...}"）
python my-agent tool run evolved sort_by_extension --json '{\"path\":\"_sort_cli_test\"}' --dry-run -y
```

exit 0；`main.py demo` 5 条 `[PASS]`；CLI 返回 `moved[]` 且 `dry_run: true`。

**T-503 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python agent.py
```

exit 0；含 `[PASS] T-503:`；`loader.py` 含 `[PASS] T-502:`。详见 [MAP.md](./MAP.md) §9.30～§9.31。

**交互式**（`LLM_API_KEY`）：`python t503_live.py` → 按屏上提示对话 → `exit` 后检查 `workspace/_downloads_inbox/` 与 `evolve_log.jsonl`。

---

## Phase 6 — M4：治理（skill 此时才可选）

> 设计见 [GOVERNANCE.md](./GOVERNANCE.md)。

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-601 | `ReviewCollector` + `my-agent review` | `agent-core/governance/` | T-110,T-301 | never-used / suspect / soft≥3词；`ReviewReport` v1.0 | done |
| T-601a | `--format cli\|json\|markdown` + `-o` | `ReviewRenderer` | T-601 | JSON 可被 jq 解析 | done |
| T-601b | **可选** evolved `governance_review` tool | `evolve/tools/common/` | T-601,T-502 | `run_evolved` 返回同 schema | defer |
| T-602 | suspect：`feedback_negative` 聚合 + 写 `status` | executor / session | T-203 | 3 次否定 → suspect；tool 拒绝执行；见 RUNTIME §10 | defer |
| T-602a | M3：`entity_used` + `pending_feedback` | loader / executor | T-305 | L2：仅 `read_file`→`evolve/memories/**`；L3+ 见 GOVERNANCE §3 | done |
| T-602b | M4：exit 问句 + `feedback_*` | `session.py` / `main.py` | T-602a | `MY_AGENT_FEEDBACK_ON_EXIT=1`；y/n/skip | done |
| T-602c | `failure_streak` 聚合 + `marked_suspect` | `governance/suspect.py` | T-602b | 与 GOVERNANCE §6 一致 | done |
| T-603 | `my-agent audit`（LLM 兜底） | `agent-core/governance/audit.py` | T-601 | `llm_findings[]`；不自动改文件 | done |
| T-604 | Git 回滚习惯写入 README | `README.md` + `governance/git_hints.py` | T-006 | accept / review 后提示 commit | done |
| T-605 | **可选** 显式加载 SKILL.md | `loader.py` 扩展 | T-502,T-503 | 用户说「用 xxx skill」才注入 | defer |
| T-606 | **可选** 自动 skill 路由 | `router.py` | T-605 | M4+，非 MVP | defer |

**T-601 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python governance\review.py demo
python my-agent review
python my-agent review --topic workflow
```

exit 0；`demo` 含 12 条 `[PASS] T-601:` + 4 条 `[PASS] T-601a:`；CLI 打印 Summary / Never-used / Observation / Suspect / Conflicts / Pending 块。详见 [MAP.md](./MAP.md) §9.33。

**T-601a 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python governance\review.py demo                    # 含 T-601a [PASS]
python my-agent review --format json | python -c "import json,sys; json.load(sys.stdin)"
python my-agent review --format markdown -o ..\data\reviews\latest.md
python my-agent review --format json -o ..\data\reviews\latest.json
```

`--format json` 输出完整 `ReviewReport` v1.0（可被 `jq` / `json.load` 解析）；`-o` 落盘；默认仍为 `cli` → stdout。

**T-602a 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python governance\entity_usage.py
python tools\logging.py
python tools\executor.py
```

exit 0；含 `[PASS] T-602a:`（L2 路径判定、`entity_used` log、memory `use_count`/`last_used_at`、`meta.pending_feedback`、非 memory 不触发）。对话或 CLI 执行：

```powershell
python my-agent tool run read_file --json '{\"path\":\"evolve/memories/workflow/downloads-sort.md\"}' -y --session _t602a_test
Get-Content ..\data\evolve_log.jsonl -Tail 3
Get-Content ..\data\sessions\_t602a_test\meta.json
```

末行 `event` 应为 `entity_used`；`meta.json` 含 `pending_feedback` 条目。

**T-602b 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python governance\feedback.py
python session.py
python main.py --demo    # 含 [PASS] T-602b
```

交互式（需先 `read_file` 某 memory 产生 `pending_feedback`）：

```powershell
$env:MY_AGENT_FEEDBACK_ON_EXIT = "1"
python main.py
# … 对话中 read_file evolve/memories/... 
# exit → 问「这次用得对吗？」→ y / n / 回车跳过
Get-Content ..\data\evolve_log.jsonl -Tail 5   # feedback_positive / feedback_negative
```

默认 **不**问；仅 `exit` 时、且 `pending_feedback` 有 L2+ 实体时触发；每 exit 只问 **一个**（L4>L3>L2，同层 `used_at` 最新）。

**T-602c 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python governance\suspect.py
python governance\feedback.py    # 含 T-602c integration [PASS]
```

| 场景 | 预期 |
|------|------|
| `compute_failure_streak` | 仅从 `evolve_log` 聚合；`feedback_positive` 归零 |
| 连续 3 次 `feedback_negative` | memory frontmatter / `tool.toml` → `status: suspect` |
| 达阈值 | `evolve_log` 追加 `marked_suspect`（含 `failure_streak`） |
| 已 suspect | 幂等，不重复写 `marked_suspect` |

交互式（需 `MY_AGENT_FEEDBACK_ON_EXIT=1`，同一 entity 跨 3 次 session exit 均答 `n`）：

```powershell
Get-Content ..\data\evolve_log.jsonl -Tail 10   # feedback_negative ×3 + marked_suspect
# 检查 evolve/memories/... 或 evolve/tools/.../tool.toml 的 status: suspect
```

**T-603 手工验收**（`agent-core/` 或仓库根）：

```powershell
cd D:\my-agent\agent-core
python governance\audit.py demo

cd D:\my-agent
python my-agent audit --topic coding
python my-agent audit prompts --topic coding
python my-agent audit --format json -o data\reviews\audit-latest.json
python my-agent audit --only-llm --format cli
```

| 场景 | 预期 |
|------|------|
| 默认 | `ReviewCollector.collect()` + LLM 审阅 prompt + 同 topic active memory |
| `audit prompts` | 仅 prompt 文件进 LLM corpus |
| `--only-llm` | CLI/markdown 只打 `llm_findings` 块 |
| `--format json` | 完整 `ReviewReport` v1.0；`scope.audit_ran=true` |
| 无 API key | `audit.py demo` 用 mock 通过；CLI 需 `LLM_API_KEY` |
| 完成后 | `evolve_log` 追加 `audit_completed`（`findings_count`, `scope`） |

**T-604 手工验收**（仓库根）：

```powershell
cd D:\my-agent
python agent-core\governance\git_hints.py
python my-agent review --topic coding
python my-agent audit --topic coding --only-llm
```

| 场景 | 预期 |
|------|------|
| `README.md` | 「Git 回滚习惯」：何时 commit、`git checkout` 回滚 |
| `git_hints.py` | 2 条 `[PASS] T-604` |
| `my-agent review` / `audit`（cli） | 末段 `== Git ==` |
| `proposals accept` | 消息含 `Git: git commit -m "evolve: accept …"` |

**T-006 手工验收**（仓库根）：

```powershell
cd D:\my-agent
git remote -v
git branch -vv
# 默认分支 main；私有远端 origin → github.com/21136/my-agent
```

| 场景 | 预期 |
|------|------|
| `git push` | 成功；`requirements.txt` 含 `httpx>=0.27` |
| `evolve/proposals/` | 仅 `.gitkeep` + `archive/.gitkeep`（无 demo 垃圾） |
| `evolve/memories/` | 保留 `example.md`、`downloads-sort.md` |

---

## Phase 7 — 对话编排（M5，ORCHESTRATION）

> 设计全文：[ORCHESTRATION.md](./ORCHESTRATION.md) v0.2.0 · **核心：explore 子代理（T-706）+ execute 续跑（T-705）**，不用固定轮次表扛大 task。

| ID | 任务 | 主要文件 | 依赖 | 验收 | 状态 |
|----|------|----------|------|------|------|
| T-701 | **Turn discipline**：`core.txt` + overlay（含子代理摘要） | `prompts/core.txt`, `loader.py` | T-209 | `loader.py` T-701 [PASS] | done |
| T-702 | **Ask/Agent**：`只聊`/`动手`；ask 禁 `run_evolved` | `session.py`, `main.py`, `executor.py` | T-701 | `main.py --demo` T-702 | done |
| T-703 | **轻量分类**：何时自动 spawn explore（无轮次表） | `turn_intent.py`, `agent.py` | T-706 | `turn_intent.py demo` | done |
| T-704 | **可选** qa 短循环软提醒 | `agent.py` | T-703 | `agent.py` T-704 | done |
| T-705 | **execute 多 segment 续跑**（大 task） | `agent.py` | T-706 | `agent.py` T-705 | done |
| T-706 | **explore 子代理**（只读、独立预算、摘要回父会话） | `subagent.py`, `agent.py`, `main.py` | T-202 | `subagent.py demo` + T-706 | done |

**Phase 7 完成标志**：见 [ORCHESTRATION.md](./ORCHESTRATION.md) §12。

**T-704 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python agent.py          # 含 [PASS] T-704: qa soft reminder
```

| 场景 | 预期 |
|------|------|
| `qa` 连续 tool | 第 `PARENT_SHORT_MAX-1` 轮后注入「请直接回答」内核消息 |
| `plan` / `execute` | 不注入软提醒 |

**T-706 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python subagent.py demo
python agent.py          # 含 [PASS] T-706
python main.py --demo    # 探索 命令
```

**T-705 手工验收**：

```powershell
python agent.py          # 含 [PASS] T-705: multi-segment execute
```

**T-701 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python loader.py
```

exit 0；含 3 条 `[PASS] T-701:`（`core.txt` Turn discipline、`subagent: none/used`、turn_discipline overlay）。

**T-702 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python session.py
python tools\executor.py
python agent.py
python loader.py
python main.py --demo
```

| 场景 | 预期 |
|------|------|
| `只聊` / `ask` | `meta.turn_mode=ask`；LLM 仅 5 个 builtin（无 `run_evolved`） |
| `动手` / `agent` | 恢复 6 个 builtin |
| executor | ask 下 `run_evolved` → `VALIDATION_ERROR` |
| explore | ask 模式仍可用（`探索 …` 只读子代理） |

exit 0 时应含多条 `[PASS] T-702:`。

**T-703 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python turn_intent.py
python agent.py          # 含 [PASS] T-703
python loader.py         # 含 [PASS] T-703
```

| intent | 自动 explore |
|--------|----------------|
| `qa` / `plan` | 否 |
| `research` / `execute` + 标记/路径 | 是（`MY_AGENT_AUTO_EXPLORE=1`） |

**T-701～T-704**：见 [ORCHESTRATION.md](./ORCHESTRATION.md) 各节。

---

## Phase 8 — 用户扩展层（双索引）

> 设计全文：[EXTENSIONS.md](./EXTENSIONS.md) v0.1.0 · **核心：种子索引与用户索引分离；`注册主题` 代替手改 TOML**。

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-801 | 双索引 + 合并加载 | `_index.core.toml`、`_index.user.toml`、`loader.load_topic_index` | T-301 | 合并正确；id 冲突报错；仅 `_index.toml` 回退兼容 | done |
| T-802 | `write_evolve` scope 读合并索引 | `write_evolve/main.py` | T-801 | 已注册 user 主题可写；未注册拒绝 | done |
| T-803 | REPL `注册主题 <id>` | `router.py`、`main.py`、`loader.py` | T-801 | `y` 后写 user 索引 + prompt/memory/tool 脚手架 | done |
| T-804 | **可选** `MY_AGENT_EXTENSIONS` | `paths.py`、`loader.py` | T-801 | 外挂目录与 user 索引等价加载 | defer |
| T-805 | **示例** `data` + `csv_head` | `tools/data/csv_head/` | T-801～T-803 | `主题 data` 会话可见 `csv_head`；`run_evolved` 跑通 | done |

**Phase 8 完成标志**：用户无需手改 TOML 即可 `注册主题 data` 并落地 `csv_head`；`git diff` 用户扩展集中在 `_index.user.toml` 与 `tools/data/`。**已达成（T-805）**。

**T-801 手工验收**（`agent-core/` 下）：

```powershell
cd D:\my-agent\agent-core
python loader.py
```

| 场景 | 预期 |
|------|------|
| 仅 `_index.toml` | 与迁移前行为一致 |
| core + 空 user | S0 主题列表与现网相同 |
| core + user `data` | 合并列表含 data |
| user `id` 与 core 重复 | 启动失败，明确报错 |

**T-803 手工验收**：

```text
注册主题 data
y
主题 data
→ system 含 topic_prompt:data；evolved 清单含 tools/data/
```

---

## Phase 9 — Electron 桌面壳（**done** · M0–M1）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-904 | 桌面壳设计 | `docs/DESKTOP.md` | Phase 7 | grow 布局/色系已定 | done |
| T-904a | WS sidecar | `agent-core/server.py` | T-207 | 续接/发消息/事件流 | done |
| T-904b | LLM 流式 + reasoning | `llm_client.py` + grow UI | T-201 | `assistant.delta` / `reasoning.delta` | done |
| T-904c | Electron 脚手架 + grow | `desktop/` | T-904a | `npm run dev` 起壳 | done |
| T-904d | 顶栏 / confirm / A 层过程 | `shells/grow/` | T-904c | 点击 confirm、过程行 | done |
| T-904e | Shell 路由 + R 主题 | `shell-router` + 设置栏 | T-904b,d | 切壳、暗色 | done |
| T-904f | 托盘 / 快捷键 / 切 CLI | `electron/main.ts` + `start-desktop.bat` | T-904e | 托盘常驻、`Ctrl+Shift+M` | done |
| T-904i | 会话锁 | `interface_lock.py` | T-904a | CLI/Electron 互斥 + 接管 | done |
| T-905a | `recall` 意图 + 无 tools 父循环 | `turn_intent.py`, `agent.py` | T-703 | `turn_intent.py` + `agent.py` T-905 demo | done |
| T-905b | `turn.start` / `turn.notice` / `session.memory` | `server.py`, `main.py` | T-905a | WS + CLI `[本轮·…]` | done |
| T-905c | grow 顶栏意图/记忆 + 状态栏分场景 | `shells/grow/`, `api/ws.ts` | T-905b | 无 reasoning 不显示「思考中」 | done |
| T-905d | 续接灌入 `session.history` | `session.py`, `server.py`, grow | T-904a | 重开桌面可见过往 user/assistant | done |
| T-906 | Activity Router + `ui.route` 自动切壳/加主题 | `activity_router.py`, `agent.py`, `server.py`, `desktop/` | T-905d | 规则表 P1；锁定/撤销 | done |
| T-906a | 外壳保活 + `session.refresh` | `desktop/main.ts`, `server.py` | T-906 | 切壳不丢对话 | done |
| T-907 | **模式驱动预算**：`turn_mode=agent` 统一宽预算 + T-705；intent 仅提示 | `agent.py`, `loader.py` | T-705,T-702 | [MODE-BUDGET.md](./MODE-BUDGET.md) §8 | done |
| T-904g | Shell **`daily` · Amp（极致嗨）** | [DAILY-SHELL.md](./DAILY-SHELL.md) | T-904e | M2 全窗 busy 能聊 | **done** |
| T-904g1 | ~~`starfield` canvas~~ | — | T-904g | — | **superseded** |
| T-904g2 | daily 对话层 + composer（无 grow chrome） | `shells/daily/` | T-904g | 无 proposal 顶栏 | **done**（保留） |
| T-904g3 | ~~WS → 星图~~ | — | — | — | **superseded** |
| T-904g4 | ~~recall 镜头 + 灌星~~ | — | — | — | **superseded** |
| T-904g5 | ~~reduced-motion 星图~~ | — | — | — | **superseded** |
| T-904g6 | 抽 `chat-state` 与 grow 共用 | `shells/chat-state.ts` | T-904g2 | 减重复 | **done**（保留） |
| T-904g7 | ~~`constellation.json` 星图落盘~~ | — | — | — | **superseded**（h6 清理） |
| T-904g8 | ~~新星音效~~ | — | — | — | **cancelled** |
| **T-904h1–h3** | ~~咖啡馆视觉~~ | — | — | **superseded**（见 T-904i*） |
| **T-904i1** | Amp token + 亮底 + idle shimmer | T-904g2 | 打开即「冲」 | **done** |
| **T-904i2** | 排版重做（无 · 前缀、无 scrim） | T-904i1 | 字干净可读 | **done** |
| **T-904i3** | 四色 `is-working`（≠ grow） | T-904i2 | 执行时一眼不是 grow | **done** |
| **T-904i4** | recall → 对话轮次聚焦 | T-905,T-904i3 | 回顾可用 | **done** |
| **T-904i5** | reduced-motion + 窗口尺寸验收 | T-904i3 | 非最大化正常 | **done** |
| **T-904i7** | 全窗 `app-frame` + app-chrome busy 染色 | T-904i3 | 顶栏与壳同步 | **done** |
| **T-904i8** | grow 整壳沉浸 busy（玻璃子层） | T-904i7 | 与 daily 机制对齐 | **done** |
| **T-904i9** | Electron 隐藏系统菜单栏 | T-904f | Win/Linux 无 File/Help | **done** |
| T-904i6 | 清理 constellation IPC / 旧文件（可选） | T-904i2 | 减债务 | defer |
| T-904h | Shell `govern` | — | review 阶段 | defer | defer |

**2026-07-11 联调修复**（无新 task id）：BUG-001～006，见 [`docs/BUGS.md`](./BUGS.md) — confirm WS 解耦、Vite/Electron 生命周期、流式错误解析、`tool_calls` 历史 repair、`_tool_result_summary`、`TURN_LOCK` 死锁。

**2026-07-13**：BUG-007 — 聊天框 `新会话` 后 `emit_session_state` + 前端 `resetTurnActivity`（见 `DESKTOP.md` §5.2.1）。

**2026-07-13**：BUG-008～013 + [CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) — 工具确认管线加固设计（Phase 14 T-1301 doc）。

**2026-07-14**：BUG-015～017，见 [`docs/BUGS.md`](./BUGS.md) — sidecar `AgentPaths` 导入、WS `ConnectionClosed` + 每连接 outbox、guard `log_guard_event` 重复 `guard_type`。

**2026-07-14**：BUG-019 — `project_api.perform_project_switch` 错从 `session` 导入 `session_memory_event`；侧栏 `project.switch` 会话替换时 `ImportError`（见 `PROJECT-MODE.md` §8.4）。

**T-907 手工验收**（实现后，`agent-core/` 下）：

```powershell
python agent.py          # 含 [PASS] T-907:
python turn_intent.py    # 分类用例无回归
```

| 场景 | 期望 |
|------|------|
| `turn_mode=agent` + 分类为 `qa` 的短句（如「推过去」） | 不在 5 轮截断；可走 segment 续跑完成 `write_evolve` |
| `turn_mode=ask` + `qa` | 仍 `PARENT_SHORT_MAX` 短循环 |
| `recall` + `agent` | 仍 0 tools |

设计全文：[MODE-BUDGET.md](./MODE-BUDGET.md)。

**Phase 9 推迟（与桌面无关）**

| ID | 任务 | 原因 |
|----|------|------|
| T-901 | Skill proposal 自动生成 | 等 tool 稳定后再说 |
| T-902 | 多 LLM adapter | 单人无收益 |
| T-903 | SQLite / 向量检索 | 文件量级不够 |
| T-905 | 进程沙箱 | 确认流够用 |
| T-906 | 自动安装 Python 依赖 | 你手工 venv |

---

## Phase 10 — 主机托管区（**done**）

> 设计：[HOST-SCOPE.md](./HOST-SCOPE.md) v0.2.9（T-1001～T-1008 **done**）。  
> **每个 task 必须先过下方「手工验收」再标 done。**

| ID | 任务 | 交付物 | 依赖 | 状态 |
|----|------|--------|------|------|
| T-1001 | Host scope 设计评审 | `HOST-SCOPE.md` v0.2.1 | Phase 9 | done |
| T-1002 | `host_scope.json` 加载与校验 | `agent-core/host_scope.py` | T-1001 | **done** |
| T-1003 | `resolve_under_host` | `paths.py` 扩展 | T-1002 | **done** |
| T-1004 | CLI 托管目录管理 | `host_scope_cli.py` + `main.py` | T-1002 | **done** |
| T-1005 | host 只读工具 | `host_tools.py` + `evolve/tools/common/host_*` | T-1003 | **done** |
| T-1006 | host 写 + confirm | `host_copy_move` + executor/log | T-1005 | **done** |
| T-1007 | workflow 工具适配 host | `evolve/tools/workflow/*` | T-1006 | **done** |
| T-1008 | 桌面托管区设置 | `desktop/` settings | T-1004 | **done** |

**Phase 10 完成标志**：登记 Downloads（或桌面）托管区 → `host_list` / `host_read` / `host_grep` 可用；未登记路径拒绝；host / workflow 写经 confirm 且不可 `a`；桌面顶栏「托管区」与 CLI `托管目录 列表` 一致。

---

## Phase 11 — 项目模式（**M3 done**）

> 设计：[PROJECT-MODE.md](./PROJECT-MODE.md) v**0.2.1**（**T-1101 done** · T-1113 **done** · 2026-07-12）。

| ID | 任务 | 交付物 | 依赖 | 状态 |
|----|------|--------|------|------|
| T-1101 | 项目模式设计评审 | `PROJECT-MODE.md` 定稿 | Phase 10 | **done** |
| T-1102 | 三件套模板 + project prompt | `workspace/_template/` · `evolve/prompts/project.md` | T-1101 | **done** |
| T-1103 | `meta` 扩展 + CLI `项目 …` | `session.py` · `main.py` · `project_cli.py` | T-1101 | **done** |
| T-1104 | `activity_router` · `ShellId project` | `activity_router.py` | T-1103 | **done** |
| T-1105 | 桌面 M0：project 壳 + 顶栏三态 | `desktop/src/shells/project/` | T-1104 | **done** |
| T-1106 | digest / 续做 overlay | `context.py` · `loader.py` | T-1102 | **done** |
| T-1107 | project 壳工具边界 + 计划门 | `executor.py` · `project_mode.py` | T-1102 | **done** |
| T-1110 | 计划确认：`项目 确认` / plan gate | `project_cli.py` · `executor.py` | T-1103,T-1107 | **done** |
| T-1108 | M1 只读 TASKS 侧栏 | `desktop/src/shells/project/` | T-1105 | **done** |
| T-1109 | WS `project.*` / `plan.*` | `server.py` · `project_api.py` · `ws.ts` | T-1105 | **done** |
| T-1111 | M2 独立视觉 + MAP 预览 | `project.css` · `project/index.ts` | T-1108 | **done** |
| T-1112 | M2 验收一键 `run_python` | `project_mode.py` · `project_api.py` · CLI | T-1107 | **done** |
| T-1113 | M3 项目列表 + 切换续接 | `project_switch.py` · `project_api.py` · `project/index.ts` | T-1109 | **done** |
| T-1114 | Git vendor 设计 | `docs/GIT-VENDOR.md` | T-1107 | workspace + evolve_tools 双落点 | **done** |
| T-1115 | **`git_clone`** common 工具 | `evolve/tools/common/git_clone/` | T-1114 | `main.py demo`；project 边界 | **done** |
| T-1116 | **壳级会话隔离** | `shell_switch.py` · `server.py` · `desktop/` | T-1113 | `shell_switch.py demo`；切壳换会话 | **done** |
| T-1117 | **`project_catalog`** + 跨会话 read confirm | `project_catalog/` · `executor.py` | T-1116 | `main.py demo`；读他会话 messages 须 confirm | **done** |

**Phase 11 M3 完成标志**：侧栏 **我的项目** 列表；`project.switch` 一会话一项目续接；跨项目确认卡；切换后 `session.history` 灌聊天区；助手忙时禁止切换。

**Phase 11 M4 完成标志**（T-1116～T-1117）：`grow` / `daily` / `project` **三线独立会话**（`shell_sessions` + `project_sessions`）；桌面切壳 `shell.switch` 换 backend 会话；隐藏壳不收对话事件；跨壳查项目用 `project_catalog` + `read_file data/sessions/…`（非当前会话须 confirm）。

---

## Phase 12 — 拖拽文件（**M0 实施中**）

> 设计：[FILES-DROP.md](./FILES-DROP.md) v0.1.0 · **project 壳拖代码优先**

| ID | 任务 | 交付物 | 依赖 | 状态 |
|----|------|--------|------|------|
| T-1201 | sidecar 暂存 + `file.stage` | `file_stage.py` · `server.py` | Phase 9 | **done** |
| T-1202 | preload `getPathForFile` + `file-drop.ts` | `preload.ts` · `file-drop.ts` | T-1201 | **done** |
| T-1203 | `user.message` 附件注入 | `server.py` · `file_stage.py` | T-1201 | **done** |
| T-1204 | **project 壳**拖代码 + chip 发送 | `shells/project/index.ts` | T-1202,T-1203 | **done** |
| T-1205 | host 内路径免复制 | `file_stage.py` | T-1201 | done（逻辑已在 T-1201） |
| T-1206 | grow / daily 复用拖放 | `grow/index.ts` · `daily/index.ts` | T-1204 | **done** |
| T-1207 | `session.history` 附件块回放 | `user-message.ts` · 三壳聊天渲染 | T-1203 | **done** |
| T-1208 | pet 壳拖放（同 daily 规则，见 [PET-SHELL.md](./PET-SHELL.md) §3.4 · P8） | `shells/pet/` | T-1206 | **done**（T-pet-i4） |

**Phase 12 M0 完成标志**：project 壳已绑项目 → 拖入区外 `.py` → chip → 发送 → 助手首轮 `read_file` 附件路径成功。

---

## Phase 13 — 伴侶壳 M1（pet）

> 设计：[PET-SHELL.md](./PET-SHELL.md) v0.1.2 · M0 done（2026-07-12）

| ID | 任务 | 交付物 | 依赖 | 状态 |
|----|------|--------|------|------|
| T-pet-i3 | 历史附件回放 | `shells/pet/index.ts` · `user-message.ts` | T-1207 | **done** |
| T-pet-i2 | recall 扩窗 + 高亮 + 状态栏 | `shells/pet/index.ts` | T-905 | **done** |
| T-pet-i1 | `ui.route` A/B 档 + govern→grow 接引 | `shells/pet/pet-route.ts` · `pet/index.ts` | T-906 | **done** |
| T-pet-i4 | 展开态拖放（= daily；吸收 T-1208） | `shells/pet/index.ts` | T-1206 | **done** |
| T-pet-i4b | 收起态拖到光球自动展开 | `shells/pet/` · `electron/main.ts` | T-pet-i4 | defer · M2 |
| T-pet-i5 | 光球拖拽 + 位置持久化 | `pet-main.ts` · `data/state.json` | M0 | defer · M2 |
| T-pet-i6 | 工作台往返未读提示 | `shells/pet/` | T-1116 | defer · M2 |
| T-pet-i7 | `prefers-reduced-motion` | `pet.css` · `shells/pet/` | — | defer · M2 |

**M1 done**（2026-07-13）：i1–i4 见 `shells/pet/`。M2：i4b–i7 defer。

---

## Phase 14 — 工具确认管线加固（**done**）

> 设计：[CONFIRM-PIPELINE.md](./CONFIRM-PIPELINE.md) v0.1.0 · 事件 [BUG-008](./bugs/2026-07-13-confirm-pipeline-stuck.md)（2026-07-13 grow · `write_evolve` 二次确认卡死）

| ID | 任务 | 交付物 | 依赖 | 状态 |
|----|------|--------|------|------|
| T-1301 | 确认管线设计评审 | `CONFIRM-PIPELINE.md` 定稿 | BUG-008 | **done** |
| T-1302 | sidecar `confirm_fn` 有界等待 + 超时 `confirm.done` | `server.py` · `test_confirm_pipeline.py` | T-1301 | **done** |
| T-1303 | grow/project 确认卡防重入 + 状态钩子 | `grow/index.ts` · `project/index.ts` · `chat-state.ts` | T-1302 | **done** |
| T-1304 | daily/pet confirm 对齐 C3–C5 | `daily/index.ts` · `pet/index.ts` | T-1303 | **done** |
| T-1305 | executor `tool.end` finally + base64 预检 | `executor.py` | T-1302 | **done** |
| T-1306 | `turn.end` 协议 + 三壳 `resetTurnActivity` | `server.py` · `main.py` · 各壳 | T-1303 | **done** |
| T-1307 | scaffold prompt：`content_workspace_path` 优先 | `core.txt` · `TOOLS.md` · `coding.md` | T-1305 | **done** |
| T-1308 | 集成验收：坏 b64 → 二次 confirm 不卡死 | `test_confirm_pipeline.py` · 手工清单 | T-1303,T-1305 | **done** |

**M0 完成标志**：错 `request_id` 不空转；超时必 `confirm.done`；双 confirm 手工 60s 内结束；`server.py --demo` 含 confirm 管线 PASS。

---

## Phase 15 — 回合控制（Stop + 有界等待）

> 设计：[TURN-CONTROL.md](./TURN-CONTROL.md) v0.2.0 · 事件 [BUG-014](./bugs/2026-07-13-turn-stall-no-stop.md)（2026-07-13 grow ·「思考中」10+ 分钟）

| ID | 任务 | 交付物 | 依赖 | 状态 |
|----|------|--------|------|------|
| T-1401 | 回合控制设计评审 | `TURN-CONTROL.md` 定稿 | BUG-014 | **done** |
| T-1402 | `turn.cancel` WS + `_dispatch_inline` | `server.py` · `ws.ts` | T-1401 | **done** |
| T-1403 | `request_cancel` + `confirm_fn` 90s + `cancelled` | `server.py` | T-1402 | **done** |
| T-1404 | LLM 协作取消（`cancel_event`） | `llm_client.py` · `agent.py` | T-1402 | **done** |
| T-1405 | 四壳 Stop 按钮 + `chat-state` | `grow` · `project` · `daily` · `pet` | T-1402 | **done** |
| T-1406 | 取消传播 + `turn.end` cancelled | `main.py` · `server.py` | T-1403,T-1404 | **done** |
| T-1407 | `test_turn_cancel.py` + demo 条目 | `tests/` · `server.py --demo` | T-1403,T-1404 | **done** |
| T-1408 | 文档同步 | `DESKTOP.md` · `CONFIRM-PIPELINE.md` · `MAP.md` · `BUGS.md` | T-1401 | **done** |

**P1 defer**（**迁入 Phase 16**，见 [RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md)）：T-1410 → T-1510 · T-1411 → T-1511 · T-1412 → T-1512。

**M0 实现完成**：LLM / confirm 等待中点 Stop → **3s 内** `turn.end`（`cancelled`）+ 可输入；confirm 不永久「提交中…」；同步工具子进程即时终止 defer T-1512。桌面重启手工验收待用户执行。

---

## Phase 16 — 运行时行为约束（Runtime Guards）

> 设计：[RUNTIME-GUARDS.md](./RUNTIME-GUARDS.md) v0.2.0 **设计已决** · 动机：grow 沉淀 `mvn_exec` 卡死 / 乱调 `run_python`
> 与 Phase 14（confirm 诚实）、Phase 15（Stop）正交；与 Phase 17（checker）互补：**执法 vs 审计**。

| ID | 任务 | 交付物 | 依赖 | 状态 |
|----|------|--------|------|------|
| T-1501 | 约束设计评审 | `RUNTIME-GUARDS.md` v0.2.0 定稿 | — | **done** |
| T-1510 | stall 看门狗（默认关；opt-in 180s；reasoning 不计进度） | `runtime_guards.py` · `server.py` | T-1501 | **done** |
| T-1511 | `WRITE_INLINE_MAX_CHARS` 硬顶 | `executor.py` | T-1501 | **done** |
| T-1512 | cancel 时 evolved subprocess `terminate`→3s→`kill` | `run_evolved.py` · `executor.py` | T-1501 | **done** |
| T-1513 | `TURN_WALL_SEC=900` 整 turn 墙钟（segment 不重置） | `runtime_guards.py` · `server.py` | T-1501 | **done** |
| T-1514 | 可取消 `run_scaffold_demo`（手动 checker 的硬事实） | `executor.py` · `run_evolved.py` | T-1512 | **done** |
| T-1515 | 同 segment / 同 tool 的 `run_python demo` 窄域拒调 | `executor.py` | T-1514 | **done** |
| T-1516 | guard 事件 `evolve_log` / `notice` | `executor.py` · `logging.py` | T-1510 | **done** |
| T-1517 | 桌面 cancel 45s 看门狗 | `chat-state.ts` | T-1405 | **done**（S-05 / S-28 · T-1808-bug-02） |
| T-1518 | `test_runtime_guards.py` + demo | `tests/` · `runtime_guards.py` | T-1510,T-1512,T-1513,T-1519 | **done** |
| T-1519 | `LLMTimeoutError` → `finish_reason=timeout` 全链路 | `agent.py` · `server.py` · desktop | T-1501 | **done** |
| T-1520 | grow scaffold 写完 `tool.toml` 后自动调用 demo probe | `executor.py` | T-1514 | **done** |

**M0 完成标志**：`STALL_WATCHDOG_SEC=2` 时静默 turn 自动 `timeout`；整 turn 900s 墙钟不随 segment 重置；Stop+长 evolved subprocess 直接终止；LLM timeout 不落 generic error；桌面 `finish_reason=timeout` 显示「已超时」。验收：`python tests/test_runtime_guards.py` · `python runtime_guards.py` · `python tools/builtin/run_evolved.py`。

**M1 完成标志**：grow 写完 `tool.toml` 后自动调用 demo probe；同 segment 对同 tool 重复 `run_python demo` 被拒且不误伤 project；9k 内联写入被拒。验收：`python tests/test_runtime_guards_m1.py` · `python tools/executor.py`（含 T-1511/T-1514/T-1516 PASS）。

---

## Phase 17 — Checker 子代理（监工）

> 设计：[CHECKER-SUBAGENT.md](./CHECKER-SUBAGENT.md) v0.2.0 **设计已决** · 关联 [ORCHESTRATION.md](./ORCHESTRATION.md) §4 · `subagent.py`
> **独立 DeepSeek 上下文**验收；硬事实依赖 Phase 16 `run_scaffold_demo`，M1 可复用自动触发结果。

| ID | 任务 | 交付物 | 依赖 | 状态 |
|----|------|--------|------|------|
| T-1601 | checker 设计评审 | `CHECKER-SUBAGENT.md` v0.2.0 定稿 | — | **done** |
| T-1610 | `run_checker` + `SubagentKind` + cancel 透传 | `subagent.py` | T-1601,T-1514 | **done** |
| T-1611 | `evolve_tool_scaffold` checklist + verdict 归并 | `subagent.py` · prompts | T-1610 | **done** |
| T-1612 | CLI `验收` / `check` 手动命令（只运行 checker） | `main.py` · `router.py` | T-1610 | **done** |
| T-1613 | overlay + `evolve_log` kind=checker | `subagent.py` · `loader.py` | T-1611 | **done** |
| T-1614 | `subagent.py demo` + 单测 | `tests/` | T-1610 | **done** |
| T-1620 | grow scaffold 后自动 spawn checker | `agent.py` · `executor.py` | T-1610,T-1520 | **done** |
| T-1621 | 桌面验收 notice | 各壳 / `chat-state` | T-1620 | **done**（S-36～38 · T-1808-bug-03） |
| T-1622 | 注入 Phase 16 demo 结果到 `CheckerTask` | `subagent.py` | T-1514,T-1610 | **done** |
| T-1623 | 完成声明门：非 PASS 不得标「已验收/沉淀完成」 | `agent.py` | T-1620 | **done** |

**M0 完成标志**：`验收 mvn_exec` → 独立 DeepSeek + PASS/FAIL/WARN 报告；子上下文不落 `messages.jsonl`；Stop 可取消 checker；fail 不自动修复、不锁会话。验收：`python -m unittest tests.test_checker_subagent -v` · `python subagent.py` · `python main.py --demo`（含 T-1612 PASS）。

**M1 完成标志**：grow 沉淀 `tool.toml` 后（`CHECKER_AUTO_ON_SCAFFOLD=1`）自动 checker；注入自动 demo 硬事实；桌面顶栏/notice 显示「验收：通过/失败」；非 PASS 不得宣称「已验收/沉淀完成」，但仍正常 `turn.end`。验收：`python -m unittest tests.test_runtime_guards tests.test_runtime_guards_m1 tests.test_checker_subagent -v`。稳定化 smoke：**S-36～S-38** pass（T-1821-04～06 · T-1808-bug-03）。

---

## Phase 18 — 稳定化专项（**done · 已解冻**）

> 设计：[STABILIZATION.md](./STABILIZATION.md) **v1.1.0 · done · 已解冻**（T-1890-10）  
> **细粒度清单**：[STABILIZATION-TASKS.md](./STABILIZATION-TASKS.md) — M3 全 done；**M2-I**（T-1830）整批 **defer**  
> **解冻**：2026-07-18 用户签字「同意解冻：可恢复 feature Phase」· 见 [MAP.md](./MAP.md) §2.1  
> **准入**：新 Phase 须 [DOC-04](./TASKS.md) / STABILIZATION §9.3

### 粗粒度索引（Epic）

| ID | Epic | 细粒度 task 范围 | 状态 |
|----|------|------------------|------|
| T-1801 | 稳定化设计评审 | T-1800-01～10 | **done** |
| T-1802 | P0 smoke 三轮 + log | T-1801-01～17 · T-1802-01～03 | **done** |
| T-1803 | 模块契约 | T-1800-09 · T-1803-01～07 | **done** |
| T-1804 | IT：project 生命周期 | T-1803-01～03 | **done** |
| T-1805 | IT：project.switch | T-1803-04～05 | **done** |
| T-1806 | IT：confirm 回归 | T-1804-01～03 | **done** |
| T-1807 | 开放项 / Phase 14～17 闭环 | T-1808-bug-01～06 | **done** |
| T-1808 | CLI ↔ 桌面 parity | T-1808-01～05 | **done** |
| T-1809 | BUG P0/P1 清册 | T-1808-bug-04～05 | **done** |
| T-1810 | Gate runner | T-1807-01～04 · T-1806-01～03 | **done** |
| T-1811 | 计划门 UX | T-1801-07 · T-1821-01～02 | **done** |
| T-1812 | **放行评审** | T-1890-01～10 | **done**（T-1890-10 签字解冻） |
| T-1813 | 协议漂移审计 | T-1813-01～04 | **done** |
| T-1814 | daily/pet P0 | T-1801-12～14 | **done** |
| T-1815 | activity_router / ui.route | T-1804-06 · T-1821-13 | **done**（IT-09 扩测 → M2-I defer） |
| T-1816 | done 定义 + Phase 准入 | T-1800-07～08 | **done** |
| T-1817 | host_scope | T-1821-07～08 | **done**（IT-13 扩测 → M2-I defer） |
| T-1818 | grow evolve + checker | T-1820-02 · T-1821-03～06 | **done** |
| T-1819 | guards 纳入 Gate | T-1806-01～04 | **done** |
| T-1820 | 退出/重连/lock | T-1801-15 · T-1820-07 · T-1821-14 | **done**（IT-39 扩测 → M2-I defer） |
| T-1821 | **sidecar 日志落盘（P0）** | T-1805-01～07 | **done** |
| T-1822 | LLM/网络异常 | T-1801-16 · T-1821-15 · T-1806-05 | **done** |
| T-1823 | 数据韧性 | T-1823-01～06 | **done** |
| T-1824 | 平台/编码/隔离 | T-1824-01～06 | **done** |
| T-1825 | 资源与备份 DOC | T-1806-doc-06～07 | **done** |

> **T-1890-08（2026-07-18）**：上表与 [STABILIZATION-TASKS.md](./STABILIZATION-TASKS.md) 对齐；Epic 必做面全 **done**。M2-I（T-1830-01～12）整批 **defer**（见该文件 · backlog 索引戳）。

**执行顺序建议**：`T-1800-*` → `T-1805-*`（日志）→ `T-1803/04/06-*`（Gate 测试）→ `T-1807-*`（runner）→ `T-1801-*`（P0 第 1 轮）→ 修 fail → `T-1802-*`（第 2/3 轮）→ `T-1820/1821-*`（P1）→ `T-1890-*`（放行）。

**M0**：`STABILIZATION.md` v1.0 定稿 + DOC-03/04。  
**M1**：P0 三轮全 pass + Gate 绿 + sidecar 日志。  
**M2**：P1 闭环 + 数据/平台修复 + DOC-01～09。  
**M3**：T-1890-01～10 **全 done** · **已解冻**。

---

## Phase 19 — 上下文换线（Context Switch Gate）

> 设计：[CONTEXT-SWITCH.md](./CONTEXT-SWITCH.md) **v0.1.0**  
> 触发：口语「新项目 …」在旧项目会话写三件套；「项目 确认」确认错项目。  
> 产品选择：**LLM 识别换线意图** + **用户确认/拒绝**；执行走内核 API。

### DOC-04 准入（提案自检）

- [x] 影响矩阵行：见 CONTEXT-SWITCH §7.2（口语新建/拒绝不写盘/跨根写拒绝/元命令 session_replaced/跨壳 M1/soft route 不改 ownership）
- [x] 回归：BUG-020 ownership；project switch / shell switch；confirm 90s；S-08～S-09 类；新增 S-/IT- 于实现任务中编号

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-1901 | 设计落盘 + 关联文档指针 | `CONTEXT-SWITCH.md` · MAP/TASKS/PROJECT-MODE | Phase 18 解冻 | X1～X9 可读；P16 链到本文 | **done** |
| T-1902 | M0：`propose_context_switch` + WS 卡 + apply `project.create/switch` | `agent-core` · `desktop` · prompt | T-1901 | §7.1 M0；拒绝后不写目标目录 | **done** |
| T-1903 | M0：executor 门闩（无提案不得写非当前 project_root 立项路径） | `executor.py` · 测试 | T-1902 | IT 拒绝路径 | **done** |
| T-1904 | M0：`项目 新建` 已绑其他项目时强制 session_replaced | `project_cli` · `project_switch` | T-1901 | 生命周期单测 | **done** |
| T-1905 | M0：可选别名 `新项目 <id>` | `project_cli.parse` | T-1904 | 解析用例 | **done** |
| T-1906 | M1：`shell.switch` 换线确认 | 同 T-1902 扩 action · 全局 overlay | T-1902 | §7.1 M1 | **done** |
| T-1907 | M2：`session.new` 同壳新会话建议 | `context_switch` · prompt · 测试 | T-1906 | 确认后同壳空白会话；跨壳 target 拒绝 | **done** |

**M0 完成标志**：口语新建项目 → 确认卡 → 确认后顶栏/会话为目标项目；拒绝则仍旧项目且目标目录无继续写入。

**M1 完成标志**：口语跨壳（如 project→grow）→ 全局换线卡 → 确认后外壳与会话线切换；拒绝则留在当前壳。

**M2 完成标志**：口语「新话题/清一下」→ `session.new` 卡 → 确认后同壳新会话（project 保留绑定）；跨壳须先 shell.switch。

---

## Phase 20 — 项目 Task 一停门（Task Stop Gate）

> 设计：[TASK-STOP.md](./TASK-STOP.md) **v0.2.0**  
> 触发：project「开始编码」一轮内多 task + 确认/编译，撞 `TURN_WALL_SEC` 墙钟；用户误以为「不适合做大项目」。  
> 产品选择：**每个 `TASKS.md` 条目完成即停**；project 关 T-705 auto-continue；墙钟保留兜底。

### DOC-04 准入（提案自检）

- [x] 影响矩阵行：见 [TASK-STOP.md](./TASK-STOP.md) §6.1（project 执行面 · segment 续跑 · turn.end 文案 · 顶栏/侧栏 · CLI 继续语义）
- [x] 回归：计划门 / project.switch；grow 侧 IT-48 auto-continue **不变**；新增 **S-50/S-51** · **IT-51/IT-52**（见设计 §6.2）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-2001 | 设计落盘 + 关联文档指针 | `TASK-STOP.md` · MAP/TASKS/PROJECT-MODE/ORCHESTRATION | Phase 19 | S1～S9 可读；P20 链到本文 | **doc** |
| T-2002 | 评审签字（开放问题 Q1～Q4） | `TASK-STOP.md` → v0.2.0 | T-2001 | 已决摘要无「待签字」 | **done** |
| T-2003 | M0：`prompts/project.md` + `_template/TASKS.md` 粒度与一停纪律 | `evolve/prompts` · `workspace/_template` | T-2002 | 文案含「每 task 一停 / 继续」 | **done** |
| T-2004 | M0：project 壳关闭 auto-continue | `agent.py` · 测试 IT-51 | T-2002 | project 不自动下一段；grow 仍可 | **done** |
| T-2005 | M1：标 `[x]` 后同 turn 写下一产物硬拒 | `executor`/`agent` · IT-52 | T-2004 | 拒绝路径单测 | **done** |
| T-2006 | M1：一停文案 + 可选 `finish_reason=task_paused` | `loader` · desktop 状态栏 | T-2005 | S-50；非 timeout 文案 | **done** |
| T-2007 | M1：「继续」语义冒烟 | 桌面/CLI S-51 | T-2006 | 继续后开下一条未勾 | **done** |
| T-2008 | M2（可选）：侧栏/顶栏当前 task 高亮 +「继续下一项」按钮 | desktop project 壳 | T-2007 | 手工；可 defer | defer |

**M0 完成标志**：project 默认不自动续 segment；prompt 要求每 `[x]` 即停并提示「继续」。→ **done**（T-2003～T-2004；`python -m unittest tests.test_task_stop`）

**M1 完成标志**：硬门阻止同 turn 跨 task 写产物；用户「继续」开启下一项；停因可区分于墙钟 timeout。→ **done**（T-2005～T-2007；`python -m unittest tests.test_task_stop`）

**M2 完成标志**（可选）：UI 高亮当前 task + 一键继续。

---

### T-1002 手工验收（`host_sco
## Phase 21 — 项目进度闭环（report_progress 可达）

> 设计：[PROJECT-MODE.md](./PROJECT-MODE.md) **§0e** · [BUG-021](./bugs/2026-07-30-project-progress-deadlock.md)  
> **状态**：实现 **done**（2026-07-31；工作区 TASKS 曾空文件，以设计文档与测试为准）

| ID | 任务 | 状态 |
|----|------|------|
| T-2101～T-2107 | 清单并入 / draft 壳 / 一停武装 / project_id 注入等 | **done** |

---

## Phase 22 — 可见计划搭档

> 设计：[PROJECT-SIDEBAR.md](./PROJECT-SIDEBAR.md) **§15.10**  
> **状态**：**done**（T-2201～T-2207）

---

## Phase 23 — 工具目录 INDEX

> 设计：[TOOL-CATALOG.md](./TOOL-CATALOG.md)  
> **状态**：**done**（M0～M5 · Mp/Mq/Mr）

---

## Phase 24 — 进度硬闸门（Progress Gate）

> 设计：[PROGRESS-GATE.md](./PROGRESS-GATE.md) **v0.1.0**  
> 触发：huiyi T-014 后拒确认仍勾验收、同 turn 连勾、口头旧凭证。  
> 产品选择：**无本回合对口工具成功证据不可勾**；人只审规则/身份异常卡；工具失败走找 bug，无强制勾选。

### DOC-04 准入（提案自检）

- [x] 影响矩阵行：见 [PROGRESS-GATE.md](./PROGRESS-GATE.md) §5.1（执行门 / report_progress / 侧栏异常卡 / overlay；grow 不变）
- [x] 回归 ID：**S-70～S-74** · **IT-70～IT-73**（同文档 §5.2）；既有一停 / armed 身份回归 IT-73

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-2401 | 设计文档 + MAP/TASKS 挂钩 | `PROGRESS-GATE.md` v0.1.0 · 本表 | — | G0～G7 可勾选；DOC-04 齐全 | **done** |
| T-2402 | 本回合证据账本（executor） | `executor.py` turn_evidence | T-2401 | 工具 ok/失败可查询；跨 turn 清空 | **done** |
| T-2403 | 证据类分类纯函数 | `project_mode.py`（或邻接模块）+ IT-70 | T-2401 | 标题→write/compile/test/build_fe/verify_db/unknown | **done** |
| T-2404 | report_progress 证据门 | `report_progress` + 内核校验 + IT-71 | T-2402,T-2403 | 无对口本回合证据 → 不 toggle | **done** |
| T-2405 | 一停扩展：禁同 turn 再 report | task-stop + IT-72 | T-2404 | 第二次 report_progress 硬拒 | **done** |
| T-2406 | 异常卡（规则/身份）无强勾 | Plan/侧栏 notices | T-2404 | 仅规则冲突可人审；失败无强制勾入口 | todo |
| T-2407 | overlay / project.md 一句对齐 | loader · evolve/prompts | T-2404 | 文案含「无对口证据不可勾」 | todo |
| T-2408 | Smoke S-70～S-74 + 记录 | stabilization-log 或等价 | T-2405 | 四条场景 pass 留痕 | todo |

**完成标志**：G1～G5 硬门单测绿；huiyi 类「拒 mvn 仍勾测试」不可复现；同 turn 双 report 硬拒。

---

## Phase 25 — 托管长驻服务（run_service）

> 设计：[RUN-SERVICE.md](./RUN-SERVICE.md) **v0.1.0**  
> 触发：huiyi 助手无法用 `mvn_exec`/`npm_exec` 起 backend/frontend（超时杀进程）。

### DOC-04 准入（提案自检）

- [x] 影响矩阵行：STABILIZATION §3 **evolve 工具执行**面（新增 common 工具 + confirm 动作门）；不改桌面壳 / host / 计划门
- [x] 回归 ID：**IT-75**（生命周期）· **IT-76**（confirm 门）— `tests/test_run_service.py`

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-2501 | 设计文档 + MAP/TASKS | `RUN-SERVICE.md` · 本表 | — | DOC-04 齐全 | **done** |
| T-2502 | `run_service` evolved 工具 | `evolve/tools/common/run_service/` | T-2501 | start/stop/status/logs/wait；状态落 `data/services/` | **done** |
| T-2503 | confirm 动作门 | `executor._needs_confirm` | T-2502 | status/logs/wait/list 不 confirm；start/stop 要 | **done** |
| T-2504 | 目录 INDEX 挂钩 | `tool-catalog/buckets/run.md` | T-2502 | 表中可见 + 勿用 mvn 跑长驻 | **done** |
| T-2505 | IT-75 / IT-76 | `tests/test_run_service.py` | T-2502,T-2503 | 单测绿 | **done** |

**完成标志**：IT-75/76 绿；助手可用 `run_service` 起 spring-boot / npm dev 而不依赖 bat。

---

## Phase 26 — 项目开发工具补齐（HTTP / 启停收敛 / Git 写侧）

> 设计：[PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md) **v0.4.0**  
> 触发：写项目盘点——缺 HTTP 探活、端口治理、受控 commit；`dev_start` 与 `run_service` 重叠。  
> **纪律**：先文档后实现；**D1～D4 已决**；**M0+M1+M2 done**（Phase 26 完成）。

### DOC-04 准入（提案自检）

- [x] 影响矩阵行：见 [PROJECT-DEV-TOOLS.md](./PROJECT-DEV-TOOLS.md) §5.1（evolve 工具 / confirm；可选 Progress Gate；壳/host/计划门无）
- [x] 回归 ID：**IT-80～IT-86** · 可选 **S-80**（同文档 §5.2）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-2601 | 设计文档 + MAP/TASKS 挂钩 | `PROJECT-DEV-TOOLS.md` · 本表 | — | 缺口表 + D1～D4 + DOC-04 齐全 | **done** |
| T-2602 | 用户确认 D1～D4 | 文档 §2.2 已决 | T-2601 | 默认提案采纳 | **done** |
| T-2603 | M0：`http_request` | `evolve/tools/common/http_request/` + IT-80/81 | T-2602 | 契约符合 §3.1；测绿 | **done** |
| T-2604 | M0：`dev_start` 收敛 | 薄封装调 `run_service` + INDEX | T-2602 | IT-82；清单主路径为 `run_service` | **done** |
| T-2605 | M1：端口治理 | `run_service` · `port_status`/`kill_port` + IT-83 | T-2603 | kill_port confirm | **done** |
| T-2606 | M1：`git_commit` | `evolve/tools/coding/git_commit/` + IT-84 | T-2602 | 禁 force；confirm；dry_run | **done** |
| T-2607 | M0/M1 目录与提示挂钩 | `tool-catalog/buckets/run.md` 等 | T-2603,T-2604 | INDEX 可见、勿双荐 | **done** |
| T-2608 | M2：`db_query` + `pip_install` active | §3.5/§3.6 + IT-85/86 | T-2605,T-2606 | 只读默认；包名校验；测绿 | **done** |

**完成标志**：IT-80～86 绿；写项目主路径（起服 / 探活 / 清端口 / 提交 / SQLite / pip）齐备。

---

## Phase 27 — 执行可观测（聊天过程 + 侧栏服务/进度）

> 设计：[EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md) **v0.1.2** · **M0 done**  
> 触发：确认工具后只见「已执行」、无启动过程；侧栏看不见服务与本回合证据 → 排障黑盒。  
> 产品选择：**聊天过程与侧栏看板都要**。

### DOC-04 准入（提案自检）

- [x] 影响矩阵行：见 [EXEC-OBSERVABILITY.md](./EXEC-OBSERVABILITY.md) §6.1（桌面聊天 / 项目侧栏 / 可选 WS 事件；工具语义与硬门不变）
- [x] 回归 ID 预留：**IT-90～IT-93** · **S-90**（同文档 §6.2）

| ID | 任务 | 交付物 | 依赖 | 验收 | 状态 |
|----|------|--------|------|------|------|
| T-2701 | 设计文档 + MAP/TASKS | `EXEC-OBSERVABILITY.md` · 本表 | — | 双面目标 + DOC-04 齐全 | **done** |
| T-2702 | 用户确认 D1～D3 | 文档 §5.2 | T-2701 | 默认采纳或改写已决 | **done** |
| T-2703 | M0：聊天 RunningCard | `chat-state` / unified 确认流 | T-2702 | 同意后可见运行中+秒表；end 更新 | **done** |
| T-2704 | M0：确认文案去黑盒 | confirm 标签文案 | T-2703 | 「已同意，执行中…」≠ 单独「已执行」当成功 | **done** |
| T-2705 | M0：侧栏 Services | project-panel + list/刷新 | T-2702 | 可见登记服务 alive/端口 | **done** |
| T-2706 | M1：progress/services 事件 + 证据条 | server WS + 侧栏 Turn | T-2703,T-2705 | IT-91～93 | todo |
| T-2707 | 提示词/catalog 对齐 | evolve prompts · run.md | T-2703 | 禁 mvn 起长驻；指向可观测 | **done** |
| T-2708 | IT/S 留痕 | 测 + smoke 记录 | T-2705,T-2706 | IT-90～；S-90 | **done**（IT-90/92；IT-91/93→M1；S-90 手工） |

**完成标志（M0）**：确认后不再「静音」；侧栏能看见服务；失败有可读原因。 **已达成**。

---

### T-1002 手工验收（`host_scope.json` 加载与校验）

**环境**：`cd D:\my-agent\agent-core`（或你的 agent 根下 `agent-core/`）。

```powershell
python host_scope.py
```

**通过标准**（exit 0，且输出含下列 `[PASS]`）：

| # | 检查项 |
|---|--------|
| 1 | `missing host_scope.json -> empty host_roots` |
| 2 | `workspace under agent root rejected for host registration` |
| 3 | `reject host root inside agent tree` |
| 4 | `deny_glob blocks .ssh/id_rsa under host root` |
| 5 | `ordinary file not denied` |
| 6 | `parse_host_uri host:demoext/notes.txt` |
| 7 | `reject bare absolute path in parse_host_uri` |
| 8 | `save_host_scope + load roundtrip` |
| 9 | `system_deny blocks Windows system path`（非 Windows 可为 `[SKIP]`） |

**可选肉眼检查**：demo 跑完后 `data/host_scope.json` 应不存在（demo 会恢复）；若你手动创建该文件，应在 `.gitignore` 内且 `git status` 不跟踪。

---

### T-1003 手工验收（`resolve_under_host`）

**环境**：`cd D:\my-agent\agent-core`。

```powershell
python paths.py
```

**通过标准**（exit 0，含 `[PASS] T-1003:` 行）：

| # | 检查项 |
|---|--------|
| 1 | 已登记 `host:downloads/...` 可读文件 → resolve 成功 |
| 2 | `host:unknown/foo` → 拒绝（未知 id） |
| 3 | `host:downloads/../../outside` → `PathOutOfBoundsError` |
| 4 | `host:downloads/.ssh/id_rsa` → `HostPathDeniedError` / `path_denied` |
| 5 | 写模式 resolve 时目标 root `write:false` → 拒绝 |

> Demo 使用临时目录模拟 `downloads` root，**不依赖** `data/host_scope.json`。

---

### T-1004 手工验收（CLI 托管目录管理）

**自动化（推荐先跑）**：

```powershell
cd D:\my-agent\agent-core
python host_scope_cli.py
```

**通过标准**：exit 0，含 6 条 `[PASS] T-1004:`。

**交互（可选）**：

```powershell
python main.py
```

```
托管目录 列表
托管目录 添加 downloads <你的 Downloads 绝对路径> 只读
托管目录 列表
```

| # | 检查项 |
|---|--------|
| 1 | `列表` 显示 `downloads`、绝对路径、`只读` |
| 2 | `data/host_scope.json` 落盘 |
| 3 | `托管目录 添加 badid workspace 只读`（agent 内相对路径）→ **拒绝** |
| 4 | 重复 id → 拒绝 |
| 5 | `exit` 后重开，`列表` 仍可读（持久化） |
| 6 | `添加` / `删除` / `写 … 开` 均提示 confirm，`n` 则不改动 |

**命令摘要**：`托管目录 列表` · `托管目录 添加 <id> <路径> [只读\|读写]` · `托管目录 删除 <id>` · `托管目录 写 <id> 开|关`

### T-1005 手工验收（host 只读工具）

**前置**：`data/host_scope.json` 中已有只读托管区（可用 T-1004 添加 `downloads`）。

```powershell
cd D:\my-agent\agent-core
python host_tools.py
```

**通过标准**：exit 0，含 8 条 `[PASS] T-1005:`。

**交互（可选，需 LLM）**：

```powershell
python main.py
```

先 `托管目录 添加 downloads <Downloads路径> 只读`，再对话：

```
列出 host:downloads 里的文件
读取 host:downloads/<某文本文件>
在 host:downloads 搜索 hello
```

| # | 检查项 |
|---|--------|
| 1 | 上述操作 **不** 弹出 confirm |
| 2 | `host:other/foo` → 错误，不读盘 |
| 3 | `host:downloads/.env`（若存在）→ `path_denied` |
| 4 | `run_evolved` → `host_list` / `host_grep` 在 registry 中为 `confirm=false` |

### T-1006 手工验收（host 写 + confirm）

**前置**：`host_scope.json` 含 `downloads`（只读）与 `documents`（读写）；demo 自建临时目录。

```powershell
cd D:\my-agent\agent-core
python host_tools.py
```

**通过标准**：exit 0，含 8 条 `[PASS] T-1006:`（接在 T-1005 之后）。

| # | 检查项 |
|---|--------|
| 1 | 同 root `move` 成功；`host_root_id` 落日志字段 |
| 2 | 跨 root `copy`：`host_src_id` + `host_dst_id` |
| 3 | 写入只读 root → `permission_denied` |
| 4 | `dry_run` 不写盘 |
| 5 | confirm 预览含 `Source:` / `Dest:` 绝对路径；**无** `allow_all` |
| 6 | confirm `y` → `evolve_log` 含 `host_src_id` / `host_dst_id` |
| 7 | confirm `n` → 文件不变；无成功 `tool_call` |

### T-1007 手工验收（workflow 适配 host）

**环境**：T-1006 done；`workflow` 主题已确认。

```powershell
cd D:\my-agent\agent-core
python host_tools.py
```

自动化 demo 通过后，可选对话验收：

```powershell
cd D:\my-agent\agent-core
python main.py
```

在 Downloads 托管区准备若干不同后缀的测试文件，对话：

```
把 host:downloads 按扩展名整理到子文件夹（先 dry_run）
```

**通过标准**：

| # | 检查项 |
|---|--------|
| 1 | `dry_run=true` 返回计划，**不写盘** |
| 2 | 正式执行经 confirm |
| 3 | 整理后目录结构符合 `sort_by_extension` 语义 |
| 4 | `archive_by_date` 或 `rename_batch` 至少一个可在 `host:` 路径跑通 |
| 5 | `python host_tools.py` 含 `[PASS] T-1007:` |

---

### T-1008 手工验收（桌面托管区设置）

**环境**：`npm run dev` 起桌面壳；sidecar 正常。

**自动化（API）**：

```powershell
cd D:\my-agent\agent-core
python host_scope_api.py
```

应含 9 条 `[PASS] T-1008:`（含 `wizard entry write=true`、`host_scope.repath`）。

**桌面手工**：

```powershell
cd D:\my-agent\desktop
npm run dev
```

**通过标准**：

| # | 检查项 |
|---|--------|
| 1 | 顶栏 **托管区** → **添加文件夹…**（picker）→ 列表出现；`data/host_scope.json` 更新 |
| 2 | 添加 / **开启写** / 删除 → UI 内 **须确认**（S11）；关闭写可直接操作 |
| 3 | 首次 **wizard**：可勾选 **下载** + **桌面**；权限 **只读 / 读写**；读写时点「继续」再确认一次 |
| 4 | 已登记项可 **更换文件夹…**（`host_scope.repath`）→ 路径更新 |
| 5 | 只读项显示提示；整理文件前须 **开启写** |
| 6 | 对话「列出 host:downloads」走 T-1005 `host_list`，grow 过程块可见 |
| 7 | 与 CLI `托管目录 列表` 数据一致 |
| 8 | `python host_scope_api.py` exit 0 |

**WS 消息**（见 [HOST-SCOPE.md](./HOST-SCOPE.md) §6.4、[DESKTOP.md](./DESKTOP.md) §5.1）：`host_scope.list` · `.add` · `.remove` · `.write` · `.repath` · `.wizard`。

---

## 依赖总图（简）

```mermaid
flowchart TD
  T004[TOOLS.md 评审]
  T101[schema + paths]
  T108[executor + builtin]
  T112[CLI tool run]
  T203[session + 主循环]
  T305[记忆三件套 M1c]
  T402[proposal memory/tool]
  T502[真实 evolved tool]
  T604[skill 可选]

  T004 --> T101 --> T108 --> T112
  T112 --> T203
  T203 --> T305
  T203 --> T402
  T203 --> T502
  T502 --> T604
```

---

## 建议你现在审什么

1. **`RUNTIME.md`** — 续接、DeepSeek、digest 是否认可  
2. **`TOOLS.md` v0.2** — 6 Builtin、主题 tools  
3. **Phase 1～2** — task 是否够细  

审完标 T-004、T-005 为 `done`，再动 T-101 写代码。

---

## 与 PROJECT.md 里程碑对照

| PROJECT 里程碑 | 本清单 |
|----------------|--------|
| M1（原：含 skill） | **拆为** M1a Phase1 + M1b Phase2 + M1c Phase3（均无 skill） |
| M2 | Phase 4 |
| M3 | Phase 5 |
| M4 | Phase 6 |

建议在 `PROJECT.md` §7.3 引用本文件为 **实施细表**。
